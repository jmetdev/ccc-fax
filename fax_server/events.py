from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from urllib.parse import unquote_plus

from .config import Settings
from .db import connect
from .esl import EventSocketClient


LOGGER = logging.getLogger(__name__)
EVENT_NAMES = ("CHANNEL_HANGUP_COMPLETE", "CUSTOM")
FAX_SUBCLASSES = {"spandsp::txfaxresult", "spandsp::rxfaxresult"}


def start_fax_event_listener(settings: Settings) -> None:
    """Start one background ESL listener per Flask process."""
    if getattr(start_fax_event_listener, "_started", False):
        return
    start_fax_event_listener._started = True
    thread = threading.Thread(target=_run_forever, args=(settings,), name="fax-event-listener", daemon=True)
    thread.start()


def _run_forever(settings: Settings) -> None:
    while True:
        try:
            client = EventSocketClient(
                settings.freeswitch_host,
                settings.freeswitch_event_socket_port,
                settings.freeswitch_event_socket_password,
                timeout=30.0,
            )
            for raw_event in client.events(EVENT_NAMES):
                _handle_event(settings, raw_event)
        except Exception:
            LOGGER.exception("FreeSWITCH fax event listener disconnected")
            time.sleep(5)


def _handle_event(settings: Settings, raw_event: str) -> None:
    event = _parse_event(raw_event)
    event_name = event.get("Event-Name", "")
    subclass = event.get("Event-Subclass", "")
    if event_name == "CUSTOM" and subclass not in FAX_SUBCLASSES:
        return

    call_uuid = _first_present(
        event,
        "Unique-ID",
        "Channel-Call-UUID",
        "variable_uuid",
        "variable_origination_uuid",
        "Application-UUID",
    )
    if not call_uuid:
        return

    with connect(settings.database) as conn:
        row = conn.execute(
            "SELECT id, direction, status FROM fax_jobs WHERE freeswitch_uuid = ?",
            (call_uuid,),
        ).fetchone()
        if row is None and subclass == "spandsp::rxfaxresult":
            _create_inbound_job(settings, conn, event, call_uuid)
            return
        if row is None or row["status"] not in {"queued", "sending", "receiving"}:
            return
        if row["direction"] == "outbound":
            status, error = _outbound_result(event)
        else:
            status, error = _inbound_result(event)
        if status is None:
            return
        conn.execute(
            "UPDATE fax_jobs SET status = ?, error = ? WHERE id = ?",
            (status, error, row["id"]),
        )


def _create_inbound_job(settings: Settings, conn, event: dict[str, str], call_uuid: str) -> None:
    status, error = _inbound_result(event)
    if status is None:
        return

    webex_line_id = _first_present(event, "variable_destination_number", "Caller-Destination-Number")
    from_number = _first_present(event, "variable_caller_id_number", "Caller-Caller-ID-Number", "Caller-ANI")
    route = _find_inbound_route(conn, webex_line_id)
    to_number = route["did_number"] if route is not None else webex_line_id
    source_path = _host_path_for_freeswitch(
        settings,
        _first_present(event, "variable_fax_file", "Fax-File", "fax_file"),
    )

    conn.execute(
        """
        INSERT INTO fax_jobs(
            direction,
            status,
            to_number,
            from_number,
            webex_line_id,
            inbound_route_id,
            source_path,
            tiff_path,
            freeswitch_uuid,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inbound",
            status if route is not None else "unrouted",
            to_number,
            from_number,
            webex_line_id,
            route["id"] if route is not None else None,
            str(source_path) if source_path else None,
            str(source_path) if source_path else None,
            call_uuid,
            error,
        ),
    )


def _outbound_result(event: dict[str, str]) -> tuple[str | None, str | None]:
    success = _first_present(event, "variable_fax_success", "Fax-Success", "fax_success")
    result_text = _fax_result_text(event)
    if _truthy(success):
        return "sent", None
    if success is not None and not _truthy(success):
        return "send_failed", result_text or "FreeSWITCH reported fax failure"
    if event.get("Event-Subclass") == "spandsp::txfaxresult" and "successfully sent" in result_text.lower():
        return "sent", None
    return None, None


def _inbound_result(event: dict[str, str]) -> tuple[str | None, str | None]:
    success = _first_present(event, "variable_fax_success", "Fax-Success", "fax_success")
    result_text = _fax_result_text(event)
    if _truthy(success):
        return "received", None
    if success is not None and not _truthy(success):
        return "receive_failed", result_text or "FreeSWITCH reported fax receive failure"
    return None, None


def _fax_result_text(event: dict[str, str]) -> str:
    return _first_present(
        event,
        "variable_fax_result_text",
        "variable_fax_result",
        "Fax-Result-Text",
        "Fax-Result",
        "fax_result_text",
        "Reply-Text",
    ) or ""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "yes", "true", "success"}


def _first_present(event: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = event.get(key)
        if value:
            return value
    return None


def _find_inbound_route(conn, webex_line_id: str | None):
    if not webex_line_id:
        return None
    row = conn.execute(
        """
        SELECT * FROM inbound_routes
        WHERE enabled = 1 AND (webex_line_id = ? OR did_number = ? OR extension = ?)
        """,
        (webex_line_id, webex_line_id, webex_line_id),
    ).fetchone()
    return row


def _host_path_for_freeswitch(settings: Settings, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    try:
        relative = path.relative_to(settings.freeswitch_storage)
    except ValueError:
        return path
    return settings.storage / relative


def _parse_event(raw_event: str) -> dict[str, str]:
    headers, _, body = raw_event.partition("\n\n")
    event = _parse_headers(headers)
    if body.startswith("Event-Name:") or "\nEvent-Name:" in body:
        event.update(_parse_headers(body))
    return event


def _parse_headers(text: str) -> dict[str, str]:
    event: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        event[key.strip()] = unquote_plus(value.strip())
    return event

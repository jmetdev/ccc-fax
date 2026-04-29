from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

from .config import Settings
from .esl import EventSocketClient, EventSocketError


NUMBER_RE = re.compile(r"^\+?[0-9]{7,20}$")


def clean_number(number: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", number)
    if not NUMBER_RE.match(cleaned):
        raise ValueError("number must contain 7-20 digits, optionally prefixed with +")
    if cleaned.startswith("+"):
        return cleaned
    if len(cleaned) == 10:
        return f"+1{cleaned}"
    if len(cleaned) == 11 and cleaned.startswith("1"):
        return f"+{cleaned}"
    return cleaned


def originate_fax(settings: Settings, to_number: str, tiff_path: Path, from_number: str | None = None) -> tuple[str, str]:
    if not tiff_path.exists():
        raise FileNotFoundError(f"fax TIFF does not exist: {tiff_path}")

    sofia_loaded = freeswitch_api(settings, "module_exists mod_sofia").strip().lower()
    if sofia_loaded != "true":
        raise RuntimeError("FreeSWITCH mod_sofia is not loaded; load SIP configuration before sending")

    call_uuid = str(uuid.uuid4())
    destination = clean_number(to_number)
    variables = {
        "origination_uuid": call_uuid,
        "fax_enable_t38": "true",
        "fax_enable_t38_request": "true",
        "ignore_early_media": "true",
        "originate_timeout": "90",
        "rtp_secure_media": "true",
        "sip_invite_params": "user=phone",
    }
    from_user = from_number or settings.freeswitch_outbound_from_user or settings.freeswitch_caller_id_number
    if from_user:
        variables["origination_caller_id_number"] = from_user
    if settings.freeswitch_caller_id_number:
        variables["origination_caller_id_name"] = settings.freeswitch_caller_id_number
        variables["sip_cid_type"] = "pid"

    var_string = ",".join(f"{key}={value}" for key, value in variables.items())
    freeswitch_tiff_path = path_for_freeswitch(settings, tiff_path)
    originate = (
        f"originate {{{var_string}}}"
        f"sofia/gateway/{settings.freeswitch_gateway}/{destination} "
        f"&txfax({freeswitch_tiff_path})"
    )
    response = freeswitch_api(settings, f"bgapi {originate}")
    return call_uuid, response


def path_for_freeswitch(settings: Settings, host_path: Path) -> Path:
    resolved = host_path.resolve()
    storage = settings.storage.resolve()
    try:
        relative = resolved.relative_to(storage)
    except ValueError:
        return host_path
    return settings.freeswitch_storage / relative


def freeswitch_api(settings: Settings, command: str) -> str:
    try:
        response = EventSocketClient(
            settings.freeswitch_host,
            settings.freeswitch_event_socket_port,
            settings.freeswitch_event_socket_password,
        ).api(command)
        return _event_socket_body(response)
    except Exception as esl_error:
        if not settings.freeswitch_cli_container:
            raise
        result = subprocess.run(
            ["docker", "exec", settings.freeswitch_cli_container, "fs_cli", "-x", command],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise EventSocketError(
                f"ESL failed ({esl_error}); fs_cli fallback failed: {result.stderr.strip()}"
            ) from esl_error
        return result.stdout


def _event_socket_body(response: str) -> str:
    if "\n\n" in response:
        return response.split("\n\n", 1)[1].strip()
    return response.strip()

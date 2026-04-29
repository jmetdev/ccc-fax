from __future__ import annotations

import shutil
from pathlib import Path
from sqlite3 import IntegrityError

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from .config import Settings
from .db import connect, init_db, row_to_dict
from .dialplan import render_inbound_dialplan
from .events import start_fax_event_listener
from .freeswitch import clean_number, freeswitch_api, originate_fax
from .gateways import render_line_gateways
from .processing import normalize_to_tiff
from .webex import WebexAPIError, WebexClient, gateway_members_from_mac, provisioning_source_from_webex


def create_app() -> Flask:
    load_dotenv()
    settings = Settings.from_env()
    init_db(settings.database)
    for folder in ("uploads", "faxes/outgoing", "faxes/incoming"):
        (settings.storage / folder).mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    start_fax_event_listener(settings)

    @app.get("/")
    def index():
        with connect(settings.database) as conn:
            jobs = [row_to_dict(row) for row in conn.execute(_fax_jobs_query("ORDER BY fax_jobs.id DESC LIMIT 100"))]
            routes = [
                row_to_dict(row)
                for row in conn.execute("SELECT * FROM inbound_routes ORDER BY display_name, did_number")
            ]
            destination_settings = _get_destination_settings(conn)
            stats = _job_stats(conn)
        config = _config_overview(settings)
        conversion = _conversion_overview(settings)
        return render_template(
            "index.html",
            jobs=jobs,
            routes=routes,
            stats=stats,
            gateway=settings.freeswitch_gateway,
            config=config,
            conversion=conversion,
            destination_settings=destination_settings,
        )

    @app.get("/api/health")
    def health():
        fs_status = "unreachable"
        fs_response = ""
        try:
            fs_response = freeswitch_api(settings, "status")
            fs_status = "ok"
        except Exception as exc:  # noqa: BLE001 - surfaced as health detail
            fs_response = str(exc)
        return jsonify(
            {
                "app": "ok",
                "database": str(settings.database),
                "storage": str(settings.storage),
                "freeswitch": fs_status,
                "freeswitch_response": fs_response,
            }
        )

    @app.get("/api/faxes")
    def list_faxes():
        with connect(settings.database) as conn:
            rows = conn.execute(_fax_jobs_query("ORDER BY fax_jobs.id DESC LIMIT 100")).fetchall()
        return jsonify([row_to_dict(row) for row in rows])

    @app.get("/api/inbound-routes")
    def list_inbound_routes():
        with connect(settings.database) as conn:
            rows = conn.execute("SELECT * FROM inbound_routes ORDER BY display_name, did_number").fetchall()
        return jsonify([row_to_dict(row) for row in rows])

    @app.get("/api/destination-settings")
    def get_destination_settings():
        with connect(settings.database) as conn:
            return jsonify(_get_destination_settings(conn))

    @app.put("/api/destination-settings")
    def update_destination_settings():
        payload = request.get_json(silent=True) or request.form.to_dict()
        settings_payload = _destination_settings_payload(payload)
        with connect(settings.database) as conn:
            _upsert_destination_settings(conn, settings_payload)
            return jsonify(_get_destination_settings(conn))

    @app.post("/api/inbound-routes")
    def create_inbound_route():
        payload = request.get_json(silent=True) or request.form.to_dict()
        route, error = _route_payload(payload)
        if error:
            return jsonify({"error": error}), 400

        try:
            with connect(settings.database) as conn:
                route_id = _insert_inbound_route(conn, route)
                _render_provisioning_files(settings, conn)
        except IntegrityError as exc:
            return jsonify({"error": _integrity_error_message(exc)}), 409
        return jsonify(_get_inbound_route(settings.database, route_id)), 201

    @app.get("/api/inbound-routes/<int:route_id>")
    def get_inbound_route(route_id: int):
        route = _get_inbound_route(settings.database, route_id)
        if route is None:
            return jsonify({"error": "inbound route not found"}), 404
        return jsonify(route)

    @app.put("/api/inbound-routes/<int:route_id>")
    def update_inbound_route(route_id: int):
        if _get_inbound_route(settings.database, route_id) is None:
            return jsonify({"error": "inbound route not found"}), 404

        payload = request.get_json(silent=True) or request.form.to_dict()
        route, error = _route_payload(payload)
        if error:
            return jsonify({"error": error}), 400

        try:
            with connect(settings.database) as conn:
                _update_inbound_route(conn, route_id, route)
                _render_provisioning_files(settings, conn)
        except IntegrityError as exc:
            return jsonify({"error": _integrity_error_message(exc)}), 409
        return jsonify(_get_inbound_route(settings.database, route_id))

    @app.delete("/api/inbound-routes/<int:route_id>")
    def delete_inbound_route(route_id: int):
        with connect(settings.database) as conn:
            cursor = conn.execute("DELETE FROM inbound_routes WHERE id = ?", (route_id,))
            if cursor.rowcount:
                _render_provisioning_files(settings, conn)
        if cursor.rowcount == 0:
            return jsonify({"error": "inbound route not found"}), 404
        return "", 204

    @app.post("/api/webex/provision")
    def provision_from_webex():
        payload = request.get_json(silent=True) or request.form.to_dict()
        workspace_ref = _optional_str(payload.get("workspace") or payload.get("workspace_url") or payload.get("workspace_id"))
        gateway_ref = _optional_str(payload.get("gateway") or payload.get("gateway_url") or payload.get("gateway_id"))
        if not workspace_ref:
            return jsonify({"error": "workspace_url or workspace_id is required"}), 400
        if not gateway_ref:
            return jsonify({"error": "gateway_url or gateway_id is required"}), 400

        try:
            client = WebexClient(
                settings.webex_access_token,
                api_base=settings.webex_api_base,
                org_id=_optional_str(payload.get("org_id")) or settings.webex_org_id,
            )
            source = provisioning_source_from_webex(
                client,
                workspace_ref=workspace_ref,
                gateway_ref=gateway_ref,
            )
            route = _route_payload(
                {
                    "webex_line_id": source.line_id,
                    "webex_workspace_id": source.workspace_id,
                    "webex_gateway_id": source.gateway_id,
                    "did_number": source.phone_number,
                    "extension": source.extension,
                    "display_name": source.display_name,
                    "destination_type": payload.get("destination_type", "local"),
                    "destination_value": payload.get("destination_value"),
                    "enabled": payload.get("enabled", True),
                    "notes": payload.get("notes") or "Provisioned from Webex",
                }
            )[0]
            if route is None:
                return jsonify({"error": "Webex payload did not contain route data"}), 400
            with connect(settings.database) as conn:
                route_id = _upsert_inbound_route(conn, route)
                rendered = _render_provisioning_files(settings, conn)
                reload_result = _reload_freeswitch(settings)
        except (ValueError, WebexAPIError) as exc:
            return jsonify({"error": str(exc)}), 502
        except IntegrityError as exc:
            return jsonify({"error": _integrity_error_message(exc)}), 409

        return jsonify(
            {
                "route": _get_inbound_route(settings.database, route_id),
                "workspace": source.workspace,
                "gateway": source.gateway,
                "dialplan": str(settings.freeswitch_inbound_dialplan),
                "gateways": rendered["gateways"],
                "reload": reload_result,
            }
        )

    @app.post("/api/webex/provision-gateway")
    def provision_gateway_from_webex():
        payload = request.get_json(silent=True) or request.form.to_dict()
        mac = _optional_str(payload.get("mac") or payload.get("mac_address"))
        if not mac:
            return jsonify({"error": "mac is required"}), 400

        try:
            client = WebexClient(
                settings.webex_access_token,
                api_base=settings.webex_api_base,
                org_id=_optional_str(payload.get("org_id")) or settings.webex_org_id,
            )
            members = gateway_members_from_mac(client, mac=mac)
            routes = []
            with connect(settings.database) as conn:
                for member in members:
                    route, error = _route_payload(
                        {
                            "webex_line_id": member.line_id,
                            "webex_gateway_id": member.gateway_id,
                            "did_number": member.phone_number,
                            "extension": member.extension,
                            "display_name": member.display_name,
                            "destination_type": payload.get("destination_type", "local"),
                            "destination_value": payload.get("destination_value"),
                            "enabled": payload.get("enabled", True),
                            "notes": payload.get("notes") or f"Provisioned from Webex gateway MAC {mac}",
                        }
                    )
                    if error:
                        raise WebexAPIError(error)
                    route_id = _upsert_inbound_route(conn, route)
                    routes.append(route_id)
                rendered = _render_provisioning_files(settings, conn)
                reload_result = _reload_freeswitch(settings)
        except (ValueError, WebexAPIError) as exc:
            return jsonify({"error": str(exc)}), 502
        except IntegrityError as exc:
            return jsonify({"error": _integrity_error_message(exc)}), 409

        return jsonify(
            {
                "gateway": members[0].gateway,
                "members": [member.member for member in members],
                "routes": [_get_inbound_route(settings.database, route_id) for route_id in routes],
                "dialplan": rendered["dialplan"],
                "gateways": rendered["gateways"],
                "reload": reload_result,
            }
        )

    @app.get("/api/monitor")
    def monitor():
        freeswitch = {
            "status": "unreachable",
            "gateway": settings.freeswitch_gateway,
            "calls": "",
            "gateway_status": "",
            "error": None,
        }
        webex = {
            "status": "not_configured" if not settings.webex_access_token else "configured",
            "api_base": settings.webex_api_base,
            "org_id": settings.webex_org_id,
        }
        try:
            freeswitch["calls"] = _fs_response_body(freeswitch_api(settings, "show calls"))
            freeswitch["gateway_status"] = _fs_response_body(
                freeswitch_api(settings, f"sofia status gateway {settings.freeswitch_gateway}")
            )
            freeswitch["status"] = "ok"
        except Exception as exc:  # noqa: BLE001 - operator-facing monitor detail
            freeswitch["error"] = str(exc)

        with connect(settings.database) as conn:
            rows = conn.execute(_fax_jobs_query("ORDER BY fax_jobs.id DESC LIMIT 25")).fetchall()
            stats = _job_stats(conn)
            routes = conn.execute("SELECT * FROM inbound_routes ORDER BY display_name, did_number").fetchall()
            destination_settings = _get_destination_settings(conn)
        return jsonify(
            {
                "freeswitch": freeswitch,
                "webex": webex,
                "stats": stats,
                "config": _config_overview(settings),
                "conversion": _conversion_overview(settings),
                "destination_settings": destination_settings,
                "jobs": [row_to_dict(row) for row in rows],
                "inbound_routes": [row_to_dict(row) for row in routes],
            }
        )

    @app.get("/api/faxes/<int:job_id>")
    def get_fax(job_id: int):
        job = _get_job(settings.database, job_id)
        if job is None:
            return jsonify({"error": "fax job not found"}), 404
        return jsonify(job)

    @app.post("/api/faxes")
    def create_fax():
        document = request.files.get("document")
        payload = request.get_json(silent=True) or {}
        to_number = request.form.get("to_number") or payload.get("to_number")
        from_number = request.form.get("from_number") or payload.get("from_number")
        if not to_number:
            return jsonify({"error": "to_number is required"}), 400
        try:
            to_number = clean_number(to_number)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if from_number:
            try:
                from_number = clean_number(from_number)
            except ValueError as exc:
                return jsonify({"error": f"from_number {exc}"}), 400

        if document is None:
            return jsonify({"error": "document upload is required"}), 400

        with connect(settings.database) as conn:
            cursor = conn.execute(
                "INSERT INTO fax_jobs(direction, status, to_number, from_number) VALUES (?, ?, ?, ?)",
                ("outbound", "created", to_number, from_number),
            )
            job_id = cursor.lastrowid

        source = settings.storage / "uploads" / f"{job_id}_{Path(document.filename or 'upload').name}"
        tiff_path = settings.storage / "faxes" / "outgoing" / f"{job_id}.tiff"
        document.save(source)

        try:
            normalize_to_tiff(source, tiff_path, settings.convert_bin, settings.office_convert_bin)
            status = "ready"
            error = None
        except Exception as exc:  # noqa: BLE001 - stored for operator visibility
            status = "conversion_failed"
            error = str(exc)

        with connect(settings.database) as conn:
            conn.execute(
                "UPDATE fax_jobs SET status = ?, source_path = ?, tiff_path = ?, error = ? WHERE id = ?",
                (status, str(source), str(tiff_path), error, job_id),
            )

        payload = _get_job(settings.database, job_id)
        return jsonify(payload), 201

    @app.post("/api/faxes/<int:job_id>/send")
    def send_fax(job_id: int):
        job = _get_job(settings.database, job_id)
        if job is None:
            return jsonify({"error": "fax job not found"}), 404
        if job["status"] not in {"ready", "send_failed"}:
            return jsonify({"error": f"fax job is not ready to send; current status is {job['status']}"}), 409

        try:
            call_uuid, response = originate_fax(
                settings,
                job["to_number"],
                Path(job["tiff_path"]),
                job.get("from_number"),
            )
            status = "queued"
            error = None
        except Exception as exc:  # noqa: BLE001 - stored for operator visibility
            call_uuid = None
            response = ""
            status = "send_failed"
            error = str(exc)

        with connect(settings.database) as conn:
            conn.execute(
                """
                UPDATE fax_jobs
                SET status = ?, freeswitch_uuid = ?, freeswitch_response = ?, error = ?
                WHERE id = ?
                """,
                (status, call_uuid, response, error, job_id),
            )
        return jsonify(_get_job(settings.database, job_id))

    @app.post("/faxes")
    def create_fax_from_ui():
        response, status = create_fax()
        if status >= 400:
            return response, status
        if request.form.get("send_now") == "on":
            job = response.get_json(silent=True) or {}
            job_id = job.get("id")
            if job_id:
                send_fax(job_id)
        return redirect(url_for("index"))

    @app.post("/faxes/<int:job_id>/send")
    def send_fax_from_ui(job_id: int):
        send_fax(job_id)
        return redirect(url_for("index"))

    @app.post("/api/inbound")
    def register_inbound():
        payload = request.get_json(silent=True) or {}
        source = payload.get("path")
        from_number = payload.get("from_number")
        to_number = payload.get("to_number")
        webex_line_id = _optional_str(payload.get("webex_line_id") or payload.get("line_id") or payload.get("lineID"))
        if not source:
            return jsonify({"error": "path is required"}), 400

        source_path = Path(source)
        destination = settings.storage / "faxes" / "incoming" / source_path.name
        if source_path.exists():
            shutil.copy2(source_path, destination)

        with connect(settings.database) as conn:
            route = _find_inbound_route(conn, webex_line_id=webex_line_id, to_number=to_number)
            if route is not None:
                webex_line_id = webex_line_id or route["webex_line_id"]
                to_number = to_number or route["did_number"] or route["extension"]
            cursor = conn.execute(
                """
                INSERT INTO fax_jobs(
                    direction,
                    status,
                    to_number,
                    from_number,
                    webex_line_id,
                    inbound_route_id,
                    source_path,
                    tiff_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "inbound",
                    "received" if route is not None else "unrouted",
                    to_number,
                    from_number,
                    webex_line_id,
                    route["id"] if route is not None else None,
                    str(source_path),
                    str(destination),
                ),
            )
            job_id = cursor.lastrowid
        return jsonify(_get_job(settings.database, job_id)), 201

    @app.get("/faxes/<int:job_id>/file")
    def download_fax_file(job_id: int):
        job = _get_job(settings.database, job_id)
        if job is None:
            return jsonify({"error": "fax job not found"}), 404
        tiff_path = job.get("tiff_path")
        if not tiff_path or not Path(tiff_path).exists():
            return jsonify({"error": "fax file not found"}), 404
        return send_file(tiff_path, as_attachment=True)

    return app


def _get_job(database: Path, job_id: int) -> dict | None:
    with connect(database) as conn:
        row = conn.execute(_fax_jobs_query("WHERE fax_jobs.id = ?"), (job_id,)).fetchone()
    return row_to_dict(row)


def _get_inbound_route(database: Path, route_id: int) -> dict | None:
    with connect(database) as conn:
        row = conn.execute("SELECT * FROM inbound_routes WHERE id = ?", (route_id,)).fetchone()
    return row_to_dict(row)


def _get_destination_settings(conn) -> dict:
    row = conn.execute("SELECT * FROM destination_settings WHERE id = 1").fetchone()
    if row is not None:
        return row_to_dict(row)
    conn.execute("INSERT INTO destination_settings(id) VALUES (1)")
    row = conn.execute("SELECT * FROM destination_settings WHERE id = 1").fetchone()
    return row_to_dict(row)


def _upsert_destination_settings(conn, payload: dict) -> None:
    _get_destination_settings(conn)
    conn.execute(
        """
        UPDATE destination_settings
        SET smtp_enabled = ?,
            smtp_host = ?,
            smtp_port = ?,
            smtp_username = ?,
            smtp_password = ?,
            smtp_from_address = ?,
            smtp_use_tls = ?,
            webex_bot_enabled = ?,
            webex_bot_token = ?,
            webex_room_id = ?,
            teams_bot_enabled = ?,
            teams_webhook_url = ?
        WHERE id = 1
        """,
        (
            payload["smtp_enabled"],
            payload["smtp_host"],
            payload["smtp_port"],
            payload["smtp_username"],
            payload["smtp_password"],
            payload["smtp_from_address"],
            payload["smtp_use_tls"],
            payload["webex_bot_enabled"],
            payload["webex_bot_token"],
            payload["webex_room_id"],
            payload["teams_bot_enabled"],
            payload["teams_webhook_url"],
        ),
    )


def _insert_inbound_route(conn, route: dict) -> int:
    cursor = conn.execute(
        """
        INSERT INTO inbound_routes(
            webex_line_id,
            webex_workspace_id,
            webex_gateway_id,
            did_number,
            extension,
            display_name,
            destination_type,
            destination_value,
            enabled,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _route_values(route),
    )
    return cursor.lastrowid


def _update_inbound_route(conn, route_id: int, route: dict) -> None:
    conn.execute(
        """
        UPDATE inbound_routes
        SET webex_line_id = ?,
            webex_workspace_id = ?,
            webex_gateway_id = ?,
            did_number = ?,
            extension = ?,
            display_name = ?,
            destination_type = ?,
            destination_value = ?,
            enabled = ?,
            notes = ?
        WHERE id = ?
        """,
        (*_route_values(route), route_id),
    )


def _upsert_inbound_route(conn, route: dict) -> int:
    existing = conn.execute(
        """
        SELECT id FROM inbound_routes
        WHERE webex_line_id = ? OR did_number = ? OR (extension IS NOT NULL AND extension = ?)
        ORDER BY
            CASE
                WHEN webex_line_id = ? THEN 0
                WHEN did_number = ? THEN 1
                ELSE 2
            END
        LIMIT 1
        """,
        (
            route["webex_line_id"],
            route["did_number"],
            route["extension"],
            route["webex_line_id"],
            route["did_number"],
        ),
    ).fetchone()
    if existing is None:
        return _insert_inbound_route(conn, route)
    _update_inbound_route(conn, existing["id"], route)
    return existing["id"]


def _route_values(route: dict) -> tuple:
    return (
        route["webex_line_id"],
        route.get("webex_workspace_id"),
        route.get("webex_gateway_id"),
        route["did_number"],
        route["extension"],
        route["display_name"],
        route["destination_type"],
        route["destination_value"],
        route["enabled"],
        route["notes"],
    )


def _render_provisioning_files(settings: Settings, conn) -> dict:
    routes = [row_to_dict(row) for row in conn.execute("SELECT * FROM inbound_routes ORDER BY display_name, did_number")]
    render_inbound_dialplan(routes, settings.freeswitch_inbound_dialplan, settings.freeswitch_storage)
    gateways = render_line_gateways(routes, settings.freeswitch_provider_dir, settings.freeswitch_parent_gateway)
    return {"dialplan": str(settings.freeswitch_inbound_dialplan), "gateways": gateways}


def _reload_freeswitch(settings: Settings) -> dict:
    if not settings.freeswitch_reload_on_provision:
        return {"attempted": False}
    try:
        reload_response = _fs_response_body(freeswitch_api(settings, "reloadxml"))
        profile_response = _fs_response_body(
            freeswitch_api(settings, f"sofia profile {settings.freeswitch_profile} rescan")
        )
        return {
            "attempted": True,
            "ok": True,
            "reloadxml": reload_response,
            "profile_rescan": profile_response,
        }
    except Exception as exc:  # noqa: BLE001 - surfaced to operator
        return {"attempted": True, "ok": False, "error": str(exc)}


def _fax_jobs_query(suffix: str) -> str:
    return f"""
        SELECT
            fax_jobs.*,
            inbound_routes.display_name AS inbound_route_name,
            inbound_routes.destination_type AS inbound_destination_type,
            inbound_routes.destination_value AS inbound_destination_value
        FROM fax_jobs
        LEFT JOIN inbound_routes ON inbound_routes.id = fax_jobs.inbound_route_id
        {suffix}
    """


def _job_stats(conn) -> dict:
    rows = conn.execute(
        """
        SELECT
            direction,
            status,
            COUNT(*) AS count
        FROM fax_jobs
        GROUP BY direction, status
        """
    ).fetchall()
    stats = {
        "total": 0,
        "outbound": 0,
        "inbound": 0,
        "ready": 0,
        "queued": 0,
        "in_progress": 0,
        "recent_failures": 0,
        "received": 0,
        "unrouted": 0,
        "failed": 0,
    }
    for row in rows:
        count = row["count"]
        direction = row["direction"]
        status = row["status"]
        stats["total"] += count
        if direction in {"outbound", "inbound"}:
            stats[direction] += count
        if status in stats:
            stats[status] += count
        if status in {"queued", "sending"}:
            stats["in_progress"] += count
        if "failed" in status:
            stats["failed"] += count
            stats["recent_failures"] += count
    return stats


def _config_overview(settings: Settings) -> dict:
    return {
        "profile": settings.freeswitch_profile,
        "gateway": settings.freeswitch_gateway,
        "provider_dir": str(settings.freeswitch_provider_dir),
        "dialplan": str(settings.freeswitch_inbound_dialplan),
        "storage": str(settings.storage),
        "event_socket": f"{settings.freeswitch_host}:{settings.freeswitch_event_socket_port}",
        "reload_on_provision": settings.freeswitch_reload_on_provision,
        "license_level": "Internal admin",
        "webex_api_base": settings.webex_api_base,
        "webex_org_id": settings.webex_org_id,
    }


def _conversion_overview(settings: Settings) -> dict:
    return {
        "imagemagick": shutil.which(settings.convert_bin) or settings.convert_bin,
        "office": shutil.which(settings.office_convert_bin) or "",
        "office_available": bool(shutil.which(settings.office_convert_bin)),
        "accepted_formats": "PDF, TIFF, PNG, JPG, MS Office, OpenDocument, RTF",
    }


def _route_payload(payload: dict) -> tuple[dict | None, str | None]:
    webex_line_id = _optional_str(payload.get("webex_line_id") or payload.get("line_id") or payload.get("lineID"))
    webex_workspace_id = _optional_str(payload.get("webex_workspace_id") or payload.get("workspace_id"))
    webex_gateway_id = _optional_str(payload.get("webex_gateway_id") or payload.get("gateway_id"))
    did_number = _optional_str(payload.get("did_number") or payload.get("did") or payload.get("phone_number"))
    extension = _optional_str(payload.get("extension"))
    display_name = _optional_str(payload.get("display_name") or payload.get("name"))
    destination_type = (_optional_str(payload.get("destination_type")) or "local").lower()
    destination_value = _optional_str(payload.get("destination_value"))
    notes = _optional_str(payload.get("notes"))

    if not webex_line_id:
        return None, "webex_line_id is required"
    if not did_number:
        return None, "did_number is required"
    if not display_name:
        display_name = did_number
    try:
        did_number = clean_number(did_number)
    except ValueError as exc:
        return None, f"did_number {exc}"
    if extension is not None and not extension.isdigit():
        return None, "extension must contain digits only"
    if destination_type not in {"local", "email", "webex_bot", "teams_bot", "webhook"}:
        return None, "destination_type must be one of local, email, webex_bot, teams_bot, or webhook"

    enabled = payload.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in {"0", "false", "off", "no"}

    return (
        {
            "webex_line_id": webex_line_id,
            "webex_workspace_id": webex_workspace_id,
            "webex_gateway_id": webex_gateway_id,
            "did_number": did_number,
            "extension": extension,
            "display_name": display_name,
            "destination_type": destination_type,
            "destination_value": destination_value,
            "enabled": 1 if enabled else 0,
            "notes": notes,
        },
        None,
    )


def _destination_settings_payload(payload: dict) -> dict:
    return {
        "smtp_enabled": _bool_payload(payload.get("smtp_enabled")),
        "smtp_host": _optional_str(payload.get("smtp_host")),
        "smtp_port": _int_payload(payload.get("smtp_port"), default=587),
        "smtp_username": _optional_str(payload.get("smtp_username")),
        "smtp_password": _optional_str(payload.get("smtp_password")),
        "smtp_from_address": _optional_str(payload.get("smtp_from_address")),
        "smtp_use_tls": _bool_payload(payload.get("smtp_use_tls"), default=True),
        "webex_bot_enabled": _bool_payload(payload.get("webex_bot_enabled")),
        "webex_bot_token": _optional_str(payload.get("webex_bot_token")),
        "webex_room_id": _optional_str(payload.get("webex_room_id")),
        "teams_bot_enabled": _bool_payload(payload.get("teams_bot_enabled")),
        "teams_webhook_url": _optional_str(payload.get("teams_webhook_url")),
    }


def _bool_payload(value, default: bool = False) -> int:
    if value is None:
        return 1 if default else 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    return 0 if str(value).strip().lower() in {"0", "false", "off", "no", ""} else 1


def _int_payload(value, default: int | None = None) -> int | None:
    if value in {None, ""}:
        return default
    return int(value)


def _find_inbound_route(conn, *, webex_line_id: str | None, to_number: str | None):
    if webex_line_id:
        row = conn.execute(
            "SELECT * FROM inbound_routes WHERE enabled = 1 AND webex_line_id = ?",
            (webex_line_id,),
        ).fetchone()
        if row is not None:
            return row
    if not to_number:
        return None
    candidates = {_optional_str(to_number)}
    try:
        candidates.add(clean_number(to_number))
    except ValueError:
        pass
    for candidate in [value for value in candidates if value]:
        row = conn.execute(
            """
            SELECT * FROM inbound_routes
            WHERE enabled = 1 AND (did_number = ? OR extension = ?)
            """,
            (candidate, candidate),
        ).fetchone()
        if row is not None:
            return row
    return None


def _optional_str(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _integrity_error_message(exc: IntegrityError) -> str:
    message = str(exc).lower()
    if "webex_line_id" in message:
        return "webex_line_id already exists"
    if "did_number" in message:
        return "did_number already exists"
    if "extension" in message:
        return "extension already exists"
    return "inbound route conflicts with an existing route"


def _fs_response_body(response: str) -> str:
    if "\n\n" in response:
        return response.split("\n\n", 1)[1].strip()
    return response.strip()

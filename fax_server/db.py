from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS fax_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK(direction IN ('outbound', 'inbound')),
    status TEXT NOT NULL,
    to_number TEXT,
    from_number TEXT,
    webex_line_id TEXT,
    inbound_route_id INTEGER,
    source_path TEXT,
    tiff_path TEXT,
    freeswitch_uuid TEXT,
    freeswitch_response TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inbound_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    webex_line_id TEXT NOT NULL UNIQUE,
    webex_workspace_id TEXT,
    webex_gateway_id TEXT,
    did_number TEXT NOT NULL UNIQUE,
    extension TEXT UNIQUE,
    display_name TEXT NOT NULL,
    destination_type TEXT NOT NULL DEFAULT 'local' CHECK(destination_type IN ('local', 'email', 'webex_bot', 'teams_bot', 'webhook')),
    destination_value TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS destination_settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    smtp_enabled INTEGER NOT NULL DEFAULT 0 CHECK(smtp_enabled IN (0, 1)),
    smtp_host TEXT,
    smtp_port INTEGER,
    smtp_username TEXT,
    smtp_password TEXT,
    smtp_from_address TEXT,
    smtp_use_tls INTEGER NOT NULL DEFAULT 1 CHECK(smtp_use_tls IN (0, 1)),
    webex_bot_enabled INTEGER NOT NULL DEFAULT 0 CHECK(webex_bot_enabled IN (0, 1)),
    webex_bot_token TEXT,
    webex_room_id TEXT,
    teams_bot_enabled INTEGER NOT NULL DEFAULT 0 CHECK(teams_bot_enabled IN (0, 1)),
    teams_webhook_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS fax_jobs_updated_at
AFTER UPDATE ON fax_jobs
BEGIN
    UPDATE fax_jobs SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS inbound_routes_updated_at
AFTER UPDATE ON inbound_routes
BEGIN
    UPDATE inbound_routes SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS destination_settings_updated_at
AFTER UPDATE ON destination_settings
BEGIN
    UPDATE destination_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
"""

MIGRATIONS = (
    "ALTER TABLE fax_jobs ADD COLUMN webex_line_id TEXT",
    "ALTER TABLE fax_jobs ADD COLUMN inbound_route_id INTEGER",
    "ALTER TABLE inbound_routes ADD COLUMN webex_workspace_id TEXT",
    "ALTER TABLE inbound_routes ADD COLUMN webex_gateway_id TEXT",
)


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(database: Path) -> None:
    with connect(database) as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        _migrate_inbound_route_destination_types(conn)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for statement in MIGRATIONS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def _migrate_inbound_route_destination_types(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'inbound_routes'"
    ).fetchone()
    if row is None or "webex_bot" in (row["sql"] or ""):
        return

    conn.executescript(
        """
        DROP TRIGGER IF EXISTS inbound_routes_updated_at;
        ALTER TABLE inbound_routes RENAME TO inbound_routes_old_destination_type;

        CREATE TABLE inbound_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            webex_line_id TEXT NOT NULL UNIQUE,
            webex_workspace_id TEXT,
            webex_gateway_id TEXT,
            did_number TEXT NOT NULL UNIQUE,
            extension TEXT UNIQUE,
            display_name TEXT NOT NULL,
            destination_type TEXT NOT NULL DEFAULT 'local' CHECK(destination_type IN ('local', 'email', 'webex_bot', 'teams_bot', 'webhook')),
            destination_value TEXT,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO inbound_routes(
            id,
            webex_line_id,
            webex_workspace_id,
            webex_gateway_id,
            did_number,
            extension,
            display_name,
            destination_type,
            destination_value,
            enabled,
            notes,
            created_at,
            updated_at
        )
        SELECT
            id,
            webex_line_id,
            webex_workspace_id,
            webex_gateway_id,
            did_number,
            extension,
            display_name,
            destination_type,
            destination_value,
            enabled,
            notes,
            created_at,
            updated_at
        FROM inbound_routes_old_destination_type;

        DROP TABLE inbound_routes_old_destination_type;

        CREATE TRIGGER IF NOT EXISTS inbound_routes_updated_at
        AFTER UPDATE ON inbound_routes
        BEGIN
            UPDATE inbound_routes SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END;
        """
    )

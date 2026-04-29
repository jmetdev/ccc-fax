from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database: Path
    storage: Path
    freeswitch_storage: Path
    convert_bin: str
    office_convert_bin: str
    freeswitch_host: str
    freeswitch_event_socket_port: int
    freeswitch_event_socket_password: str
    freeswitch_cli_container: str
    freeswitch_profile: str
    freeswitch_gateway: str
    freeswitch_caller_id_number: str
    freeswitch_outbound_from_user: str
    freeswitch_inbound_dialplan: Path
    freeswitch_provider_dir: Path
    freeswitch_parent_gateway: str
    freeswitch_reload_on_provision: bool
    webex_access_token: str
    webex_org_id: str
    webex_api_base: str

    @classmethod
    def from_env(cls) -> "Settings":
        storage = Path(os.getenv("FAX_STORAGE", BASE_DIR / "storage")).expanduser()
        database = Path(os.getenv("FAX_DATABASE", storage / "fax.db")).expanduser()
        return cls(
            database=database,
            storage=storage,
            freeswitch_storage=Path(os.getenv("FREESWITCH_STORAGE", "/var/lib/ccc-fax")),
            convert_bin=os.getenv("FAX_CONVERT_BIN", "convert"),
            office_convert_bin=os.getenv("FAX_OFFICE_CONVERT_BIN", "soffice"),
            freeswitch_host=os.getenv("FREESWITCH_HOST", "127.0.0.1"),
            freeswitch_event_socket_port=int(os.getenv("FREESWITCH_EVENT_SOCKET_PORT", "8021")),
            freeswitch_event_socket_password=os.getenv("FREESWITCH_EVENT_SOCKET_PASSWORD", "ClueCon"),
            freeswitch_cli_container=os.getenv("FREESWITCH_CLI_CONTAINER", "ccc-freeswitch"),
            freeswitch_profile=os.getenv("FREESWITCH_PROFILE", "ccc-fax"),
            freeswitch_gateway=os.getenv("FREESWITCH_GATEWAY", "webex"),
            freeswitch_caller_id_number=os.getenv("FREESWITCH_CALLER_ID_NUMBER", ""),
            freeswitch_outbound_from_user=os.getenv("FREESWITCH_OUTBOUND_FROM_USER", ""),
            freeswitch_inbound_dialplan=Path(
                os.getenv(
                    "FREESWITCH_INBOUND_DIALPLAN",
                    BASE_DIR / "freeswitch-docker" / "configuration" / "dialplan" / "ccc-fax.xml",
                )
            ).expanduser(),
            freeswitch_provider_dir=Path(
                os.getenv(
                    "FREESWITCH_PROVIDER_DIR",
                    BASE_DIR / "freeswitch-docker" / "configuration" / "sip_profiles" / "provider",
                )
            ).expanduser(),
            freeswitch_parent_gateway=os.getenv("FREESWITCH_PARENT_GATEWAY", "webex"),
            freeswitch_reload_on_provision=os.getenv("FREESWITCH_RELOAD_ON_PROVISION", "true").lower()
            not in {"0", "false", "off", "no"},
            webex_access_token=os.getenv("WEBEX_ACCESS_TOKEN", ""),
            webex_org_id=os.getenv("WEBEX_ORG_ID", ""),
            webex_api_base=os.getenv("WEBEX_API_BASE", "https://webexapis.com/v1"),
        )

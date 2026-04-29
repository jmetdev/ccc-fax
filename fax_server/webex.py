from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ID_SEGMENTS = {"workspaces", "devices", "details"}
LINE_ID_KEYS = {
    "lineid",
    "line_id",
    "lineport",
    "line_port",
    "linenumberid",
    "line_number_id",
}


class WebexAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class WebexProvisioningSource:
    workspace_id: str
    gateway_id: str
    workspace: dict[str, Any]
    gateway: dict[str, Any]
    line_id: str
    phone_number: str
    extension: str | None
    display_name: str


@dataclass(frozen=True)
class WebexGatewayMember:
    gateway_id: str
    gateway: dict[str, Any]
    member: dict[str, Any]
    line_id: str
    phone_number: str
    extension: str | None
    display_name: str


class WebexClient:
    def __init__(self, access_token: str, *, api_base: str, org_id: str = "", timeout: float = 20.0) -> None:
        if not access_token:
            raise WebexAPIError("WEBEX_ACCESS_TOKEN is required")
        self.access_token = access_token
        self.api_base = api_base.rstrip("/")
        self.org_id = org_id
        self.timeout = timeout

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        return self._get(f"/workspaces/{workspace_id}")

    def get_device(self, device_id: str) -> dict[str, Any]:
        return self._get(f"/devices/{device_id}")

    def list_devices(self, **params: str) -> list[dict[str, Any]]:
        data = self._get("/devices", params={key: value for key, value in params.items() if value})
        return data.get("items", [])

    def get_device_by_mac(self, mac: str) -> dict[str, Any]:
        items = self.list_devices(mac=normalize_mac(mac))
        if not items:
            raise WebexAPIError(f"no Webex device found for MAC {mac}")
        if len(items) > 1:
            raise WebexAPIError(f"multiple Webex devices found for MAC {mac}")
        return items[0]

    def get_device_members(self, device_id: str) -> list[dict[str, Any]]:
        data = self._get(f"/telephony/config/devices/{device_id}/members")
        return data.get("members") or data.get("items") or []

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        if self.org_id and "orgId" not in params:
            params["orgId"] = self.org_id
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.api_base}{path}{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise WebexAPIError(f"Webex API returned {exc.code} for {path}: {body}") from exc
        except URLError as exc:
            raise WebexAPIError(f"Webex API request failed for {path}: {exc.reason}") from exc


def provisioning_source_from_webex(
    client: WebexClient,
    *,
    workspace_ref: str,
    gateway_ref: str,
) -> WebexProvisioningSource:
    workspace_id = control_hub_id(workspace_ref)
    gateway_id = control_hub_id(gateway_ref)
    workspace = client.get_workspace(workspace_id)
    gateway = client.get_device(gateway_id)

    calling = workspace.get("calling") or {}
    webex_calling = calling.get("webexCalling") or {}
    phone_number = _first_string(webex_calling, "phoneNumber", "phone_number", "number")
    extension = _first_string(webex_calling, "extension")
    display_name = _first_string(workspace, "displayName", "name") or phone_number or workspace_id
    line_id = extract_line_id(gateway) or extract_line_id(workspace)

    if not phone_number:
        raise WebexAPIError("Workspace does not include calling.webexCalling.phoneNumber")
    if not line_id:
        raise WebexAPIError(
            "Could not find a line ID in the Webex gateway/workspace payload; "
            "check that the token can read the customer-managed gateway details"
        )

    return WebexProvisioningSource(
        workspace_id=workspace_id,
        gateway_id=gateway_id,
        workspace=workspace,
        gateway=gateway,
        line_id=line_id,
        phone_number=phone_number,
        extension=extension,
        display_name=display_name,
    )


def gateway_members_from_mac(client: WebexClient, *, mac: str) -> list[WebexGatewayMember]:
    mac = normalize_mac(mac)
    gateway = client.get_device_by_mac(mac)
    gateway_id = _first_string(gateway, "id", "deviceId")
    if not gateway_id:
        raise WebexAPIError(f"Webex device for MAC {mac} did not include an id")
    members = client.get_device_members(gateway_id)
    if not members:
        raise WebexAPIError(f"Webex device {gateway_id} has no telephony members")

    provisionable: list[WebexGatewayMember] = []
    for member in members:
        line_id = extract_line_id(member)
        phone_number = _member_phone_number(member)
        extension = _member_extension(member)
        display_name = _member_display_name(member, phone_number, extension)
        if not line_id or not phone_number:
            continue
        provisionable.append(
            WebexGatewayMember(
                gateway_id=gateway_id,
                gateway=gateway,
                member=member,
                line_id=line_id,
                phone_number=phone_number,
                extension=extension,
                display_name=display_name,
            )
        )
    if not provisionable:
        raise WebexAPIError(f"Webex device {gateway_id} has no members with both lineId and phone number")
    return provisionable


def control_hub_id(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if not parsed.scheme:
        return value.strip("/")
    parts = [part for part in parsed.path.split("/") if part]
    if "details" in parts:
        index = parts.index("details")
        if index + 1 < len(parts):
            return parts[index + 1]
    for index, part in enumerate(parts):
        if part in ID_SEGMENTS and index + 1 < len(parts):
            return parts[index + 1]
    uuid_match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        parsed.path,
    )
    if uuid_match:
        return uuid_match.group(0)
    raise WebexAPIError(f"could not extract a Webex id from {value}")


def normalize_mac(mac: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()
    if len(normalized) != 12:
        raise WebexAPIError("mac must contain 12 hexadecimal characters")
    return normalized


def extract_line_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = re.sub(r"[^a-z0-9_]", "", key.lower())
            if normalized in LINE_ID_KEYS and isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = extract_line_id(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = extract_line_id(item)
            if found:
                return found
    return None


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _member_phone_number(member: dict[str, Any]) -> str | None:
    return _first_nested_string(
        member,
        "phoneNumber",
        "phone_number",
        "number",
        "directNumber",
        "direct_number",
        "external",
        "primaryNumber",
    )


def _member_extension(member: dict[str, Any]) -> str | None:
    return _first_nested_string(member, "extension", "extensionNumber", "extension_number")


def _member_display_name(member: dict[str, Any], phone_number: str | None, extension: str | None) -> str:
    return (
        _first_nested_string(member, "displayName", "display_name", "name", "firstName")
        or phone_number
        or extension
        or "Webex Gateway Line"
    )


def _first_nested_string(payload: Any, *keys: str) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _first_nested_string(value, *keys)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_nested_string(item, *keys)
            if found:
                return found
    return None

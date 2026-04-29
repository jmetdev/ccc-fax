from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable


GENERATED_PREFIX = "generated-webex-line-"


def render_line_gateways(routes: Iterable[dict], provider_dir: Path, parent_gateway_name: str) -> list[str]:
    provider_dir.mkdir(parents=True, exist_ok=True)
    parent_path = provider_dir / f"{parent_gateway_name}.xml"
    parent = ET.parse(parent_path).getroot()
    if parent.tag != "gateway":
        raise ValueError(f"{parent_path} does not contain a FreeSWITCH gateway")
    parent_username = _param_value(parent, "username")

    rendered: list[str] = []
    parent_user = _sip_user(parent_username)
    line_ids = [
        str(route["webex_line_id"])
        for route in routes
        if route.get("enabled") and route.get("webex_line_id") and route.get("webex_line_id") != parent_username
        and _sip_user(str(route["webex_line_id"])) != parent_user
    ]
    expected_paths = {provider_dir / f"{GENERATED_PREFIX}{_safe_name(line_id)}.xml" for line_id in line_ids}

    for existing in provider_dir.glob(f"{GENERATED_PREFIX}*.xml"):
        if existing not in expected_paths:
            existing.unlink()

    for line_id in line_ids:
        gateway = _clone_gateway(parent, line_id)
        path = provider_dir / f"{GENERATED_PREFIX}{_safe_name(line_id)}.xml"
        _indent(gateway)
        ET.ElementTree(gateway).write(path, encoding="utf-8", xml_declaration=False)
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        rendered.append(str(path))

    return rendered


def _clone_gateway(parent: ET.Element, line_id: str) -> ET.Element:
    gateway = ET.Element("gateway", {"name": f"webex-line-{_safe_name(line_id)}"})
    sip_user = _sip_user(line_id)
    for param in parent.findall("param"):
        attrs = dict(param.attrib)
        name = attrs.get("name")
        if name in {"username", "from-user", "extension"}:
            attrs["value"] = sip_user
        gateway.append(ET.Element("param", attrs))
    return gateway


def _sip_user(value: str | None) -> str | None:
    if value is None:
        return None
    return value.split("@", 1)[0]


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return normalized[:80] or "unknown"


def _param_value(gateway: ET.Element, name: str) -> str | None:
    for param in gateway.findall("param"):
        if param.attrib.get("name") == name:
            return param.attrib.get("value")
    return None


def _indent(element: ET.Element, level: int = 0) -> None:
    indent = "\n" + level * "  "
    child_indent = "\n" + (level + 1) * "  "
    children = list(element)
    if children:
        if not element.text or not element.text.strip():
            element.text = child_indent
        for child in children:
            _indent(child, level + 1)
        if not children[-1].tail or not children[-1].tail.strip():
            children[-1].tail = indent
    if level and (not element.tail or not element.tail.strip()):
        element.tail = indent

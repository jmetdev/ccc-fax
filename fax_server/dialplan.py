from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable


def render_inbound_dialplan(routes: Iterable[dict], destination: Path, freeswitch_storage: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    include = ET.Element("include")
    context = ET.SubElement(include, "context", {"name": "ccc-fax-inbound"})
    enabled_routes = [route for route in routes if route.get("enabled")]
    for route in enabled_routes:
        _append_route_extension(context, route, freeswitch_storage)
    _append_catchall_extension(context, freeswitch_storage)

    _indent(include)
    tree = ET.ElementTree(include)
    tree.write(destination, encoding="utf-8", xml_declaration=False)
    destination.write_text(destination.read_text(encoding="utf-8") + "\n", encoding="utf-8")


def _append_route_extension(context: ET.Element, route: dict, freeswitch_storage: Path) -> None:
    name = f"receive-route-{route['id']}"
    expression = f"^({'|'.join(_route_patterns(route))})$"
    extension = ET.SubElement(context, "extension", {"name": name})
    condition = ET.SubElement(extension, "condition", {"field": "destination_number", "expression": expression})
    _append_receive_actions(condition, freeswitch_storage)


def _append_catchall_extension(context: ET.Element, freeswitch_storage: Path) -> None:
    extension = ET.SubElement(context, "extension", {"name": "receive-fax-catchall"})
    condition = ET.SubElement(extension, "condition", {"field": "destination_number", "expression": "^(.+)$"})
    _append_receive_actions(condition, freeswitch_storage)


def _append_receive_actions(condition: ET.Element, freeswitch_storage: Path) -> None:
    incoming = freeswitch_storage / "faxes" / "incoming"
    actions = (
        ("answer", None),
        ("set", "fax_enable_t38=true"),
        ("set", "fax_enable_t38_request=true"),
        ("set", "fax_ident=CCC Fax"),
        ("set", "fax_header=CCC Fax ${destination_number}"),
        ("mkdir", str(incoming)),
        (
            "set",
            f"fax_file={incoming}/${{strftime(%Y%m%d-%H%M%S)}}-${{uuid}}-${{caller_id_number}}-${{destination_number}}.tiff",
        ),
        ("rxfax", "${fax_file}"),
        ("hangup", None),
    )
    for application, data in actions:
        attrs = {"application": application}
        if data is not None:
            attrs["data"] = data
        ET.SubElement(condition, "action", attrs)


def _route_patterns(route: dict) -> list[str]:
    patterns: list[str] = []
    for value in (route.get("webex_line_id"), route.get("did_number"), route.get("extension")):
        if value:
            patterns.append(re.escape(str(value)))

    did = str(route.get("did_number") or "")
    digits = re.sub(r"\D", "", did)
    if len(digits) == 11 and digits.startswith("1"):
        patterns.append(r"\+?" + re.escape(digits))
        patterns.append(r"\+?1?" + re.escape(digits[1:]))
    elif len(digits) == 10:
        patterns.append(r"\+?1?" + re.escape(digits))

    deduped: list[str] = []
    for pattern in patterns:
        if pattern not in deduped:
            deduped.append(pattern)
    return deduped


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

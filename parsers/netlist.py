"""Read KiCad's XML netlist, including resolved pin functions and no-connect flags."""

import xml.etree.ElementTree as ET

from ..models import Pin
from .sexpr import ParseError


def parse_netlist(text: str) -> tuple[dict[str, list[Pin]], dict[str, list[str]]]:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ParseError("DTD/entity declarations are unsupported in netlist XML.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParseError(f"Invalid netlist XML: {exc}") from exc
    if root.tag != "export" or root.find("components") is None or root.find("nets") is None:
        raise ParseError("Expected a KiCad XML netlist with components and nets.")
    pins = {}
    for comp in root.findall("./components/comp"):
        ref = comp.get("ref", "")
        if not ref or ref in pins:
            raise ParseError("Missing or duplicate component reference in the netlist.")
        pins[ref] = []
    nets = {}
    seen = set()
    for net in root.findall("./nets/net"):
        name = net.get("name", "")
        if not name or name in nets:
            raise ParseError("Missing or duplicate net name in the netlist.")
        nets[name] = []
        for node in net.findall("node"):
            ref, number = node.get("ref", ""), node.get("pin", "")
            if ref not in pins or not number or (ref, number) in seen:
                raise ParseError(f"Unknown component or repeated pin {ref}.{number} in the netlist.")
            seen.add((ref, number))
            pintype = node.get("pintype", "")
            pins[ref].append(Pin(number, node.get("pinfunction", ""), pintype,
                                 name, "no_connect" in pintype.split("+")))
            nets[name].append(f"{ref}.{number}")
    return pins, nets

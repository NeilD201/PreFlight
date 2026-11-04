"""Read footprint identity and fields from a modern KiCad board."""

import math

from ..models import ComponentRecord, Pad, ParsedComponents
from .sexpr import ParseError, parse, properties


def _pad_geometry(pad) -> tuple:
    """Measured pad center and size in mm from the pad node.

    Returns (x, y, width, height); any entry is None when the pad node has
    no usable at/size (custom primitives, arcs). Never raises: unusable
    geometry is reported as None so the caller treats conformance as
    unverified rather than inventing dimensions.
    """
    values: list = [None, None, None, None]
    try:
        at = pad.first("at")
        if at is not None and len(at.values) >= 2:
            x, y = float(at.values[0]), float(at.values[1])
            if math.isfinite(x) and math.isfinite(y):
                values[0], values[1] = x, y
        size = pad.first("size")
        if size is not None and len(size.values) >= 2:
            w, h = float(size.values[0]), float(size.values[1])
            if math.isfinite(w) and math.isfinite(h) and w > 0 and h > 0:
                values[2], values[3] = w, h
    except (TypeError, ValueError):
        pass
    return tuple(values)


def parse_pcb(text: str, source_file: str) -> ParsedComponents:
    root = parse(text, "kicad_pcb")
    result = ParsedComponents()
    net_names = {}
    for net in root.all("net"):
        if len(net.values) != 2:
            raise ParseError(f"Invalid net definition at line {net.line}.")
        if net.values[0] in net_names and net_names[net.values[0]] != net.values[1]:
            raise ParseError(f"Duplicate net identifier at line {net.line}.")
        net_names[net.values[0]] = net.values[1]
    if root.all("module"):
        raise ParseError("Legacy PCB modules are unsupported; resave the board in modern KiCad.")
    for footprint in root.all("footprint"):
        if not footprint.values:
            raise ParseError(f"Missing footprint identifier at line {footprint.line}.")
        fields = properties(footprint)
        # KiCad 6/7 store these two fields in fp_text instead of property nodes.
        for text_node in footprint.all("fp_text"):
            if len(text_node.values) >= 2 and text_node.values[0] in ("reference", "value"):
                name = text_node.values[0].capitalize()
                old_value = text_node.values[1]
                if name in fields and fields[name] != old_value:
                    raise ParseError(f"Conflicting {name} fields at line {footprint.line}.")
                fields[name] = old_value
        attr = footprint.first("attr")
        attributes = attr.values if attr else []
        pads = []
        for pad in footprint.all("pad"):
            if len(pad.values) < 2:
                raise ParseError(f"Invalid pad at line {pad.line}.")
            net = pad.first("net")
            net_name = ""
            if net:
                if len(net.values) != 2 or net.values[0] not in net_names:
                    raise ParseError(f"Unknown or invalid pad net at line {pad.line}.")
                net_name = net.values[1]
                if net_names[net.values[0]] != net_name:
                    raise ParseError(f"Pad net name disagrees with the board net table at line {pad.line}.")
            geometry = _pad_geometry(pad)
            pads.append(Pad(pad.values[0], net_name, pad.line, pad.values[1], pad.get("pinfunction"),
                            x_mm=geometry[0], y_mm=geometry[1],
                            width_mm=geometry[2], height_mm=geometry[3]))
        position = footprint.first("at")
        x = y = rotation = None
        if position:
            if len(position.values) < 2:
                raise ParseError(f"Invalid footprint position at line {position.line}.")
            try:
                x, y = map(float, position.values[:2])
                rotation = float(position.values[2]) if len(position.values) > 2 else 0.0
                if not all(math.isfinite(v) for v in (x, y, rotation)):
                    raise ValueError("non-finite coordinate")
            except ValueError as exc:
                raise ParseError(f"Invalid footprint position at line {position.line}.") from exc
        result.components.append(ComponentRecord(
            reference=fields.get("Reference", "").strip(),
            value=fields.get("Value", ""), footprint=footprint.values[0],
            source_file=source_file, line=footprint.line,
            uuid=footprint.get("uuid", footprint.get("tstamp")),
            in_bom="exclude_from_bom" not in attributes,
            dnp="dnp" in attributes, board_only="board_only" in attributes,
            attributes=attributes, properties=fields,
            pads=pads, x_mm=x, y_mm=y, rotation=rotation, layer=footprint.get("layer"),
        ))
    return result

"""Internal KiCad schematic connectivity resolver.

Builds pin-to-net assignments directly from schematic sheets (symbols,
wires, junctions, labels, power ports) when no KiCad CLI netlist export is
available. Output matches ``parsers.netlist.parse_netlist`` shapes so the
existing connectivity comparison runs unchanged.

Honesty rules:
- Pin positions come from library pin offsets transformed by the instance
  placement. Library offsets are y-up while the sheet frame is y-down, so
  the library y is negated before the clockwise rotation in _rotate();
  the convention was discriminated empirically on a reference board
  (name mismatches 589 -> 46, net splits 120 -> 4 when the flip is on).
- Symbols with mirror transforms are skipped (positions unverified) with a
  note, never guessed.
- Pins missing from the library definition are skipped with a note.
- Labels attach only to copper they touch; unattached labels are noted.
- Unlabeled copper nets are named ``Net-(Ref-PinName)`` and unconnected pins
  ``unconnected-(Ref-Pin)``, mirroring KiCad netlist conventions.
"""

import math
from collections import defaultdict

from ..models import Pin
from .netlist import ParseError
from .sexpr import Node, parse

_EPS = 1e-6


def _rotate(px, py, theta):
    """Clockwise rotation in y-down board coordinates (see module docstring)."""
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return (px * cos_t + py * sin_t, -px * sin_t + py * cos_t)


def _key(point):
    return (round(point[0], 6), round(point[1], 6))


def _ukey(sheet_name, point):
    """Sheet-scoped union key: child sheets reuse local coordinates, so bare
    (x, y) keys would short copper across sheets that share numbers."""
    return (sheet_name, round(point[0], 6), round(point[1], 6))


def _dist_point_segment(point, end_a, end_b):
    ax, ay, bx, by = end_a[0], end_a[1], end_b[0], end_b[1]
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.dist(point, end_a)
    t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq))
    return math.dist(point, (ax + t * dx, ay + t * dy))


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, key):
        parent = self.parent.setdefault(key, key)
        if parent != key:
            parent = self.find(parent)
            self.parent[key] = parent
        return parent

    def union(self, first, second):
        root_first, root_second = self.find(first), self.find(second)
        if root_first != root_second:
            self.parent[root_first] = root_second


def _lib_pin_table(sheet_root):
    """{lib_id: (common_pins, {unit: pins})} with (number, name, x, y, etype)."""
    table = {}
    lib_symbols = sheet_root.first("lib_symbols")
    if lib_symbols is None:
        return table
    for lib in lib_symbols.children:
        if not isinstance(lib, Node) or lib.tag != "symbol" or not lib.values:
            continue
        common, units = [], defaultdict(list)
        direct = [c for c in lib.children
                  if isinstance(c, Node) and c.tag == "pin"]
        for pin in direct:
            entry = _pin_entry(pin)
            if entry:
                common.append(entry)
        for sub in lib.children:
            if not (isinstance(sub, Node) and sub.tag == "symbol") or not sub.values:
                continue
            parts = str(sub.values[0]).rsplit("_", 2)
            try:
                unit = int(parts[1]) if len(parts) == 3 else 1
            except (ValueError, IndexError):
                unit = -1
            for child in sub.children:
                if not (isinstance(child, Node) and child.tag == "pin"):
                    continue
                entry = _pin_entry(child)
                if not entry:
                    continue
                if unit == 0:
                    common.append(entry)
                elif unit > 0:
                    units[unit].append(entry)
                else:
                    common.append(entry)
        table[str(lib.values[0])] = (common, dict(units))
    return table


def _pin_entry(pin):
    try:
        at, name_node, number_node = pin.first("at"), pin.first("name"), pin.first("number")
        if at is None or number_node is None or not number_node.values:
            return None
        name = str(name_node.values[0]) if name_node and name_node.values else ""
        return (str(number_node.values[0]), name,
                float(at.values[0]), float(at.values[1]),
                str(pin.values[0]) if len(pin.values) > 0 else "")
    except (ValueError, TypeError, IndexError):
        return None


def _properties(node):
    props = {}
    for child in node.children:
        if isinstance(child, Node) and child.tag == "property" and child.values:
            props[str(child.values[0])] = child
    out = {}
    for key, prop in props.items():
        out[key] = str(prop.values[1]) if len(prop.values) > 1 else ""
    return out


def _sheet_data(text, source_file):
    root = parse(text, "kicad_sch")
    return root


def resolve_schematic_nets(root_text, child_texts=None, root_name="root"):
    """Resolve pin-to-net assignments for a schematic hierarchy.

    ``child_texts`` maps sheet filenames to file text. Returns pins, nets,
    notes and stats. Raises ParseError on unreadable input.
    """
    sheets = {root_name: _sheet_data(root_text, root_name)}
    for name, text in (child_texts or {}).items():
        try:
            sheets[name] = _sheet_data(text, name)
        except ParseError as exc:
            raise ParseError(f"Could not parse child sheet {name}: {exc}")
    lib_tables = {name: _lib_pin_table(root) for name, root in sheets.items()}
    notes = []

    union = _UnionFind()
    segments = []  # (sheet, end_a, end_b)
    junctions = []  # (sheet, point)
    labels = []  # (sheet, text, point, scope) scope: local|global|hier
    sheet_pin_points = []  # (sheet, name, point)
    no_connects = []  # (sheet, point)
    pin_spots = []  # dicts with sheet/ref/number/name/etype/point
    power_ports = []  # (sheet, net, point)

    for sheet_name, root in sheets.items():
        lib = lib_tables.get(sheet_name, {})
        for symbol in [n for n in root.children
                       if isinstance(n, Node) and n.tag == "symbol" and n.first("lib_id")]:
            lib_id = str(symbol.first("lib_id").values[0])
            at = symbol.first("at")
            if at is None or len(at.values) < 2:
                notes.append(f"Symbol {lib_id} in {sheet_name} has no placement; skipped.")
                continue
            try:
                fx, fy = float(at.values[0]), float(at.values[1])
                rotation = float(at.values[2]) if len(at.values) >= 3 else 0.0
            except (ValueError, TypeError):
                notes.append(f"Symbol {lib_id} in {sheet_name} has a bad placement; skipped.")
                continue
            if any(isinstance(c, Node) and c.tag == "mirror" for c in symbol.children):
                notes.append(f"Symbol {lib_id} in {sheet_name} is mirrored; its pins are unverified.")
                continue
            props = _properties(symbol)
            if lib_id == "power:PWR_FLAG":
                continue
            if lib_id.startswith("power:"):
                common, units = lib.get(lib_id, ([], {}))
                power_pins = list(common)
                for unit_pins in units.values():
                    power_pins.extend(unit_pins)
                seen_positions = set()
                unique = []
                for entry in power_pins:
                    key = (round(entry[2], 6), round(entry[3], 6))
                    if key not in seen_positions:
                        seen_positions.add(key)
                        unique.append(entry)
                if len(unique) == 1:
                    _number, name, px, py, _etype = unique[0]
                    qx, qy = _rotate(px, -py, math.radians(rotation))
                    net = props.get("Value", "") or (name if name != "~" else "")
                    power_ports.append((sheet_name, net, (fx + qx, fy + qy)))
                else:
                    notes.append(f"Power symbol {lib_id} in {sheet_name} has no single pin; skipped.")
                continue
            reference = props.get("Reference", "")
            try:
                unit = int(symbol.first("unit").values[0]) if symbol.first("unit") else 1
            except (ValueError, TypeError, IndexError):
                unit = 1
            common, units = lib.get(lib_id, ([], {}))
            available = {entry[0]: entry for entry in list(common) + list(units.get(unit, []))}
            for pin in [c for c in symbol.children
                        if isinstance(c, Node) and c.tag == "pin" and c.values]:
                number = str(pin.values[0])
                entry = available.get(number)
                if entry is None:
                    notes.append(f"Pin {reference}.{number} is missing from library {lib_id}; skipped.")
                    continue
                _number, name, px, py, etype = entry
                qx, qy = _rotate(px, -py, math.radians(rotation))
                point = (fx + qx, fy + qy)
                union.find(_key(point))
                pin_spots.append({"sheet": sheet_name, "ref": reference,
                                  "number": number, "name": name, "etype": etype,
                                  "point": point})
        for wire in [n for n in root.children if isinstance(n, Node) and n.tag == "wire"]:
            pts = wire.first("pts")
            if pts is None:
                continue
            coords = []
            for xy in pts.children:
                if isinstance(xy, Node) and xy.tag == "xy" and len(xy.values) >= 2:
                    try:
                        coords.append((float(xy.values[0]), float(xy.values[1])))
                    except (ValueError, TypeError):
                        continue
            for start, end in zip(coords, coords[1:]):
                segments.append((sheet_name, start, end))
                union.find(_key(start))
                union.find(_key(end))
        for junction in [n for n in root.children if isinstance(n, Node) and n.tag == "junction"]:
            at = junction.first("at")
            if at is not None and len(at.values) >= 2:
                try:
                    junctions.append((sheet_name, (float(at.values[0]), float(at.values[1]))))
                except (ValueError, TypeError):
                    continue
        for tag, scope in (("label", "local"), ("global_label", "global"),
                           ("hierarchical_label", "hier")):
            for label in [n for n in root.children if isinstance(n, Node) and n.tag == tag]:
                at = label.first("at")
                if not label.values or at is None or len(at.values) < 2:
                    continue
                try:
                    labels.append((sheet_name, str(label.values[0]),
                                   (float(at.values[0]), float(at.values[1])), scope))
                except (ValueError, TypeError):
                    continue
        for sheet in [n for n in root.children if isinstance(n, Node) and n.tag == "sheet"]:
            for pin in [c for c in sheet.children if isinstance(c, Node) and c.tag == "pin"]:
                at = pin.first("at")
                if pin.values and at is not None and len(at.values) >= 2:
                    try:
                        sheet_pin_points.append((sheet_name, str(pin.values[0]),
                                                 (float(at.values[0]), float(at.values[1]))))
                    except (ValueError, TypeError):
                        continue
        for marker in [n for n in root.children if isinstance(n, Node) and n.tag == "no_connect"]:
            at = marker.first("at")
            if at is not None and len(at.values) >= 2:
                try:
                    no_connects.append((sheet_name, (float(at.values[0]), float(at.values[1]))))
                except (ValueError, TypeError):
                    continue

    # Endpoint-to-endpoint and endpoint-to-segment joins (KiCad T-junctions
    # connect without a dot; only body crossings need a junction). All union
    # keys are sheet-scoped so reused local coordinates never short sheets.
    by_sheet_segments = defaultdict(list)
    for sheet_name, start, end in segments:
        by_sheet_segments[sheet_name].append((start, end))
        union.union(_ukey(sheet_name, start), _ukey(sheet_name, end))
    for sheet_name, group in by_sheet_segments.items():
        ends = set()
        for start, end in group:
            ends.add(_ukey(sheet_name, start))
            ends.add(_ukey(sheet_name, end))
        for key in ends:
            point = (key[1], key[2])
            for start, end in group:
                if _ukey(sheet_name, start) == key or _ukey(sheet_name, end) == key:
                    continue
                if _dist_point_segment(point, start, end) <= _EPS:
                    union.union(key, _ukey(sheet_name, start))
    # Junction dots merge every wire they touch.
    for sheet_name, point in junctions:
        touched = []
        for start, end in by_sheet_segments.get(sheet_name, []):
            if (_dist_point_segment(point, start, end) <= _EPS
                    or math.dist(point, start) <= _EPS
                    or math.dist(point, end) <= _EPS):
                touched.append(_ukey(sheet_name, start))
                touched.append(_ukey(sheet_name, end))
        for key in touched[1:]:
            union.union(touched[0], key)

    # Pins are connection points too: a pin coincident with (or touching) a
    # wire joins that copper (overlapping pins already share one union
    # node), and ports/labels placed exactly on a pin attach to the pin's
    # net even with no wire drawn.
    pin_keys = defaultdict(set)
    for spot in pin_spots:
        key = _ukey(spot["sheet"], spot["point"])
        union.find(key)
        pin_keys[spot["sheet"]].add(key)
        for start, end in by_sheet_segments.get(spot["sheet"], []):
            if _ukey(spot["sheet"], start) == key or _ukey(spot["sheet"], end) == key:
                union.union(key, _ukey(spot["sheet"], start))
                union.union(key, _ukey(spot["sheet"], end))
            elif _dist_point_segment(spot["point"], start, end) <= _EPS:
                union.union(key, _ukey(spot["sheet"], start))

    def group_at(sheet_name, point):
        for key in pin_keys.get(sheet_name, ()):
            if math.dist(point, (key[1], key[2])) <= _EPS:
                return union.find(key)
        best, best_dist = None, _EPS
        for start, end in by_sheet_segments.get(sheet_name, []):
            for candidate in (start, end):
                distance = math.dist(point, candidate)
                if distance <= best_dist:
                    best, best_dist = _ukey(sheet_name, candidate), distance
        if best is not None:
            return union.find(best)
        for start, end in by_sheet_segments.get(sheet_name, []):
            if _dist_point_segment(point, start, end) <= _EPS:
                return union.find(_ukey(sheet_name, start))
        return None

    # Net names: globals span sheets, locals stay sheet-scoped, hierarchical
    # pins bind to same-named child labels.
    group_net = {}
    for sheet_name, text, point, scope in labels:
        group = group_at(sheet_name, point)
        if group is None:
            notes.append(f"Label {text!r} in {sheet_name} touches no wire; ignored.")
            continue
        if scope == "local":
            key = (sheet_name, text)
        else:
            key = text
        group_net.setdefault(group, key)
        if group_net[group] != key:
            notes.append(f"Conflicting labels on one copper net in {sheet_name}; kept {group_net[group]!r}.")
    for sheet_name, net, point in power_ports:
        if not net:
            continue
        group = group_at(sheet_name, point)
        if group is None:
            notes.append(f"Power port {net!r} in {sheet_name} touches no wire; ignored.")
            continue
        group_net.setdefault(group, net)
        if group_net[group] != net:
            notes.append(f"Conflicting power nets on one copper net in {sheet_name}; kept {group_net[group]!r}.")
    hier_names = defaultdict(list)
    for sheet_name, text, point, scope in labels:
        if scope == "hier":
            hier_names[(sheet_name, text)].append(point)
    for sheet_name, name, point in sheet_pin_points:
        group = group_at(sheet_name, point)
        if group is None:
            continue
        for (child_sheet, text), points in hier_names.items():
            if text == name:
                for _ in points:
                    group_net[group] = ("hier", child_sheet, text)

    def auto_net_name(members):
        """Mirror KiCad's Net-(Ref-Pin) convention from board evidence.

        KiCad names an unlabeled net after the member pad with a meaningful
        (non-numeric) symbol pin name, breaking ties by lexical reference
        (DIN, USB_DP, A win over bare pad numbers); with no usable name it
        falls back to the lexically-first reference with Pad<number>.
        Verified against reference-board auto nets (Net-(D100-DIN),
        Net-(U2-USB_DP), Net-(D1-A), Net-(BOOT1-Pad2)).
        """
        candidates = []
        for ref, number, name in members:
            # "~" is KiCad's placeholder for an unnamed pin, not a real name.
            usable = bool(name and name != "~" and not name.isdigit())
            suffix = name if usable else f"Pad{number}"
            candidates.append((0 if usable else 1, ref, suffix, number))
        _, ref, suffix, _number = min(candidates)
        return f"Net-({ref}-{suffix})"

    def labeled_net_name(group, sheet_name):
        key = group_net.get(group)
        if key is None:
            return None
        if isinstance(key, tuple) and key and key[0] == "hier":
            return f"{key[1]}:{key[2]}"
        if isinstance(key, tuple):
            _, text = key
            others = {k for g, k in group_net.items()
                      if isinstance(k, tuple) and k[1:] == key[1:] and g != group}
            if others:
                notes.append(f"Local label {text!r} in {sheet_name} collides across sheets; qualified.")
                return f"{sheet_name}:{text}"
            return text
        return key

    group_members = defaultdict(list)
    for spot in pin_spots:
        group_members[union.find(_ukey(spot["sheet"], spot["point"]))].append(
            (spot["ref"], spot["number"], spot["name"]))
    group_auto_name = {}
    for group, members in group_members.items():
        if group not in group_net:
            group_auto_name[group] = auto_net_name(members)

    pins, nets = {}, {}
    for spot in pin_spots:
        group = union.find(_ukey(spot["sheet"], spot["point"]))
        net = labeled_net_name(group, spot["sheet"])
        if net is None:
            net = group_auto_name[group]
        no_connect = any(math.dist(spot["point"], point) <= _EPS
                         and sheet == spot["sheet"] for sheet, point in no_connects)
        pin = Pin(spot["number"], spot["name"], spot["etype"], net, no_connect)
        pins.setdefault(spot["ref"], []).append(pin)
        nets.setdefault(net, []).append(f"{spot['ref']}.{spot['number']}")
    # A pin with no wire at all gets its own unconnected net. Overlapping
    # pins share one union node and keep their shared auto Net- name even
    # with no wire drawn; only a lone pin becomes unconnected-(Ref-Pin).
    wired_points = set()
    for sheet_name, start, end in segments:
        wired_points.add(_ukey(sheet_name, start))
        wired_points.add(_ukey(sheet_name, end))
    group_spots = defaultdict(list)
    for spot in pin_spots:
        group_spots[union.find(_ukey(spot["sheet"], spot["point"]))].append(spot)
    for group, spots in group_spots.items():
        if group in group_net:
            continue
        if any(_ukey(item["sheet"], item["point"]) in wired_points for item in spots):
            continue
        if len(spots) != 1:
            continue
        spot = spots[0]
        net = f"unconnected-({spot['ref']}-{spot['number']})"
        for pin in pins.get(spot["ref"], []):
            if pin.number == spot["number"]:
                old = pin.net
                pin.net = net
                if old in nets and f"{spot['ref']}.{spot['number']}" in nets[old]:
                    nets[old].remove(f"{spot['ref']}.{spot['number']}")
                    if not nets[old]:
                        del nets[old]
        nets.setdefault(net, []).append(f"{spot['ref']}.{spot['number']}")
    stats = {"pins": sum(len(v) for v in pins.values()),
             "nets": len(nets),
             "sheets": len(sheets),
             "unattached_labels": sum(1 for n in notes if "touches no wire" in n)}
    return {"pins": pins, "nets": nets, "notes": notes, "stats": stats}

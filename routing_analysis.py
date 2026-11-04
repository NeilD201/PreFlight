"""General-purpose physical PCB routing analysis: measured copper facts first.

Builds a net/layer-aware geometric connectivity graph from drawn segments,
arcs, vias and pads, then reports endpoint-to-endpoint routed path lengths,
generic differential-pair metrics, turn angles and pair-level parallel runs.

Every number is a measurement of drawn copper. Engineering judgments are
labeled as judgments: a length mismatch is a reason to check the interface
skew budget, not proof of failure, and obtuse bends are normal routing.
"""

import hashlib
import math
import re
from collections import defaultdict
from heapq import heappop, heappush

from .archive import ArchiveError, read_project_file
from .models import Issue
from .parsers.sexpr import ParseError, parse
from .review import build_review
from .routing_geometry import parallel_pair, point

ACUTE_TURN_DEG = 90.0
MIN_BEND_LEG_MM = 0.5
MISMATCH_REVIEW_MM = 1.0
MAX_SEGMENTS_PER_NET = 5000
MAX_PINS_PER_NET = 64
MAX_PAIR_ROWS = 200
MAX_GAP_SAMPLES = 200
MAX_EVIDENCE_BENDS = 20

AUTO_NET = re.compile(r"(?i)^(net[-_(\[]|unconnected-\()")

# Screening constants for interface-aware skew assessment. These are generic
# signal-integrity screening bounds, not interface compliance limits.
PS_PER_MM = 6.7  # microstrip FR-4 propagation screening assumption
USB_FS_MIN_RISE_NS = 4.0  # USB 2.0 full-speed minimum rise/fall time
USB_FS_SKEW_SCREEN_PS = 400.0  # 10% of the minimum rise time
_USB_DM_NAMES = {'D+', 'D-', 'DP', 'DM', 'USB_DP', 'USB_DM', 'USBDP', 'USBDM',
                 'D_P', 'D_N', 'UDP', 'UDM'}
POWER_BASE = {"GND", "AGND", "DGND", "PGND", "EGND", "VCC", "VDD", "VSS", "VEE",
              "V5", "V33", "5V", "3V3", "3V", "VBUS", "VBAT", "VIN", "VOUT",
              "VPWR", "PWR", "BAT", "VREF"}


def _arc_length(start, mid, end):
    """Analytic arc length from start/mid/end points (radius x sweep)."""
    ax, ay, bx, by, cx, cy = start[0], start[1], mid[0], mid[1], end[0], end[1]
    denominator = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    straight = math.dist(start, mid) + math.dist(mid, end)
    if abs(denominator) < 1e-12:
        return straight
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / denominator
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / denominator
    radius = math.hypot(ax - ux, ay - uy)
    if radius < 1e-9:
        return straight
    angles = [math.atan2(y - uy, x - ux) for x, y in (start, mid, end)]
    sweep = (angles[2] - angles[0]) % (2 * math.pi)
    if (angles[1] - angles[0]) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    return abs(sweep) * radius


def _arc_chords(start, mid, end, steps=8):
    """Short chords for topology only; lengths always use _arc_length."""
    ax, ay, bx, by, cx, cy = start[0], start[1], mid[0], mid[1], end[0], end[1]
    denominator = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(denominator) < 1e-12:
        return [(start, mid), (mid, end)]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / denominator
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / denominator
    radius = math.hypot(ax - ux, ay - uy)
    if radius < 1e-9:
        return [(start, mid), (mid, end)]
    angles = [math.atan2(y - uy, x - ux) for x, y in (start, mid, end)]
    sweep = (angles[2] - angles[0]) % (2 * math.pi)
    if (angles[1] - angles[0]) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    pts = [[ux + radius * math.cos(angles[0] + sweep * i / steps),
            uy + radius * math.sin(angles[0] + sweep * i / steps)] for i in range(steps + 1)]
    return list(zip(pts, pts[1:]))


def _layers_in_order(board):
    layers = board.first("layers")
    if not layers:
        return []
    return [n.values[0] for n in layers.children
            if n.values and n.values[0].endswith(".Cu")]


def _pad_layer_set(pad_layers, copper_layers):
    copper = [layer for layer in pad_layers if layer.endswith(".Cu")]
    if any(layer.startswith("*.") for layer in copper):
        return set(copper_layers)
    return set(copper) & set(copper_layers)


def _pad_centers(board, copper_layers):
    """Footprint pad centers in board coordinates with their copper layers."""
    pads = []
    for footprint in board.all("footprint"):
        try:
            fx, fy = float(footprint.first("at").values[0]), float(footprint.first("at").values[1])
        except (AttributeError, ValueError, TypeError, IndexError):
            continue
        rotation = 0.0
        try:
            rotation = float(footprint.first("at").values[2])
        except (AttributeError, ValueError, TypeError, IndexError):
            pass
        theta = math.radians(rotation)
        reference = ""
        for text in footprint.all("fp_text"):
            if len(text.values) >= 2 and text.values[0] == "reference":
                reference = text.values[1]
                break
        if not reference:
            for prop in footprint.all("property"):
                if len(prop.values) >= 2 and prop.values[0] == "Reference":
                    reference = prop.values[1]
                    break
        for pad in footprint.all("pad"):
            if not pad.values:
                continue
            number = str(pad.values[0])
            try:
                dx = float(pad.first("at").values[0])
                dy = float(pad.first("at").values[1])
            except (AttributeError, ValueError, TypeError, IndexError):
                dx, dy = 0.0, 0.0
            # KiCad footprint angles rotate pad offsets clockwise in the
            # y-down board coordinates: verified on 326 rotated SMD pads of
            # the reference board (320 snap to same-net copper under this
            # convention vs 0 under the CCW-math sign). The wrong sign
            # displaces every rotated pad by twice its local offset.
            x = fx + dx * math.cos(theta) + dy * math.sin(theta)
            y = fy - dx * math.sin(theta) + dy * math.cos(theta)
            layers_node = pad.first("layers")
            layers = _pad_layer_set(layers_node.values if layers_node else [], copper_layers)
            size = 0.3
            size_node = pad.first("size")
            if size_node and len(size_node.values) >= 2:
                try:
                    size = math.hypot(float(size_node.values[0]), float(size_node.values[1]))
                except (ValueError, TypeError):
                    pass
            net_node = pad.first("net")
            pad_type = str(pad.values[1]) if len(pad.values) >= 2 else ""
            drill_node = pad.first("drill")
            try:
                drill = float(drill_node.values[0]) if drill_node and drill_node.values else 0.0
            except (ValueError, TypeError):
                drill = 0.0
            pads.append({"reference": reference, "pin": number, "net": net_node.values[1]
                         if net_node and len(net_node.values) >= 2 else "",
                         "x": x, "y": y, "layers": layers, "snap_mm": max(size / 2, 0.15),
                         "plated": pad_type != "np_thru_hole", "drill_mm": drill,
                         "tht": pad_type in ("thru_hole", "np_thru_hole"),
                         "uuid": pad.get("uuid", ""), "line": pad.line})
    return pads


def _point_in_polygon(point, pts):
    """Ray-casting containment; polygons are hole-free fill islands."""
    x, y = point
    inside, prev = False, pts[-1]
    for current in pts:
        if (current[1] > y) != (prev[1] > y):
            intersect = (prev[0] - current[0]) * (y - current[1]) / (prev[1] - current[1]) + current[0]
            if x < intersect:
                inside = not inside
        prev = current
    return inside


def _pad_pour_touches(pad, zones):
    """Filled-pour polygons containing this pad's copper, as (zone, layer, polygon).

    SMD pads are solid copper (center sample suffices); plated through-hole
    pads are sampled on a mid-annulus ring so the drilled hole is never
    mistaken for isolation. Only polygons on the pad's own net are tested, so
    a touch means shared pour copper, not proximity.
    """
    touches = set()
    drill = pad.get("drill_mm", 0.0) or 0.0
    snap = max(pad.get("snap_mm", 0.15), 0.15)
    if pad.get("tht") and drill > 0:
        radius = min(max((drill / 2 + snap) / 2, drill / 2 + 0.05), snap)
        samples = [(pad["x"], pad["y"])] + [
            (pad["x"] + radius * math.cos(i * math.pi / 12),
             pad["y"] + radius * math.sin(i * math.pi / 12)) for i in range(24)]
    else:
        radius = min(snap / 2, 0.3)
        samples = [(pad["x"], pad["y"])] + [
            (pad["x"] + radius * math.cos(i * math.pi / 4),
             pad["y"] + radius * math.sin(i * math.pi / 4)) for i in range(8)]
    for zone_index, zone in enumerate(zones):
        if zone["net"] != pad.get("net"):
            continue
        for layer in pad.get("layers", ()):
            for poly_index, polygon in enumerate(zone["polygons"]):
                if polygon["layer"] != layer:
                    continue
                min_x, min_y, max_x, max_y = polygon["bbox"]
                for sample in samples:
                    if not (min_x <= sample[0] <= max_x and min_y <= sample[1] <= max_y):
                        continue
                    if _point_in_polygon(sample, polygon["pts"]):
                        touches.add((zone_index, layer, poly_index))
                        break
    return touches


def _zone_fills(board, names):
    """Stored copper-pour fills: net name, layers, filled polygons with bboxes.

    The file's filled polygons are the filler's own output, so a pad whose
    copper lies inside one shares pour copper (thermal spokes included);
    clearance cutouts keep unconnected pads outside. A refill after board
    edits changes the islands, so results are per stored fill geometry.
    """
    zones = []
    for item in board.children:
        if item.tag != "zone" or item.first("keepout"):
            continue
        net = names.get(item.get("net"), item.get("net_name", ""))
        if not net:
            continue
        layers_node = item.first("layers")
        layers = list(layers_node.values) if layers_node else []
        polygons = []
        for poly in item.children:
            if not hasattr(poly, "tag") or poly.tag != "filled_polygon":
                continue
            layer_node = poly.first("layer")
            pts_node = poly.first("pts")
            if layer_node is None or pts_node is None or not layer_node.values:
                continue
            pts = []
            for xy in pts_node.children:
                if hasattr(xy, "tag") and xy.tag == "xy" and len(xy.values) >= 2:
                    try:
                        pts.append((float(xy.values[0]), float(xy.values[1])))
                    except (ValueError, TypeError):
                        continue
            if len(pts) >= 3:
                xs = [pt[0] for pt in pts]
                ys = [pt[1] for pt in pts]
                polygons.append({"layer": layer_node.values[0], "pts": pts,
                                 "bbox": (min(xs), min(ys), max(xs), max(ys))})
        if polygons:
            zones.append({"net": net, "layers": layers, "polygons": polygons})
    return zones


def extract_copper(board):
    """Drawn segments, arcs, vias and pads per net, plus coverage limits."""
    names = {n.values[0]: n.values[1] for n in board.all("net") if len(n.values) >= 2}
    copper_layers = _layers_in_order(board)
    nets = defaultdict(lambda: {"segments": [], "arcs": [], "vias": [], "pads": [],
                                "pour": False, "truncated": False})
    unsupported = 0
    for item in board.children:
        tag = item.tag
        if tag == "zone" and not item.first("keepout"):
            net = names.get(item.get("net"), item.get("net_name", ""))
            if net:
                nets[net]["pour"] = True
            continue
        if tag == "via":
            at = point(item.first("at"))
            net = names.get(item.get("net"), "")
            if not at or not net:
                unsupported += 1
                continue
            try:
                size = float(item.get("size", 0) or 0)
            except (ValueError, TypeError):
                size = 0
            layers_node = item.first("layers")
            declared = layers_node.values if layers_node else []
            stacked = [layer for layer in declared if layer in copper_layers]
            nets[net]["vias"].append({"at": list(at), "size_mm": size, "net": net,
                                      "layers": stacked, "declared_layers": declared,
                                      "uuid": item.get("uuid", ""), "line": item.line})
            if not stacked:
                unsupported += 1
            continue
        if tag == "arc":
            start = point(item.first("start"))
            mid = point(item.first("mid"))
            end = point(item.first("end"))
            net = names.get(item.get("net"), "")
            try:
                width = float(item.get("width"))
            except (ValueError, TypeError):
                width = 0
            if not start or not end or not mid or not net or width <= 0:
                unsupported += 1
                continue
            nets[net]["arcs"].append({"start": list(start), "mid": list(mid), "end": list(end),
                                      "width_mm": width, "layer": item.get("layer"),
                                      "length_mm": _arc_length(start, mid, end),
                                      "uuid": item.get("uuid", ""), "line": item.line})
            continue
        if tag != "segment":
            continue
        start = point(item.first("start"))
        end = point(item.first("end"))
        net = names.get(item.get("net"), "")
        try:
            width = float(item.get("width"))
        except (ValueError, TypeError):
            width = 0
        if not start or not end or start == end or not net or width <= 0:
            unsupported += 1
            continue
        nets[net]["segments"].append({"start": list(start), "end": list(end),
                                      "width_mm": width, "layer": item.get("layer"),
                                      "uuid": item.get("uuid", ""), "line": item.line})
    for pad in _pad_centers(board, copper_layers):
        net_id = pad["net"]
        net = names.get(net_id, net_id) if net_id else ""
        if net and pad["layers"]:
            entry = dict(pad)
            entry["net"] = net
            nets[net]["pads"].append(entry)
        elif net:
            unsupported += 1
    for entry in nets.values():
        if len(entry["segments"]) + len(entry["arcs"]) > MAX_SEGMENTS_PER_NET:
            entry["segments"] = entry["segments"][:MAX_SEGMENTS_PER_NET]
            entry["arcs"] = []
            entry["truncated"] = True
    return {"nets": dict(nets), "layers": copper_layers, "unsupported_items": unsupported,
            "zones": _zone_fills(board, names),
            "limits": ["Via barrel height is excluded from routed lengths; layer transitions are recorded separately.",
                        "Copper pours are verified as areas from the stored fill geometry: islands whose pads or vias share one filled polygon (including via stitching across layers) are joined by the pour; no path length is claimed through pour copper. Results follow the stored fill: refill zones after editing the board.",
                        "Pad centers snap to touching same-net copper within the pad footprint; hidden internal plane connections are not modeled."]}


def _node_key(x, y, layer):
    return (round(x, 4), round(y, 4), layer)


def build_graph(items, copper_layers):
    """Node/edge graph for one net; pads and vias join layers and pieces."""
    nodes, adjacent = {}, defaultdict(list)

    def link(key_a, key_b, length_mm, kind):
        if key_a == key_b or length_mm < 0:
            return
        adjacent[key_a].append((key_b, length_mm, kind))
        adjacent[key_b].append((key_a, length_mm, kind))

    def ensure(key, pin=None):
        node = nodes.setdefault(key, {"pins": []})
        if pin and pin not in node["pins"]:
            node["pins"].append(pin)
        return node

    for seg in items["segments"]:
        key_a = _node_key(*seg["start"], seg["layer"])
        key_b = _node_key(*seg["end"], seg["layer"])
        ensure(key_a)
        ensure(key_b)
        link(key_a, key_b, math.dist(seg["start"], seg["end"]), "trace")
    for arc in items["arcs"]:
        chords = _arc_chords(arc["start"], arc["mid"], arc["end"])
        keys = [_node_key(*pt, arc["layer"]) for pt in [arc["start"]] + [c[1] for c in chords]]
        for key in keys:
            node = ensure(key)
            node["arc"] = True
        total = arc["length_mm"]
        chord_sum = sum(math.dist(a, b) for a, b in chords) or 1.0
        for (first, second), key_a, key_b in zip(chords, keys, keys[1:]):
            link(key_a, key_b, total * math.dist(first, second) / chord_sum, "arc")
    for via in items["vias"]:
        hub = ("via", round(via["at"][0], 4), round(via["at"][1], 4))
        ensure(hub)
        # Only the via's own start/end layers join; a blind F.Cu-In1 via never
        # touches B.Cu even though the board stack has it.
        for layer in via.get("layers", copper_layers):
            key = _node_key(via["at"][0], via["at"][1], layer)
            ensure(key)
            link(hub, key, 0.0, "via")
    points = [(key, (key[0], key[1])) for key in nodes if len(key) == 3 and key[0] != "via"]
    for pad in items["pads"]:
        for layer in sorted(pad["layers"]):
            key = _node_key(pad["x"], pad["y"], layer)
            ensure(key, (pad["reference"], pad["pin"]))
            for other, xy in points:
                if other[2] != layer:
                    continue
                if math.dist((pad["x"], pad["y"]), xy) <= pad["snap_mm"] + 1e-9:
                    link(key, other, math.dist((pad["x"], pad["y"]), xy), "pad")
    for pad in items["pads"]:
        # A plated through-hole barrel joins the pad's own copper layers at the
        # hole wall, so F.Cu copper reaching the pad continues onto B.Cu.
        # NPTH holes have no plating and must never join unrelated copper.
        if not pad.get("plated", True):
            continue
        barrel = [_node_key(pad["x"], pad["y"], layer) for layer in pad["layers"]
                  if _node_key(pad["x"], pad["y"], layer) in nodes]
        for key in barrel[1:]:
            link(barrel[0], key, 0.0, "barrel")
    return {"nodes": nodes, "adjacent": dict(adjacent)}


def _shortest(adjacent, origin):
    return _shortest_multi(adjacent, [origin])


def _shortest_multi(adjacent, origins):
    """Shortest copper from any of a pin's layer-nodes (THT pads span layers).

    Heap entries carry a monotonic counter so tied costs never compare node
    keys: via-hub keys are strings while coordinate keys are tuples, and
    zero-length barrel edges make exact ties routine.
    """
    dist = {node: 0.0 for node in origins}
    counter = 0
    heap = []
    for node in origins:
        heappush(heap, (0.0, counter, node))
        counter += 1
    while heap:
        cost, _, node = heappop(heap)
        if cost > dist[node]:
            continue
        for peer, length, _kind in adjacent.get(node, ()):
            total = cost + length
            if total < dist.get(peer, math.inf) - 1e-12:
                dist[peer] = total
                heappush(heap, (total, counter, peer))
                counter += 1
    return dist


def _islands(graph):
    """Connected copper components over every edge kind (traces, arcs, vias, pads)."""
    seen, islands = {}, []
    for node in graph["nodes"]:
        if node in seen:
            continue
        group = len(islands)
        stack = [node]
        seen[node] = group
        while stack:
            current = stack.pop()
            for peer, _length, _kind in graph["adjacent"].get(current, ()):
                if peer not in seen:
                    seen[peer] = group
                    stack.append(peer)
        islands.append(group)
    return seen


SERIES_REF = re.compile(r"(?i)^(?:R|C|L|FB|FERRITE)\d")
# DC-conducting series parts: resistors, inductors and ferrite beads are
# ohmic paths between their pads. Capacitors are NOT: a decoupling cap from
# a rail to ground must never merge those nets, and a series (AC-coupling)
# cap blocks DC even though it passes signal. Only DC_BRIDGE_REF parts may
# join islands or net names; C parts still get DNP/open classification.
DC_BRIDGE_REF = re.compile(r"(?i)^(?:R|L|FB|FERRITE)\d")


def _component_value(project, ref):
    if project is None:
        return "", False
    component = project.components.get(ref)
    if component is None:
        return "", False
    for record in component.schematic + component.pcb:
        if record.dnp:
            return (record.value or "").strip(), True
    for record in component.schematic + component.pcb:
        if (record.value or "").strip():
            return record.value.strip(), False
    return "", False


def series_net_links(copper, project):
    """DC-conducting two-terminal parts whose pads sit on different PCB nets.

    Returns one entry per DC_BRIDGE_REF reference (R/L/FB, never C) spanning
    two or more nets, flagged fitted or DNP from the project components.
    Capacitors block DC: a rail-to-ground decoupling cap must never merge
    those nets. Connectors and other multi-pin parts spanning nets are never
    links: their pins are separate copper by design.
    """
    by_ref = defaultdict(lambda: defaultdict(list))
    for name, items in copper["nets"].items():
        for pad in items["pads"]:
            ref = pad.get("reference", "")
            if ref:
                by_ref[ref][name].append(pad)
    links = []
    for ref in sorted(by_ref):
        nets = sorted(by_ref[ref])
        if len(nets) < 2 or not DC_BRIDGE_REF.match(ref or ""):
            continue
        value, dnp = _component_value(project, ref)
        links.append({"reference": ref, "value": value, "nets": nets, "fitted": not dnp})
    return links


def series_net_sets(copper, project, fitted_only=True):
    """Union of net names joined by series parts (fitted, or any when False).

    Fitted sets are electrical nets: complete routes may hop the part.
    Including DNP links as well keeps the copper view whole so an unplaced
    part reads as an explicit open by design intent instead of vanishing.
    """
    parent = {}

    def find(net):
        while parent[net] != net:
            parent[net] = parent[parent[net]]
            net = parent[net]
        return net

    for link in series_net_links(copper, project):
        if fitted_only and not link["fitted"]:
            continue
        for net in link["nets"]:
            parent.setdefault(net, net)
        root = find(link["nets"][0])
        for net in link["nets"][1:]:
            parent[find(net)] = root
    groups = defaultdict(list)
    for net in parent:
        groups[find(net)].append(net)
    return sorted(sorted(members) for members in groups.values() if len(members) > 1)


def electrical_net_groups(copper, project):
    """Net names joined into one electrical net by fitted series parts."""
    return series_net_sets(copper, project, fitted_only=True)


def merged_group_items(copper, group):
    """One copper view for a net set: segments, arcs, vias and pads joined.

    Pads keep their own net names; pin identity is (reference, pin), so
    topology can hop a fitted part across the set's net names.
    """
    merged = {"segments": [], "arcs": [], "vias": [], "pads": [],
              "pour": False, "truncated": False}
    for net in group:
        items = copper["nets"].get(net)
        if not items:
            continue
        for key in ("segments", "arcs", "vias", "pads"):
            merged[key].extend(items[key])
        merged["pour"] = merged["pour"] or items["pour"]
        merged["truncated"] = merged["truncated"] or items["truncated"]
    return merged


def _via_touch_points(via):
    """A via as a synthetic pad for pour-touch sampling (annulus ring)."""
    size = max(via.get("size_mm", 0.8) or 0.8, 0.2)
    return {"x": via["at"][0], "y": via["at"][1], "layers": via.get("layers", ()),
            "net": via.get("net", ""), "drill_mm": size / 2, "snap_mm": size / 2,
            "tht": True, "plated": True}


def _pour_join_sets(graph, island_of, pads, vias, zones):
    """Union islands whose pads or vias share one filled pour polygon.

    Stitching vias join pours across layers and across polygons, so both
    pads and vias link their islands to the polygons they touch. Returns
    island id -> union root plus island id -> touch descriptions
    (zone net, layer, polygon index). Barrel/via-hub cross-layer joining is
    already in the island graph.
    """
    touch_owner = {}
    entries = list(pads or [])
    for via in vias or []:
        if isinstance(via, dict) and via.get("at"):
            entries.append(_via_touch_points(via))
    for pad in entries:
        touches = _pad_pour_touches(pad, zones or [])
        if not touches:
            continue
        for zone_index, layer, poly_index in touches:
            if layer not in pad.get("layers", ()):
                continue
            key = _node_key(pad["x"], pad["y"], layer)
            if key not in graph["nodes"]:
                continue
            touch_owner.setdefault((zone_index, layer, poly_index), set()).add(island_of[key])
    parent = {}

    def find(node):
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for owners in touch_owner.values():
        owners = list(owners)
        for other in owners[1:]:
            parent[find(owners[0])] = find(other)
    roots = {node: find(node) for owners in touch_owner.values() for node in owners}
    zones = zones or []
    touches = {}
    for (zone_index, layer, poly_index), owners in touch_owner.items():
        net = zones[zone_index]["net"] if zone_index < len(zones) else ""
        for node in owners:
            touches.setdefault(node, set()).add((net, layer, poly_index))
    return roots, touches


def pin_topology(graph, pads, project=None, zones=None, vias=None):
    """Pin-to-pin connectivity with island hops through fitted parts.

    Returns direct copper pairs plus, for separated pins, either a pour
    verification (shared stored fill copper), a route through fitted series
    components (with per-leg copper lengths), or an explicit open
    classification. Nothing unreachable stays silent.
    """
    island_of = _islands(graph)
    pin_nodes = defaultdict(list)
    for key, node in graph["nodes"].items():
        for pin in node["pins"]:
            pin_nodes[pin].append(key)
    pins = sorted(pin_nodes)
    truncated = len(pins) > MAX_PINS_PER_NET
    pins = pins[:MAX_PINS_PER_NET]
    # Which islands each component reference touches (via its copper pads).
    ref_islands = defaultdict(set)
    ref_pad_nodes = defaultdict(list)
    for pad in pads:
        ref = pad.get("reference", "")
        if not ref:
            continue
        for layer in pad.get("layers", ()):
            key = _node_key(pad["x"], pad["y"], layer)
            if key in graph["nodes"]:
                ref_islands[ref].add(island_of[key])
                ref_pad_nodes[(ref, pad.get("pin", ""))].append(key)
    # Island adjacency only through fitted series parts. Connectors, NPTH
    # mounting holes and other multi-island references are reported as
    # bridges for evidence, but they never conduct between islands: only a
    # fitted R/C/L/FB part is a known electrical path through its body.
    island_links = defaultdict(list)
    bridges = []
    for ref, touched in ref_islands.items():
        if len(touched) < 2:
            continue
        value, dnp = _component_value(project, ref)
        kind = ("series" if SERIES_REF.match(ref or "") else "component-internal")
        bridges.append({"reference": ref, "value": value, "fitted": not dnp,
                        "kind": kind, "islands": sorted(touched)})
        if not dnp and DC_BRIDGE_REF.match(ref or ""):
            touched = sorted(touched)
            for first, second in zip(touched, touched[1:]):
                island_links[first].append((second, ref, value))
                island_links[second].append((first, ref, value))
    pin_island = {}
    island_roster = defaultdict(list)
    for pin in pins:
        islands = {island_of[key] for key in pin_nodes[pin]}
        pin_island[pin] = min(islands)
        island_roster[min(islands)].append(pin)
    for roster in island_roster.values():
        roster.sort()
    # Copper pairs keep full pairwise detail (they size spans and tuning).
    # Opens are reported once per island pair instead: every pin pair across
    # the same two copper islands carries the same underlying fact, so the
    # old pin x pin listing was pure combinatorial noise (130 rows on one
    # power net). Component pads are internal to their parts, so each island
    # is represented by a non-component pin where one exists.
    multi_island_refs = {ref for ref, touched in ref_islands.items() if len(touched) > 1}

    def representative(island):
        roster = island_roster[island]
        for pin in roster:
            if pin[0] not in multi_island_refs:
                return pin
        return roster[0]

    pairs = []
    for index, first in enumerate(pins):
        dist = _shortest_multi(graph["adjacent"], pin_nodes[first])
        for second in pins[index + 1:]:
            best = min((dist.get(key, math.inf) for key in pin_nodes[second]), default=math.inf)
            if math.isfinite(best):
                pairs.append({"pins": [list(first), list(second)], "length_mm": round(best, 4)})
            if len(pairs) >= MAX_PAIR_ROWS:
                truncated = True
                break
        if truncated:
            break
    sizes = {island: len(roster) for island, roster in island_roster.items()}
    main = min(island_roster, key=lambda island: (-sizes[island], island)) if island_roster else None
    pour_roots, pour_touches = _pour_join_sets(graph, island_of, pads, vias, zones)
    pour_checked_nets = {zone["net"] for zone in zones or []}
    disconnected, pour_joined = [], []
    for island in sorted(island_roster):
        if island == main:
            continue
        first, second = sorted((representative(island), representative(main)))
        origin, target = pin_island[first], pin_island[second]
        if (origin in pour_roots and pour_roots.get(target) == pour_roots[origin]
                and pour_roots.get(target) is not None):
            evidence = sorted({f"{net} {layer} fill {poly}"
                               for net, layer, poly in pour_touches.get(origin, set())
                               | pour_touches.get(target, set())})
            pour_joined.append({"pins": [list(first), list(second)],
                                "detail": ("islands share stored pour copper: "
                                           + "; ".join(evidence))})
            continue
        route = _island_route(graph, pin_nodes[first][0], pin_nodes[second],
                              origin, target, island_links,
                              ref_pad_nodes, island_of)
        if route is not None:
            pairs.append({"pins": [list(first), list(second)],
                          "length_mm": round(route["copper_mm"], 4),
                          "through": route["hops"]})
        else:
            pour_checked = bool(pour_checked_nets & {pad_net(pin, pads) for pin in (first, second)})
            disconnected.append({"pins": [list(first), list(second)],
                                 "reason": _open_reason(first, second, pin_island,
                                                         ref_islands, island_roster, project,
                                                         pour_checked)})
        if len(pairs) + len(disconnected) >= MAX_PAIR_ROWS:
            truncated = True
            break
    span = max((row["length_mm"] for row in pairs), default=0.0)
    return {"pins": len(pin_nodes), "span_mm": round(span, 4), "pairs": pairs,
            "unreachable": [[list(a), list(b)] for a, b in
                            (d["pins"] for d in disconnected)],
            "disconnected": disconnected, "pour_joined": pour_joined,
            "truncated": truncated,
            "island_count": len({island_of[k] for k in graph["nodes"]}),
            "bridges": bridges}


def _island_route(graph, origin, targets, origin_island, target_island, island_links,
                  ref_pad_nodes, island_of):
    """Shortest island path between separated pins; None when truly open."""
    if origin_island == target_island:
        return None
    previous, queue = {origin_island: None}, [origin_island]
    while queue:
        current = queue.pop(0)
        if current == target_island:
            break
        for peer, _ref, _value in island_links.get(current, ()):
            if peer not in previous:
                previous[peer] = current
                queue.append(peer)
    if target_island not in previous:
        return None
    chain, current = [], target_island
    while current is not None:
        chain.append(current)
        current = previous[current]
    chain.reverse()
    # Copper legs run inside one island at a time; each component hop resumes
    # from that same part's pads in the next island.
    legs, hops = [], []
    entries = [origin]
    for first, second in zip(chain, chain[1:]):
        link = next((peer for peer in island_links.get(first, []) if peer[0] == second), None)
        if link is None:
            return None
        _peer, ref, value = link
        exits = [key for (r, _p), keys in ref_pad_nodes.items() if r == ref
                 for key in keys if island_of.get(key) == first]
        landings = [key for (r, _p), keys in ref_pad_nodes.items() if r == ref
                    for key in keys if island_of.get(key) == second]
        if not exits or not landings:
            return None
        best = math.inf
        for entry in entries:
            dist = _shortest(graph["adjacent"], entry)
            best = min([best] + [dist.get(key, math.inf) for key in exits])
        if not math.isfinite(best):
            return None
        legs.append(best)
        hops.append({"reference": ref, "value": value})
        entries = landings
    best = math.inf
    for entry in entries:
        dist = _shortest(graph["adjacent"], entry)
        best = min([best] + [dist.get(key, math.inf) for key in targets])
    if not math.isfinite(best):
        return None
    legs.append(best)
    return {"copper_mm": sum(legs), "hops": hops}


def pad_net(pin, pads):
    """Net name of a (reference, pin) pad, or empty when not present."""
    for pad in pads:
        if (pad.get("reference"), pad.get("pin")) == (pin[0], pin[1]):
            return pad.get("net", "")
    return ""


def _open_reason(first, second, pin_island, ref_islands, island_roster, project, pour_checked=False):
    """Classify a no-copper-path pair: DNP part, missing part, or true open."""
    roster = lambda pin: ", ".join(f"{r}-{p}" for r, p in island_roster.get(pin_island[pin], [])[:6])
    if pour_checked:
        suffix = "stored pour fill checked: separate fill islands"
    else:
        suffix = "pour or internal-plane connections are not modeled"
    for ref, touched in ref_islands.items():
        if pin_island[first] in touched and pin_island[second] in touched:
            _value, dnp = _component_value(project, ref)
            if dnp:
                return (f"series part {ref} is unplaced (DNP): the copper is open by design intent "
                        f"({roster(first)} vs {roster(second)})")
            return (f"copper open between {first[0]}-{first[1]} [{roster(first)}] and "
                    f"{second[0]}-{second[1]} [{roster(second)}]; nearest shared reference {ref} "
                    f"has no usable bridge ({suffix})")
    return (f"copper open between {first[0]}-{first[1]} [{roster(first)}] and "
            f"{second[0]}-{second[1]} [{roster(second)}]; no fitted part joins these islands "
            f"({suffix})")


def _drawn_length(items):
    """Total drawn copper on the net: every segment plus every arc at full
    analytic length. Branches and serpentines count; this is not a route."""
    total = sum(math.dist(s["start"], s["end"]) for s in items["segments"])
    return round(total + sum(a["length_mm"] for a in items["arcs"]), 4)


def _turn_angles(items, graph):
    """Interior bend angles at trace-trace joints; arcs are smooth by construction.

    Each bend carries its shorter incident leg length so fillet-scale joints
    (teardrops, junction fillers with sub-0.5 mm legs) can be told apart from
    genuine routing bends.
    """
    bends = []
    for key, node in graph["nodes"].items():
        if len(key) != 3 or key[0] == "via" or node.get("arc") or node.get("pins"):
            continue
        edges = [(peer, length) for peer, length, kind in graph["adjacent"].get(key, [])
                 if kind == "trace"]
        if len(edges) != 2:
            continue
        (first, len_a), (second, len_b) = edges
        incoming = (key[0] - first[0], key[1] - first[1])
        outgoing = (second[0] - key[0], second[1] - key[1])
        norm = math.hypot(*incoming) * math.hypot(*outgoing)
        if norm < 1e-12:
            continue
        # Deflection is the direction change; interior is its supplement.
        # Straight copper has 0 deflection and 180 interior.
        deflection = math.degrees(math.acos(max(-1.0, min(1.0,
            (incoming[0] * outgoing[0] + incoming[1] * outgoing[1]) / norm))))
        interior = 180.0 - deflection
        bends.append({"point_mm": [round(key[0], 4), round(key[1], 4)], "layer": key[2],
                      "interior_deg": round(interior, 1),
                      "leg_mm": round(min(len_a, len_b), 4)})
    return bends


def _reportable_bends(items, graph):
    """Acute bends on substantial legs; fillet-scale joints are counted, not listed."""
    reportable, fillets = [], 0
    for bend in _turn_angles(items, graph):
        if bend["interior_deg"] >= ACUTE_TURN_DEG:
            continue
        if bend["leg_mm"] < MIN_BEND_LEG_MM:
            fillets += 1
        else:
            reportable.append(bend)
    return reportable, fillets


def _pair_base(name):
    """Return (base, leg) when a net name follows a generic pair convention."""
    up = name.upper()
    if AUTO_NET.match(name):
        return None
    match = re.fullmatch(r"(.*?)[_\-+](P|N|M|PLUS|MINUS)$", up)
    if match and len(match.group(1)) >= 1:
        return match.group(1), ("P" if match.group(2) in ("P", "PLUS") else "N")
    match = re.fullmatch(r"(.*?)[_\-](DP|DM)$", up)
    if match and len(match.group(1)) >= 1:
        return match.group(1), ("P" if match.group(2) == "DP" else "N")
    match = re.fullmatch(r"(.+)([+-])$", up)
    if match:
        return match.group(1), ("P" if match.group(2) == "+" else "N")
    return None


def find_diff_pairs(net_names):
    """Group nets into differential pairs by generic naming conventions.

    Power, ground and auto-generated names never pair. SDA/SCL style
    single-ended buses do not match any convention and stay unpaired.
    """
    legs = defaultdict(dict)
    for name in net_names:
        parsed = _pair_base(name)
        if not parsed:
            continue
        base, leg = parsed
        if base in POWER_BASE or name.upper() in POWER_BASE:
            continue
        legs[base][leg] = name
    return sorted(([legs[base]["P"], legs[base]["N"]] for base in legs
                   if set(legs[base]) == {"P", "N"}))


def _min_gap(leg_a, leg_b):
    """Minimum edge-to-edge gap between two legs' drawn segments (bounded sample)."""
    segs_a = (leg_a["segments"] + [dict(s, start=c[0], end=c[1])
                                   for arc in leg_a["arcs"] for c in _arc_chords(arc["start"], arc["mid"], arc["end"])])[:MAX_GAP_SAMPLES]
    segs_b = (leg_b["segments"] + [dict(s, start=c[0], end=c[1])
                                   for arc in leg_b["arcs"] for c in _arc_chords(arc["start"], arc["mid"], arc["end"])])[:MAX_GAP_SAMPLES]
    best = math.inf
    for first in segs_a:
        if first["layer"] is None:
            continue
        for second in segs_b:
            if second["layer"] != first["layer"]:
                continue
            gap = _segment_gap(first, second)
            if gap < best:
                best = gap
    return None if best is math.inf else round(max(0.0, best), 4)


def _segment_gap(first, second):
    ax, ay, bx, by = first["start"] + first["end"]
    cx, cy, dx, dy = second["start"] + second["end"]
    best = min(math.dist((ax, ay), (cx, cy)), math.dist((ax, ay), (dx, dy)),
               math.dist((bx, by), (cx, cy)), math.dist((bx, by), (dx, dy)))
    best = min(best, _point_segment(cx, cy, ax, ay, bx, by), _point_segment(dx, dy, ax, ay, bx, by),
               _point_segment(ax, ay, cx, cy, dx, dy), _point_segment(bx, by, cx, cy, dx, dy))
    return best - (first["width_mm"] + second["width_mm"]) / 2


def _point_segment(px, py, ax, ay, bx, by):
    length2 = (bx - ax) ** 2 + (by - ay) ** 2
    if length2 < 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / length2))
    return math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)))


def _parallel_run(leg_a, leg_b):
    """Longest same-layer parallel overlap between the legs (screening only)."""
    tracks_a = [dict(start=s["start"], end=s["end"], width_mm=s["width_mm"],
                     net="A", layer=s["layer"]) for s in leg_a["segments"]]
    tracks_b = [dict(start=s["start"], end=s["end"], width_mm=s["width_mm"],
                     net="B", layer=s["layer"]) for s in leg_b["segments"]]
    best = 0.0
    for first in tracks_a[:500]:
        for second in tracks_b[:500]:
            pair = parallel_pair(first, second)
            if pair and pair["parallel_mm"] > best:
                best = pair["parallel_mm"]
    return round(best, 4)


def _widths(items):
    widths = sorted(s["width_mm"] for s in items["segments"]
                    + [dict(width_mm=a["width_mm"]) for a in items["arcs"]])
    if not widths:
        return None, None
    return widths[0], widths[len(widths) // 2]


def _leg_topology(name, copper, project, merge_sets):
    """Topology for one net: merged across its series-joined net set, if any.

    Fitted series parts (R4/R5 style) join net names into one electrical net,
    so routes hop the part instead of stopping at the name boundary. DNP
    links merge the copper view too, reading as explicit opens by intent.
    """
    for group in merge_sets:
        if name in group:
            merged = merged_group_items(copper, group)
            graph = build_graph(merged, copper["layers"])
            return pin_topology(graph, merged["pads"], project, copper.get("zones"),
                                merged["vias"]), group
    items = copper["nets"][name]
    graph = build_graph(items, copper["layers"])
    return pin_topology(graph, items["pads"], project, copper.get("zones"),
                        items["vias"]), [name]


def _usb_fs_controller(project, nets):
    """Reference of an RP2040-class (FS-only USB device) part driving nets.

    USB data usually reaches the connector through series resistors, so one hop
    through a two-pin part is followed before concluding the controller is
    absent. Only direct membership or a single passive hop counts.
    """
    if project is None:
        return None
    schematic_nets = getattr(project, 'schematic_nets', None) or {}
    pin_net, ref_pins = {}, {}
    for net, endpoints in schematic_nets.items():
        for endpoint in endpoints or []:
            pin_net[endpoint] = net
            if '.' in endpoint:
                ref, _, pin = endpoint.partition('.')
                ref_pins.setdefault(ref, set()).add(pin)
    targets = set(nets)
    refs_on_nets = set()
    for net, endpoints in schematic_nets.items():
        if net in targets:
            for endpoint in endpoints or []:
                refs_on_nets.add(endpoint.split('.')[0])
    passive = lambda ref: re.sub(r"[0-9]+$", "", ref or "").upper() in {"R", "C", "L"}
    two_pin_refs = {ref for ref, pins in ref_pins.items() if len(pins) == 2 and passive(ref)}
    for ref in sorted(refs_on_nets):
        if ref in two_pin_refs:
            for pin in ref_pins[ref]:
                neighbor = pin_net.get(f"{ref}.{pin}")
                if neighbor and neighbor not in targets:
                    for endpoint in schematic_nets.get(neighbor, []):
                        refs_on_nets.add(endpoint.split('.')[0])
    for ref in sorted(refs_on_nets):
        if ref in two_pin_refs:
            continue
        component = project.components.get(ref)
        if component and any('RP2040' in (record.value or '').upper()
                             for record in component.schematic + component.pcb):
            return ref
    return None


def pair_skew_assessment(name_p, name_n, mismatch_mm, project=None):
    """Speed-aware skew verdict for a measured pair mismatch.

    Unknown interfaces keep the generic budget message. USB D+/D- driven by an
    FS-only controller gets a numeric screening verdict with the calculation
    shown; the screening bound is a reason to check, not a compliance limit.
    """
    skew_ps = round(mismatch_mm * PS_PER_MM, 1)
    scale = f"about {skew_ps:g} ps at the {PS_PER_MM:g} ps/mm screening assumption"
    names = {name_p.strip().upper(), name_n.strip().upper()}
    if names <= _USB_DM_NAMES:
        controller = _usb_fs_controller(project, (name_p, name_n))
        if controller:
            bound = USB_FS_SKEW_SCREEN_PS
            basis = (f"USB 2.0 full-speed via {controller} (12 Mbps device; "
                     f"FS rise/fall >= {USB_FS_MIN_RISE_NS:g} ns)")
            if skew_ps <= bound:
                return (f"Interface: {basis}. Skew {mismatch_mm} mm ({scale}) is within "
                        f"the {bound:g} ps screening bound (10% of minimum rise time): "
                        f"no length tuning is supported by these numbers.",
                        "No length tuning is indicated. Keep the series termination and "
                        "re-check only if the interface speed or controller changes.")
            return (f"Interface: {basis}. Skew {mismatch_mm} mm ({scale}) exceeds "
                    f"the {bound:g} ps screening bound (10% of minimum rise time): "
                    f"confirm against the validated interface timing before adjusting tuning.",
                    "Confirm the measured skew against the validated interface timing; "
                    "adjust tuning only if that budget requires it.")
    if mismatch_mm <= MISMATCH_REVIEW_MM:
        return ("Mismatch within the 1 mm screening bound; no routing action is supported "
                f"by these numbers. ({scale}.)",
                "No action is supported by these numbers.")
    return (f"Mismatch {mismatch_mm} mm ({scale}) exceeds the 1 mm screening bound: compare "
            "against the interface skew budget before adjusting tuning. "
            "This measurement alone does not prove a functional failure.",
            "Compare the measured mismatch against the interface skew budget; adjust tuning "
            "only if the budget requires it. Check the longest parallel run and gap against "
            "the stackup rules for this interface.")


def analyze_pair(name_p, name_n, copper, project=None):
    """Measured facts for one differential pair; judgments stay separate."""
    leg_p, leg_n = copper["nets"][name_p], copper["nets"][name_n]
    merge_sets = series_net_sets(copper, project, fitted_only=False)
    paths_p, group_p = _leg_topology(name_p, copper, project, merge_sets)
    paths_n, group_n = _leg_topology(name_n, copper, project, merge_sets)
    mismatch = round(abs(paths_p["span_mm"] - paths_n["span_mm"]), 4)
    min_p, med_p = _widths(leg_p)
    min_n, med_n = _widths(leg_n)
    bends_p, fillets_p = _reportable_bends(leg_p, build_graph(leg_p, copper["layers"]))
    bends_n, fillets_n = _reportable_bends(leg_n, build_graph(leg_n, copper["layers"]))
    bends = ([dict(b, net=name_p) for b in bends_p]
             + [dict(b, net=name_n) for b in bends_n])
    fillets = fillets_p + fillets_n
    measurement = {
        "pair": [name_p, name_n],
        "electrical_p": group_p, "electrical_n": group_n,
        "span_p_mm": paths_p["span_mm"], "span_n_mm": paths_n["span_mm"],
        "drawn_p_mm": _drawn_length(leg_p), "drawn_n_mm": _drawn_length(leg_n),
        "mismatch_mm": mismatch,
        "pins_p": paths_p["pins"], "pins_n": paths_n["pins"],
        "islands_p": paths_p["island_count"], "islands_n": paths_n["island_count"],
        "bridges_p": paths_p["bridges"], "bridges_n": paths_n["bridges"],
        "disconnected_p": paths_p["disconnected"], "disconnected_n": paths_n["disconnected"],
        "vias_p": len(leg_p["vias"]), "vias_n": len(leg_n["vias"]),
        "layers_p": sorted({s["layer"] for s in leg_p["segments"]} | {a["layer"] for a in leg_p["arcs"]}),
        "layers_n": sorted({s["layer"] for s in leg_n["segments"]} | {a["layer"] for a in leg_n["arcs"]}),
        "min_width_p_mm": min_p, "med_width_p_mm": med_p,
        "min_width_n_mm": min_n, "med_width_n_mm": med_n,
        "min_gap_mm": _min_gap(leg_p, leg_n),
        "parallel_run_mm": _parallel_run(leg_p, leg_n),
        "acute_bends": bends[:MAX_EVIDENCE_BENDS],
        "acute_bends_total": len(bends),
        "fillet_joints_excluded": fillets,
        "paths_p": {"pairs": paths_p["pairs"], "disconnected": paths_p["disconnected"]},
        "paths_n": {"pairs": paths_n["pairs"], "disconnected": paths_n["disconnected"]},
        "pour": leg_p["pour"] or leg_n["pour"],
        "truncated": paths_p["truncated"] or paths_n["truncated"] or leg_p["truncated"] or leg_n["truncated"],
    }
    judgment, guidance = pair_skew_assessment(name_p, name_n, mismatch, project)
    measurement["skew_ps"] = round(mismatch * PS_PER_MM, 1)
    measurement["interface"] = judgment.split(".")[0] if judgment.startswith("Interface:") else "unidentified"
    return measurement, judgment, guidance


def _hop_text(hop):
    value = f" {hop['value']}" if hop.get("value") else ""
    return f"{hop['reference']}{value}"


def _route_lines(paths):
    """Complete-route and open-pair lines: copper legs plus component hops."""
    lines = []
    for row in sorted(paths["pairs"], key=lambda r: -r["length_mm"])[:5]:
        first, second = row["pins"]
        if "through" in row:
            hops = " -> ".join(_hop_text(h) for h in row["through"])
            lines.append(f"Route {first[0]}-{first[1]} to {second[0]}-{second[1]}: "
                         f"{row['length_mm']} mm copper through {hops}.")
        elif row["length_mm"] == max(r["length_mm"] for r in paths["pairs"]):
            lines.append(f"Longest direct copper span {first[0]}-{first[1]} to "
                         f"{second[0]}-{second[1]}: {row['length_mm']} mm.")
    for item in paths["disconnected"][:5]:
        first, second = item["pins"]
        lines.append(f"No copper path {first[0]}-{first[1]} to {second[0]}-{second[1]}: "
                     f"{item['reason']}.")
    return lines


def analyze_routing(project, source, pcb_tree=None):
    """Measure drawn routing, file review issues, and rebuild the review.

    pcb_tree is a pre-parsed tree from digest-verified bytes, shared with the
    signal-path pass to avoid re-reading and re-parsing the same file. The
    tree is only read, never mutated, so sharing it cannot change results.
    """
    project.issues = [i for i in project.issues if i.source != "routing"]
    relative = project.selected_files.get("pcb")
    digest = project.provenance.get("source_hashes", {}).get(relative)
    if not relative or not digest:
        project.routing = {"status": "unavailable", "message": "No verified PCB source is available."}
        return
    board = pcb_tree
    if board is None:
        try:
            raw = read_project_file(source, relative)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ArchiveError("PCB source changed since this check. Run preflight again.")
            board = parse(raw.decode("utf-8"), "kicad_pcb")
        except (ArchiveError, UnicodeError, ParseError) as exc:
            project.routing = {"status": "unavailable", "message": str(exc)}
            return
    copper = extract_copper(board)
    names = [name for name in copper["nets"] if not AUTO_NET.match(name)]
    pairs = find_diff_pairs(names)
    measurements, pair_names = [], set()
    for name_p, name_n in pairs:
        measurement, judgment, guidance = analyze_pair(name_p, name_n, copper, project)
        pair_names.update((name_p, name_n))
        pad_items = []
        for net in (name_p, name_n):
            for pad in copper["nets"][net]["pads"]:
                if pad["reference"] and re.fullmatch(r"[A-Za-z]+[0-9]+", pad["reference"]):
                    pad_items.append({"description":
                        f"Footprint {pad['reference']} pad {pad['pin']} ({net})"})
        mismatch_text = (f"routed spans {measurement['span_p_mm']} mm vs {measurement['span_n_mm']} mm "
                         f"(mismatch {measurement['mismatch_mm']} mm)")
        evidence = [
            f"{relative}: {name_p} {mismatch_text}; drawn copper {measurement['drawn_p_mm']} mm; "
            f"widths min/med {measurement['min_width_p_mm']}/{measurement['med_width_p_mm']} mm; "
            f"vias {measurement['vias_p']}; layers {','.join(measurement['layers_p'] or ['?'])}",
            f"{relative}: {name_n} spans {measurement['span_n_mm']} mm; drawn copper {measurement['drawn_n_mm']} mm; "
            f"widths min/med {measurement['min_width_n_mm']}/{measurement['med_width_n_mm']} mm; "
            f"vias {measurement['vias_n']}; layers {','.join(measurement['layers_n'] or ['?'])}",
            f"Pair gap min {measurement['min_gap_mm']} mm; longest parallel run {measurement['parallel_run_mm']} mm (screening only, not a coupling result).",
            f"Acute bends (<{ACUTE_TURN_DEG:g} deg interior): {measurement['acute_bends_total']} on pair nets. " + judgment,
        ]
        evidence += [f"{relative}: pair bend on {b['net']} interior {b['interior_deg']} deg at "
                     f"({b['point_mm'][0]}, {b['point_mm'][1]}) on {b['layer']}"
                     for b in measurement["acute_bends"][:5]]
        evidence += _route_lines(measurement["paths_p"])
        evidence += _route_lines(measurement["paths_n"])
        if measurement["pour"]:
            evidence.append("At least one leg has copper pours: trace spans are tuning lengths, not lowest-impedance-path claims.")
        if measurement["truncated"]:
            evidence.append("Measurement bounds were hit; counts and spans are partial.")
        project.issues.append(Issue(
            "warning", "ROUTING_DIFF_PAIR",
            (f"Differential pair {name_p}/{name_n}: {mismatch_text}. {judgment}"),
            evidence, source="routing", confidence=0.9, items=pad_items,
            suggested_fix=guidance,
            details={"pair": [name_p, name_n], "measurement": measurement}))
        measurements.append(measurement)
    bends, bend_nets, fillets = [], set(), 0
    net_table, open_labels = {}, []
    merge_sets = series_net_sets(copper, project, fitted_only=False)
    group_topo = {}

    def topology_for(name, items):
        for group in merge_sets:
            if name in group:
                key = tuple(group)
                if key not in group_topo:
                    merged = merged_group_items(copper, group)
                    built = build_graph(merged, copper["layers"])
                    group_topo[key] = (pin_topology(built, merged["pads"], project, copper.get("zones"),
                                                  merged["vias"]), built)
                topology, built = group_topo[key]
                return topology, group, built
        built = build_graph(items, copper["layers"])
        return pin_topology(built, items["pads"], project, copper.get("zones"),
                            items["vias"]), [name], built

    emitted_groups = set()
    pour_joined_total = 0
    truncated_labels = []
    for name in names:
        if name in pair_names:
            continue
        items = copper["nets"][name]
        if not items["segments"] and not items["arcs"]:
            continue
        topology, group, topo_graph = topology_for(name, items)
        # Bends stay per-net copper facts: a merged group graph would
        # duplicate bends across member nets and misattribute their net.
        graph = topo_graph if len(group) == 1 else build_graph(items, copper["layers"])
        net_table[name] = {"span_mm": topology["span_mm"],
                           "drawn_mm": _drawn_length(items),
                           "pins": topology["pins"],
                           "islands": topology["island_count"],
                           "electrical_nets": group,
                           "pour": items["pour"],
                           "bridges": topology["bridges"],
                           "disconnected": topology["disconnected"],
                           "pour_joined": topology.get("pour_joined", []),
                           "truncated": topology["truncated"]}
        pour_joined_total += len(topology.get("pour_joined", []))
        if topology.get("truncated"):
            truncated_labels.append(" + ".join(group) if len(group) > 1 else name)
        if topology["disconnected"]:
            label = " + ".join(group) if len(group) > 1 else name
            if label not in emitted_groups:
                emitted_groups.add(label)
                open_labels.append((label, topology["disconnected"]))
        reportable, skipped = _reportable_bends(items, graph)
        fillets += skipped
        for bend in reportable:
            bends.append({"net": name, **bend})
            bend_nets.add(name)
    if bends:
        shown = bends[:MAX_EVIDENCE_BENDS]
        project.issues.append(Issue(
            "warning", "ROUTING_ACUTE_BEND",
            (f"{len(bends)} acute copper bend(s) on {len(bend_nets)} non-pair net(s). "
             "Sharp interior corners are a fabrication etch concern, not an electrical defect; obtuse bends are normal and unreported."),
            [f"{relative}: {b['net']} interior {b['interior_deg']} deg at "
             f"({b['point_mm'][0]}, {b['point_mm'][1]}) on {b['layer']}" for b in shown] +
            (["Partial list; bounds were hit."] if len(bends) > MAX_EVIDENCE_BENDS else []),
            source="routing", confidence=0.9,
            suggested_fix="Confirm the bends against the fabricator's etch rules or widen them; no electrical change is implied."))
    measured_nets = len([n for n in names if copper["nets"][n]["segments"] or copper["nets"][n]["arcs"]])
    pair_fillet_note = ""
    if fillets + sum(m.get("fillet_joints_excluded", 0) for m in measurements):
        pair_fillet_note = (f" {fillets + sum(m.get('fillet_joints_excluded', 0) for m in measurements)} "
                            "fillet-scale joints were counted separately, not listed as bends.")
    open_lines = []
    for label, disconnected in sorted(open_labels)[:10]:
        for item in disconnected[:3]:
            first, second = item["pins"]
            open_lines.append(f"{relative}: {label}: no copper path {first[0]}-{first[1]} to "
                              f"{second[0]}-{second[1]}: {item['reason']}.")
    if len(open_labels) > 10:
        open_lines.append(f"{len(open_labels) - 10} further net(s) with open pin pairs not listed.")
    pour_note = (f" {pour_joined_total} island pair(s) verified joined by copper pour." if pour_joined_total else "")
    truncated_note = (f" Partial coverage: measurement bounds hit on "
                      f"{len(truncated_labels)} net(s) ({', '.join(sorted(set(truncated_labels))[:5])}); "
                      f"spans and open-pair lists there are incomplete.") if truncated_labels else ""
    project.issues.append(Issue(
        "info", "ROUTING_SUMMARY",
        (f"Routing measured on {measured_nets} net(s): {len(pairs)} differential pair(s), "
         f"{len(bends)} acute bend(s) outside pairs, {len(open_labels)} net(s) with open pin pairs, "
         f"{copper['unsupported_items']} unsupported item(s)." + pair_fillet_note + pour_note + truncated_note),
        [f"Scope: drawn segments, arcs (analytic length), vias and pads from {relative}. " + "; ".join(copper["limits"])] + open_lines,
        source="routing"))
    pair_fillets = sum(m.get("fillet_joints_excluded", 0) for m in measurements)
    project.routing = {"status": "measured", "pairs": measurements,
                       "nets": net_table,
                       "acute_bends": bends,
                       "fillet_joints_excluded": fillets + pair_fillets,
                       "net_count": measured_nets,
                       "unsupported_items": copper["unsupported_items"],
                       "limits": copper["limits"] + [
                           "Only generic P/N, +/- and DP/DM naming conventions identify pairs; pairs with unconventional names are not analyzed as pairs.",
                           "Mismatch screening uses a 1 mm bound as a reason to check the interface skew budget, not a pass/fail limit.",
                           "Obtuse bends (>=90 deg interior) are normal routing and are never reported as defects.",
                           "Sub-0.5 mm fillet-scale joints (teardrops, junction fillers) are counted separately and never listed as bends."]}
    if "routing_geometry_review" not in project.checks_performed:
        project.checks_performed.append("routing_geometry_review")
    project.review = build_review(project)

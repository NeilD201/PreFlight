"""Saved stackup and bounded trace-only DC estimates. No inferred fabrication profile."""
import math
from collections import Counter, defaultdict

# TI Analog Engineer's Pocket Reference, Eq. 152, nominal copper at 25 degrees C.
COPPER_OHM_MM = 17e-6
RESISTANCE_SOURCE = 'https://www.ti.com/seclit/eb/slyw038d/slyw038d.pdf'


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def stackup(board):
    setup = board.first('setup')
    saved = setup.first('stackup') if setup else None
    layers = []
    for layer in saved.all('layer') if saved else []:
        layers.append({'name': layer.values[0] if layer.values else '', 'type': layer.get('type'),
                       'thickness_mm': number(layer.get('thickness')), 'material': layer.get('material'),
                       'epsilon_r': number(layer.get('epsilon_r'))})
    return {'status': 'saved' if layers else 'missing', 'layers': layers,
            'basis': 'KiCad stackup settings; actual manufactured thickness is unverified.'}


def _touch(a, b, c, d):
    """Whether two segments touch, including collinear overlap."""
    def cross(p, q, r):
        return (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
    def on(p, q, r):
        return abs(cross(p, q, r)) < 1e-9 and all(min(p[i], q[i])-1e-8 <= r[i] <= max(p[i], q[i])+1e-8 for i in (0, 1))
    return (cross(a,b,c)*cross(a,b,d) < 0 and cross(c,d,a)*cross(c,d,b) < 0 or
            on(a,b,c) or on(a,b,d) or on(c,d,a) or on(c,d,b))


def dc_trace(drawn, copper, excluded):
    """Only a single, nonbranching, single-layer drawn chain can be summed.

    This is resistance between its drawn endpoints, not between component pins.
    Zones, vias, arcs, intersections and disconnected or branched geometry are
    deliberately unavailable instead of producing a misleading net resistance.
    """
    def unavailable(reason):
        return {'status': 'unavailable', 'reason': reason}
    if excluded:
        return unavailable('This net includes ' + ', '.join(sorted(excluded)) + '; complete resistance is not modeled.')
    if not drawn or len(drawn) > 1000:
        return unavailable('No bounded straight trace chain is available.')
    if len({s['layer'] for s in drawn}) != 1:
        return unavailable('The trace uses more than one copper layer.')
    thickness = copper.get(drawn[0]['layer'])
    if not thickness or thickness <= 0:
        return unavailable('Copper thickness is missing from the saved stackup.')
    graph, edges, endpoints = defaultdict(set), [], Counter()
    resistance, length = 0.0, 0.0
    for s in drawn:
        a, b = tuple(round(v, 6) for v in s['start']), tuple(round(v, 6) for v in s['end'])
        width = s['width_mm']
        if not all(math.isfinite(v) for v in (*a, *b, width)) or width <= 0 or a == b:
            return unavailable('A trace has invalid or zero dimensions.')
        edges.append((a, b)); graph[a].add(b); graph[b].add(a); endpoints.update((a,b))
        distance = math.dist(s['start'], s['end'])
        resistance += COPPER_OHM_MM * distance / (width * thickness)
        length += distance
    ends = [point for point, degree in endpoints.items() if degree == 1]
    if len(ends) != 2 or any(degree > 2 for degree in endpoints.values()):
        return unavailable('The copper branches or forms a loop; adding lengths would not give its resistance.')
    seen, todo = set(), [ends[0]]
    while todo:
        point = todo.pop()
        if point in seen:
            continue
        seen.add(point); todo.extend(graph[point] - seen)
    if len(seen) != len(graph):
        return unavailable('There is more than one drawn trace piece. Pads or pours may connect them.')
    for i, (a,b) in enumerate(edges):
        for c,d in edges[i+1:]:
            shared = set((a,b)) & set((c,d))
            if shared:
                common = next(iter(shared))
                other_a = b if a == common else a
                other_b = d if c == common else c
                # Adjacent collinear backtracking would count overlapping copper twice.
                if _touch(other_a, common, other_b, other_b) or _touch(other_b, common, other_a, other_a):
                    return unavailable('Drawn copper overlaps; a series sum is not valid.')
            elif _touch(a,b,c,d):
                return unavailable('Drawn copper intersects; a series sum is not valid.')
    return {'status': 'estimated', 'ohms': round(resistance, 7), 'length_mm': round(length, 3),
            'layer': drawn[0]['layer'], 'copper_thickness_mm': thickness, 'temperature_c': 25,
            'endpoints_mm': [list(point) for point in ends],
            'scope': 'Trace centerline between the shown endpoints only. Excludes pads, parts, contacts and heating; not RF impedance or full circuit resistance.'}


def measure_board(board, segments, vias):
    saved = stackup(board)
    copper = {layer['name']: layer['thickness_mm'] for layer in saved['layers'] if layer['type'] == 'copper'}
    names = {n.values[0]: n.values[1] for n in board.all('net') if len(n.values) >= 2}
    excluded = defaultdict(set)
    for name, items in vias.items():
        if items:
            excluded[name].add('vias')
    for item in board.children:
        if item.tag in ('zone', 'arc'):
            name = names.get(item.get('net'))
            if name:
                excluded[name].add('copper pours' if item.tag == 'zone' else 'curved traces')
    rows = []
    for name in sorted(set(segments) | set(vias) | set(excluded)):
        drawn = segments.get(name, [])
        widths = [s['width_mm'] for s in drawn if math.isfinite(s['width_mm']) and s['width_mm'] > 0]
        rows.append({'net': name, 'drawn_mm': round(sum(math.dist(s['start'], s['end']) for s in drawn), 3),
                     'min_width_mm': min(widths) if widths else None, 'vias': len(vias.get(name, [])),
                     'layers': sorted({s['layer'] for s in drawn}),
                     'dc_trace': dc_trace(drawn, copper, excluded[name])})
    from .manufacturing import measure_fabrication
    return {'fabrication': measure_fabrication(board), 'stackup': saved, 'nets': rows, 'resistance_source': RESISTANCE_SOURCE,
            'resistance_model': 'R = rho × length / (width × thickness); nominal copper rho = 17e-6 ohm mm at 25 °C.',
            'limits': ['Drawn lengths include branches and omit pads, pours and arcs.',
                       'No voltage-drop limit is evaluated without the operating current and complete return path.',
                       'No noise, current capacity, RF impedance or antenna performance simulation has run.']}

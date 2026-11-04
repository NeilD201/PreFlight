"""Bounded placement search for planner context; final validation belongs to KiCad.

Courtyard and pad bounding boxes are conservative planning envelopes. Candidates
are not RF designs or DRC approvals, and never mutate the board.
"""
import math

from .board_locations import _arc
from .parsers.sexpr import properties


def bounds(points):
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)] if points else None


def overlaps(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def edge_segments(root):
    result = []
    def xy(node, name):
        child = node.first(name)
        return [float(v) for v in child.values[:2]] if child and len(child.values) >= 2 else None
    for node in root.children:
        if node.get('layer') != 'Edge.Cuts':
            continue
        a, b = xy(node, 'start'), xy(node, 'end')
        points = []
        if node.tag == 'gr_line' and a and b:
            points = [a, b]
        elif node.tag == 'gr_arc' and a and b and xy(node, 'mid'):
            points = _arc(a, xy(node, 'mid'), b)
        elif node.tag == 'gr_rect' and a and b:
            points = [a, [b[0], a[1]], b, [a[0], b[1]], a]
        elif node.tag == 'gr_circle' and xy(node, 'center') and b:
            c = xy(node, 'center'); r = math.dist(c, b)
            points = [[c[0]+r*math.cos(i*math.tau/128), c[1]+r*math.sin(i*math.tau/128)] for i in range(129)]
        elif node.tag == 'gr_poly' and node.first('pts'):
            points = [[float(v) for v in p.values[:2]] for p in node.first('pts').all('xy')]
            if points: points.append(points[0])
        result.extend(zip(points, points[1:]))
    return result


def inside(point, segments):
    x, y = point
    return sum((a[1] > y) != (b[1] > y) and
               x < a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])
               for a, b in segments) % 2 == 1


def segment_hits_box(a, b, box):
    """Slab intersection, including endpoints and zero-width segment bounds."""
    low, high = 0., 1.
    for axis in (0, 1):
        delta = b[axis]-a[axis]
        if abs(delta) < 1e-12:
            if a[axis] < box[axis] or a[axis] > box[axis+2]: return False
        else:
            u, v = sorted(((box[axis]-a[axis])/delta, (box[axis+2]-a[axis])/delta))
            low, high = max(low, u), min(high, v)
            if low > high: return False
    return True


def box_inside(box, edges):
    corners = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
    # Also rejects cutouts wholly enclosed by the candidate rectangle.
    return all(inside(p, edges) for p in corners) and not any(segment_hits_box(a, b, box) for a, b in edges)


def local_envelope(footprint):
    points = []
    for shape in footprint.children:
        if shape.get('layer') not in ('F.CrtYd', 'B.CrtYd'): continue
        if shape.tag=='fp_circle' and shape.first('center') and shape.first('end'):
            c=[float(v) for v in shape.first('center').values[:2]]
            e=[float(v) for v in shape.first('end').values[:2]]
            r=math.dist(c,e); points.extend([[c[0]-r,c[1]-r],[c[0]+r,c[1]+r]])
        if shape.tag=='fp_arc' and all(shape.first(key) for key in ('start','mid','end')):
            arc=[tuple(float(v) for v in shape.first(key).values[:2]) for key in ('start','mid','end')]
            # Radius envelope around sampled points with a small sampling
            # margin avoids using just the three defining points as extents.
            sampled=_arc(*arc); arc_box=bounds(sampled)
            margin=max(math.dist(a,b) for a,b in zip(sampled,sampled[1:]))
            points.extend([[arc_box[0]-margin,arc_box[1]-margin],[arc_box[2]+margin,arc_box[3]+margin]])
        for key in ('start', 'end', 'mid'):
            point = shape.first(key)
            if point and len(point.values) >= 2: points.append([float(v) for v in point.values[:2]])
        if shape.first('pts'):
            points.extend([[float(v) for v in p.values[:2]] for p in shape.first('pts').all('xy')])
    # Pads also matter when a courtyard is absent or incomplete.
    for pad in footprint.all('pad'):
        at, size = pad.first('at'), pad.first('size')
        if not at or not size: continue
        x, y = map(float, at.values[:2]); w, h = map(float, size.values[:2])
        # A circular envelope remains conservative at every pad rotation.
        r = math.hypot(w, h)/2
        points.extend([[x-r, y-r], [x+r, y+r]])
    return bounds(points)


def transformed_box(box, x, y, angle):
    a = math.radians(angle); c, s = math.cos(a), math.sin(a)
    return bounds([[x+u*c+v*s, y-u*s+v*c] for u, v in
                   [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]])


def placement_candidates(root, footprint, *, reference='', anchors=(), limit=6, prefer_edge=False):
    edges = edge_segments(root); envelope = local_envelope(footprint)
    if not edges or not envelope: return {'candidates': [], 'reason': 'Outline or footprint envelope unavailable.'}
    # Open outlines cannot establish which side is inside.
    endpoints = {}
    for a, b in edges:
        for p in (a, b):
            key = tuple(round(v, 4) for v in p); endpoints[key] = endpoints.get(key, 0)+1
    if any(count % 2 for count in endpoints.values()):
        return {'candidates': [], 'reason': 'Board outline is open; placement search needs a closed outline.'}
    outer = bounds([p for edge in edges for p in edge])
    obstacles = []
    side = footprint.get('layer', 'F.Cu')
    for other in root.all('footprint'):
        ref = properties(other).get('Reference', '')
        at = other.first('at'); box = local_envelope(other)
        if ref == reference or not at or not box or other.get('layer', 'F.Cu') != side: continue
        x, y = map(float, at.values[:2]); angle = float(at.values[2]) if len(at.values)>2 else 0
        obstacles.append((ref, transformed_box(box, x, y, angle),
                          bool(other.first('locked') or 'locked' in other.values)))
    width, height = outer[2]-outer[0], outer[3]-outer[1]
    step = max(.5, math.sqrt(max(width*height, .01)/2500))
    nx, ny = max(1, math.ceil(width/step)), max(1, math.ceil(height/step))
    anchors = list(anchors) or [[(outer[0]+outer[2])/2, (outer[1]+outer[3])/2]]
    original=footprint.first('at') if reference else None
    origin=[float(v) for v in original.values[:2]] if original else None
    original_angle=float(original.values[2]) if original and len(original.values)>2 else 0
    if origin: anchors=[origin]
    scored = []; tested = 0
    for angle in (0, 90, 180, 270):
        for i in range(nx+1):
            for j in range(ny+1):
                x, y = round(outer[0]+i*width/nx, 5), round(outer[1]+j*height/ny, 5)
                if origin and math.dist((x,y),origin)<.25 and angle==original_angle: continue
                box = transformed_box(envelope, x, y, angle)
                tested += 1
                if not box_inside(box, edges): continue
                hits = [(ref, locked) for ref, obstacle, locked in obstacles if overlaps(box, obstacle)]
                if any(locked for _, locked in hits): continue
                distance = min(math.dist((x, y), p) for p in anchors)
                gap=0.
                if prefer_edge:
                    gaps=[]
                    for a,b in edges:
                        dx,dy=b[0]-a[0],b[1]-a[1]; denominator=dx*dx+dy*dy
                        for px,py in ((box[0],box[1]),(box[2],box[1]),(box[2],box[3]),(box[0],box[3])):
                            t=max(0,min(1,((px-a[0])*dx+(py-a[1])*dy)/denominator)) if denominator else 0
                            gaps.append(math.hypot(px-a[0]-t*dx,py-a[1]-t*dy))
                    gap=min(gaps)
                scored.append((len(hits), distance, x, y, angle, box, [ref for ref, _ in hits],gap))
    scored.sort(key=lambda row:(row[0],row[7]>1.5,row[1]+row[7]*2,row[2],row[3],row[4]))
    selected = []
    def select(rows, count, strategy):
        for hits, distance, x, y, angle, box, refs,gap in rows:
            if any(math.dist((x, y), (p['x_mm'], p['y_mm'])) < max(1., step*2) for p in selected): continue
            selected.append({'x_mm': x, 'y_mm': y, 'rotation_deg': angle, 'envelope_mm': box,
                             'overlapping_references': refs, 'distance_to_anchor_mm': round(distance, 3),
                             'strategy':strategy})
            if len(selected) >= count: break
    # Give the planner both low-disruption sites and nearby sites that require
    # relocation; a distant empty spot alone is poor guidance for an RF feed.
    select(scored,max(1,limit//2) if prefer_edge else limit,'Minimize footprint collisions')
    if prefer_edge and len(selected)<limit:
        nearby=sorted(scored,key=lambda row:(row[7]>1.5,row[1],row[0],row[2],row[3]))
        select(nearby,limit,'Shorter feed with explicit neighboring-part relocation')
    return {'candidates': selected, 'positions_checked': tested, 'grid_mm': round(step, 3),
            'basis': 'Conservative courtyard/pad envelopes inside the sampled outline, excluding locked-footprint collisions. '
                     'Tracks, vias, filled copper, mechanical fit, manufacturer keepouts and RF performance still require validation. '
                     'No candidate is not proof that placement is impossible.'}

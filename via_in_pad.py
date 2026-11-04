"""Detect drilled via holes intersecting exposed SMD copper lands.

Uses drill geometry, not the via's copper ring. Mask/paste artwork and custom
pad primitives require a separate assembly review; they are not inferred here.
"""
import math

from .parsers.sexpr import properties


def _values(node, tag, count=2):
    child = node.first(tag)
    if not child or len(child.values) < count:
        raise ValueError(tag)
    values = [float(v) for v in child.values[:count]]
    if not all(math.isfinite(v) for v in values):
        raise ValueError(tag)
    return values


def measure_via_in_pad(board):
    pads, skipped, matches = [], 0, []
    for footprint in board.all('footprint'):
        for pad in footprint.all('pad'):
            if len(pad.values) < 3 or pad.values[1] != 'smd':
                continue
            layers = pad.first('layers')
            exposed = [side for side in ('F', 'B') if layers and
                       (side+'.Cu' in layers.values or '*.Cu' in layers.values) and
                       (side+'.Mask' in layers.values or '*.Mask' in layers.values)]
            if not exposed:
                continue
            try:
                shape = pad.values[2]
                if shape not in ('rect', 'circle', 'oval', 'roundrect') or pad.first('chamfer'):
                    raise ValueError('Unsupported pad shape')
                ox, oy = _values(footprint, 'at')
                fp_at = footprint.first('at').values
                angle = math.radians(float(fp_at[2]) if len(fp_at) > 2 else 0)
                x, y = _values(pad, 'at')
                center = [ox+x*math.cos(angle)+y*math.sin(angle), oy-x*math.sin(angle)+y*math.cos(angle)]
                w, h = _values(pad, 'size')
                if min(w, h) <= 0 or (shape == 'circle' and abs(w-h) > 1e-8):
                    raise ValueError('Invalid pad size')
                pa = pad.first('at').values
                rotation = math.radians(float(pa[2]) if len(pa) > 2 else 0)
                radius = min(w,h)/2 if shape in ('circle','oval') else (
                    min(w,h)*float(pad.get('roundrect_rratio','0.25')) if shape == 'roundrect' else 0)
                if not 0 <= radius <= min(w,h)/2 or not math.isfinite(rotation+angle):
                    raise ValueError('Invalid pad geometry')
                ref = properties(footprint).get('Reference', '')
                if not ref:
                    ref = next((n.values[1] for n in footprint.all('fp_text')
                                if len(n.values)>1 and n.values[0]=='reference'), '')
                pads.append((center, w/2, h/2, rotation, radius, exposed, ref, pad))
            except (ValueError, TypeError):
                skipped += 1
    unreadable = 0
    for via in board.all('via'):
        try:
            x,y = _values(via,'at')
            drill = _values(via,'drill',1)[0]
            if drill <= 0:
                raise ValueError('Invalid drill')
            layers = via.first('layers')
            if not layers:
                raise ValueError('Missing layers')
        except (ValueError, TypeError):
            unreadable += 1
            continue
        for center, hw, hh, rotation, radius, sides, ref, pad in pads:
            shared = [side for side in sides if side+'.Cu' in layers.values]
            if not shared:
                continue
            dx,dy=x-center[0],y-center[1]
            # Pad angle is board-absolute in KiCad; footprint angle transforms
            # its local center only (including saved back-side footprints).
            px,py=dx*math.cos(rotation)-dy*math.sin(rotation),dx*math.sin(rotation)+dy*math.cos(rotation)
            qx,qy=abs(px)-(hw-radius),abs(py)-(hh-radius)
            distance=math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-radius
            if distance >= drill/2-1e-8:
                continue
            matches.append({'reference':ref,'pad':pad.values[0], 'sides':shared,
                            'x_mm':x,'y_mm':y,'drill_mm':drill,
                            'via_uuid':via.get('uuid') or via.get('tstamp'),
                            'pad_uuid':pad.get('uuid') or pad.get('tstamp'),
                            'via_line':via.line,'pad_line':pad.line,
                            'hole_center_in_pad':distance<=0})
    return {'status':'partial' if skipped or unreadable else 'measured',
            'count':len({(m['via_uuid'],m['via_line']) for m in matches}),
            'locations':matches,'unsupported_pads':skipped,'unreadable_vias':unreadable,
            'scope':'Drill holes overlapping exposed SMD copper lands. Custom/chamfered pads, mask expansion, paste apertures, thermal performance and actual fill/cap fabrication are not verified.'}

"""A cropped, position-calibrated KiCad board preview for the local UI."""

import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

from .archive import read_project_file
from .native import find_kicad, _run
from .parsers.sexpr import parse
from .board_locations import board_locations


LINE = re.compile(r'<path\s+d="\s*M\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+L\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*"', re.S)


def _edge_lines(board_text):
    root = parse(board_text, 'kicad_pcb')
    lines, points = [], []
    for item in root.children:
        if item.get('layer') != 'Edge.Cuts':
            continue
        for tag in ('start','mid','end'):
            point = item.first(tag)
            if point and len(point.values) >= 2:
                points.append(tuple(float(v) for v in point.values[:2]))
        if item.tag == 'gr_circle':
            center, edge = item.first('center'), item.first('end')
            if center and edge and len(center.values) >= 2 and len(edge.values) >= 2:
                cx, cy = map(float,center.values[:2]); ex, ey = map(float,edge.values[:2])
                radius = math.hypot(ex-cx,ey-cy)
                points.extend(((cx-radius,cy-radius),(cx+radius,cy+radius)))
        if item.tag != 'gr_line':
            continue
        start, end = item.first('start'), item.first('end')
        if start and end and len(start.values) >= 2 and len(end.values) >= 2:
            lines.append(tuple(float(v) for v in (*start.values[:2], *end.values[:2])))
    return lines, points


def _plot_lines(svg):
    return [tuple(map(float, match.groups())) for match in LINE.finditer(svg)]


def _calibrate(source_lines, plot_lines, mirrored=False):
    """Vote on the translation of matching Edge.Cuts segments.

    KiCad shifts plot coordinates to a positive page origin. Its offset depends
    on drawings elsewhere in the file, so the SVG viewBox alone is insufficient.
    """
    votes = {}
    direction = -1 if mirrored else 1
    for x1, y1, x2, y2 in source_lines:
        if math.hypot(x2-x1, y2-y1) < 2:
            continue
        for a, b, c, d in plot_lines:
            for px1, py1, px2, py2 in ((a,b,c,d), (c,d,a,b)):
                if abs((px2-px1)-direction*(x2-x1)) > .012 or abs((py2-py1)-(y2-y1)) > .012:
                    continue
                key = (round(px1-direction*x1, 2), round(py1-y1, 2))
                votes[key] = votes.get(key, 0) + 1
    if not votes:
        raise ValueError('Could not align KiCad outline with PCB coordinates.')
    (offset_x, offset_y), count = max(votes.items(), key=lambda item:item[1])
    if count < 2:
        raise ValueError('Board outline alignment is ambiguous.')
    return offset_x, offset_y


def _safe_svg(svg, bounds):
    if any(re.search(pattern, svg, re.I) for pattern in (r'<script\b', r'<foreignObject\b', r'<image\b', r'\bon\w+\s*=', r'\b(?:xlink:)?href\s*=')):
        raise ValueError('KiCad SVG contains unsupported external or active content.')
    start = svg.find('<svg')
    if start < 0:
        raise ValueError('KiCad did not return an SVG.')
    svg = svg[start:]
    x, y, width, height = bounds
    svg, changed = re.subn(r'viewBox="[^"]+"', f'viewBox="{x:.4f} {y:.4f} {width:.4f} {height:.4f}"', svg, count=1)
    if changed != 1:
        raise ValueError('KiCad SVG has no viewBox.')
    svg = re.sub(r'\bwidth="[^"]+"', f'width="{width:.4f}mm"', svg, count=1)
    svg = re.sub(r'\bheight="[^"]+"', f'height="{height:.4f}mm"', svg, count=1)
    return svg.encode('utf-8')


def render_board(project, source, executable=None):
    """Return generated assets, or an explanatory metadata-only result."""
    relative = project.selected_files.get('pcb')
    if not relative or not source:
        return {'status':'unavailable', 'message':'A selected PCB is needed for the board view.'}, {}
    binary = find_kicad(executable)
    if not binary:
        return {'status':'unavailable', 'message':'Install KiCad CLI to render the board.'}, {}
    try:
        content = read_project_file(source, relative)
        digest = hashlib.sha256(content).hexdigest()
        if digest != project.provenance.get('source_hashes', {}).get(relative):
            raise ValueError('The PCB changed since this report. Run a new check to update its board view.')
        analyzed = getattr(project, '_fabrication_assets', {}).get('analyzed-board.kicad_pcb')
        if analyzed:
            if hashlib.sha256(analyzed).hexdigest() != project.board_checks.get('analyzed_pcb_sha256'):
                raise ValueError('The prepared board snapshot no longer matches its check results.')
            content = analyzed
        source_lines, points = _edge_lines(content.decode('utf-8'))
        if len(source_lines) < 2:
            raise ValueError('The PCB has too few straight Edge.Cuts segments to locate markers reliably.')
        if not points:
            raise ValueError('The PCB outline has no usable coordinates.')
        min_x, max_x = min(x for x,y in points), max(x for x,y in points)
        min_y, max_y = min(y for x,y in points), max(y for x,y in points)
        if max_x-min_x <= 0 or max_y-min_y <= 0:
            raise ValueError('The PCB outline has no usable area.')
        with tempfile.TemporaryDirectory(prefix='pcb-board-view-') as temp:
            folder = Path(temp)
            board = folder/'board.kicad_pcb'
            board.write_bytes(content)
            assets, views = {}, {}
            for side, layers, mirror in (('front','F.Cu,F.SilkS,Edge.Cuts',False),('back','B.Cu,B.SilkS,Edge.Cuts',True)):
                edge = folder/f'edge-{side}.svg'
                output = folder/f'board-{side}.svg'
                base = ['pcb','export','svg','--mode-single','--page-size-mode','2']
                flip = ['--mirror'] if mirror else []
                _run(binary, [*base,'--layers','Edge.Cuts',*flip,'--output',str(edge),str(board)], folder)
                _run(binary, [*base,'--layers',layers,*flip,'--output',str(output),str(board)], folder)
                offset_x, offset_y = _calibrate(source_lines, _plot_lines(edge.read_text(encoding='utf-8')), mirror)
                margin = 1.8
                left = (offset_x-max_x if mirror else offset_x+min_x)-margin
                top = offset_y+min_y-margin
                width, height = max_x-min_x+2*margin, max_y-min_y+2*margin
                filename = f'board-{side}.svg'
                assets[filename] = _safe_svg(output.read_text(encoding='utf-8'), (left,top,width,height))
                views[side] = {'file':filename,'bounds':[left,top,width,height],
                               'offset':[offset_x,offset_y], 'mirrored':mirror}
        return {'status':'ready','pcb_sha256':digest,'source_file':relative,
                **board_locations(content),
                'analyzed_pcb_sha256':hashlib.sha256(content).hexdigest(),
                'outline_mm':[min_x,min_y,max_x,max_y], 'views':views,
                'note':'Markers identify component locations; board-wide rule groups have no single marker.'}, assets
    except (OSError, ValueError, UnicodeError) as exc:
        return {'status':'unavailable','message':str(exc)}, {}


def previous_visual(folder):
    """Carry a verified board snapshot across a context-only review."""
    try:
        metadata = json.loads((folder/'board-visual.json').read_text(encoding='utf-8'))
        assets = {name:(folder/name).read_bytes() for name in ('board-front.svg','board-back.svg') if (folder/name).is_file()}
        return metadata, assets
    except (OSError, ValueError):
        return {'status':'unavailable','message':'Run a new check to render the board.'}, {}

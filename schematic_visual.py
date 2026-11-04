"""KiCad-rendered root schematic with symbol locations from the same source bytes."""
import hashlib
import math
from pathlib import Path
import re
import tempfile
from .archive import read_project_file
from .native import find_kicad, _run, _stage
from .parsers.sexpr import parse
from .board_visual import _safe_svg
from .parsers.sexpr import properties, unit_number


def schematic_locations(root, references):
    """Map placed UUIDs and pin UUIDs to the source sheet's millimetres.

    ERC JSON positions can use a different scale. Resolve identifiers in the
    source instead of guessing a conversion from reported coordinates.
    """
    locations, items, pins = {}, {}, {}
    library = root.first('lib_symbols')
    definitions = {s.values[0]: s for s in library.all('symbol')} if library else {}
    for symbol in root.all('symbol'):
        at = symbol.first('at')
        if not at or len(at.values) < 2:
            continue
        sx, sy = map(float, at.values[:2])
        angle = math.radians(float(at.values[2]) if len(at.values) > 2 else 0)
        mirror = symbol.get('mirror')
        def transform(x, y):
            # Mirror around the named axis, then rotate in library coordinates.
            if mirror == 'x': y = -y
            if mirror == 'y': x = -x
            return [round(sx + x*math.cos(angle) - y*math.sin(angle), 6),
                    round(sy - x*math.sin(angle) - y*math.cos(angle), 6)]
        ref = references.get(symbol.get('uuid')) or properties(symbol).get('Reference', '')
        if ref and not ref.startswith('#'):
            locations.setdefault(ref, []).append([sx, sy])
        items[symbol.get('uuid')] = {'point': [sx, sy], 'reference': ref, 'label': ref, 'kind': 'symbol'}
        definition = definitions.get(symbol.get('lib_id'))
        if not definition:
            continue
        unit = unit_number(symbol)
        libpins = {}
        for part in definition.all('symbol'):
            match = re.search(r'_(\d+)_(\d+)$', part.values[0] if part.values else '')
            if match and int(match[1]) not in (0, unit):
                continue
            if match and int(match[2]) not in (0, int(symbol.get('convert', '1'))):
                continue
            for pin in part.all('pin'):
                pa = pin.first('at')
                if not pa or len(pa.values) < 2: continue
                number = pin.get('number')
                point = transform(*map(float, pa.values[:2]))
                libpins.setdefault(number, []).append({'point': point, 'reference': ref,
                    'pin': number, 'name': pin.get('name'), 'label': f'{ref} pin {number}',
                    'kind': 'pin', 'hidden': 'hide' in pin.values or pin.first('hide') is not None})
        for pin in symbol.all('pin'):
            matches = libpins.get(pin.values[0] if pin.values else '', [])
            unique = {tuple(p['point']) for p in matches}
            if len(unique) != 1: continue
            entry = matches[0]
            items[pin.get('uuid')] = entry
            pins.setdefault(ref, {})[entry['pin']] = entry
    for element in root.children:
        if element.tag == 'symbol': continue
        at = element.first('at')
        uuid = element.get('uuid')
        if uuid and at and len(at.values) >= 2:
            items[uuid] = {'point': list(map(float, at.values[:2])), 'kind': element.tag,
                           'label': element.values[0] if element.values else element.tag,
                           'reference': ''}
    return locations, items, pins


def render_schematic(project, source, executable=None):
    relative=project.selected_files.get('schematic')
    binary=find_kicad(executable)
    if not relative or not source or not binary:
        if not relative:
            message='No schematic file was selected, so there is no schematic preview for this review. Other checks are unaffected.'
        elif not source:
            message='The report files are unavailable, so the schematic preview cannot be drawn.'
        else:
            message='KiCad CLI was not found, so the schematic preview cannot be drawn.'
        return {'status':'unavailable','message':message},{}
    try:
        content=read_project_file(source,relative)
        digest=hashlib.sha256(content).hexdigest()
        if digest!=project.provenance.get('source_hashes',{}).get(relative):
            raise ValueError('The schematic changed. Check your updated files to refresh this view.')
        root=parse(content.decode('utf-8'),'kicad_sch')
        # Use the parsed report's reference mapping, not potentially stale symbol labels.
        references={r.uuid:ref for ref,c in project.components.items() for r in c.schematic if r.source_file==relative}
        locations, item_locations, pins = schematic_locations(root, references)
        with tempfile.TemporaryDirectory(prefix='pcb-schematic-') as temp:
            folder=Path(temp);_stage(source,project,folder)
            out=folder/'rendered';out.mkdir()
            _run(binary,['sch','export','svg','--exclude-drawing-sheet','--pages','1','--output',str(out),str(folder/relative)],folder)
            files=list(out.glob('*.svg'))
            if len(files)!=1:raise ValueError('Could not identify the exported root sheet.')
            svg=files[0].read_text()
            match=re.search(r'viewBox="([^"]+)"',svg)
            bounds=[float(v) for v in match.group(1).split()] if match else []
            if len(bounds)!=4 or min(bounds[2:])<=0:raise ValueError('The schematic image has no valid page size.')
            points=[point for entries in locations.values() for point in entries]
            # KiCad allows circuits beyond the nominal paper boundary.
            for item in root.children:
                if item.tag in ('wire','bus','polyline'):
                    pts=item.first('pts')
                    if pts:points.extend([[float(v) for v in xy.values[:2]] for xy in pts.all('xy')])
            if points:
                xs,ys=zip(*points);bounds=[min(xs)-30,min(ys)-30,max(xs)-min(xs)+60,max(ys)-min(ys)+60]
            image=_safe_svg(svg,bounds)
        return {'status':'ready','file':'schematic.svg','bounds':bounds,'locations':locations,
                'item_locations':item_locations,'pins':pins,'coordinate_version':2,
                'source_file':relative,'sha256':digest,'root_only':bool(root.all('sheet')),
                'message':'Root sheet only; child sheets are not shown.' if root.all('sheet') else 'Rendered by KiCad from this report’s schematic.'},{'schematic.svg':image}
    except (OSError,ValueError,RuntimeError) as exc:
        return {'status':'unavailable','message':str(exc)},{}


def previous_schematic(folder):
    import json
    try:
        meta=json.loads((Path(folder)/'schematic-visual.json').read_text())
        return meta,{'schematic.svg':(Path(folder)/'schematic.svg').read_bytes()} if meta['status']=='ready' else {}
    except (OSError,ValueError,KeyError):
        return {'status':'unavailable','message':'Run a new check to create the schematic preview.'},{}

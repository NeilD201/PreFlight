"""Bounded, evidence-led checks of selected high-sensitivity PCB nets.

The measurements describe drawn copper. They do not calculate impedance,
coupled noise, emissions, or a completed route through filled zones.
"""

import hashlib
import math
import re
from collections import defaultdict

from .archive import ArchiveError, read_project_file
from .models import Issue
from .parsers.sexpr import ParseError, parse
from .review import build_review


NORDIC_PIN = 'https://docs.nordicsemi.com/r/bundle/ps_nrf52840/page/pin.html?contentId=hMpuE~dcsjrrIlIt8ErcrQ'
NORDIC_REFERENCE = 'https://docs.nordicsemi.com/r/bundle/ps_nrf52840/page/ref_circuitry.html?contentId=~8dvfAp~FNqgG6g6LSuHdA'
MAX30001_SHEET = 'https://www.analog.com/media/en/technical-documentation/data-sheets/max30001.pdf'


def _xy(node, tag):
    child = node.first(tag)
    if child and len(child.values) >= 2:
        try:
            return [float(child.values[0]), float(child.values[1])]
        except ValueError:
            pass
    return None


def _board_geometry(board):
    net_names = {item.values[0]: item.values[1] for item in board.all('net') if len(item.values) >= 2}
    segments, vias = defaultdict(list), defaultdict(list)
    for item in board.children:
        if item.tag == 'segment':
            start, end, net = _xy(item, 'start'), _xy(item, 'end'), net_names.get(item.get('net'))
            if start and end and net:
                try:
                    width = float(item.get('width'))
                except ValueError:
                    continue
                segments[net].append({'start': start, 'end': end, 'width_mm': width,
                                      'layer': item.get('layer'), 'line': item.line})
        elif item.tag == 'via':
            at, net = _xy(item, 'at'), net_names.get(item.get('net'))
            if at and net:
                vias[net].append({'at': at, 'line': item.line})
    return segments, vias


def _metrics(name, segments, vias):
    drawn = segments.get(name, [])
    return {'net': name, 'copper_mm': round(sum(math.dist(s['start'], s['end']) for s in drawn), 2),
            'segments': len(drawn), 'vias': len(vias.get(name, [])),
            'layers': sorted({s['layer'] for s in drawn}),
            'widths_mm': sorted({round(s['width_mm'], 3) for s in drawn})[:20],
            'geometry': drawn[:500], 'geometry_truncated': len(drawn) > 500}


def _pads(project, ref):
    component = project.components.get(ref)
    if not component:
        return []
    return [pad for record in component.pcb for pad in record.pads]


def _pad_net(project, ref, number):
    return next((p.net for p in _pads(project, ref) if p.number == number), '')


def _members(project, net):
    return [(ref, pad.number) for ref in project.components for pad in _pads(project, ref) if pad.net == net]


def _antenna_terminal(project, net, radio_ref):
    for ref, _ in _members(project, net):
        if ref == radio_ref:
            continue
        component = project.components[ref]
        records = component.schematic or component.pcb
        identity = ' '.join((r.value + ' ' + r.footprint) for r in records).lower()
        if 'antenna' in identity or 'sma' in identity or 'u.fl' in identity or 'ufl' in identity or 'ipex' in identity:
            return ref
    return None


def _series_neighbor(project, net):
    """Follow only a unique fitted series element, never a shunt or first match."""
    from .checks.circuit import is_ground
    power_nets={p.net for c in project.components.values() for p in c.schematic_pins
                if p.electrical_type in ('power_in','power_out')}
    candidates=set()
    for ref, _ in _members(project, net):
        if not re.fullmatch(r'(?:L|FB|R)\d+',ref) or any(r.dnp for r in project.components[ref].pcb):
            continue
        pads = _pads(project, ref)
        if len(pads) != 2:
            continue
        other = next((p.net for p in pads if p.net and p.net != net), '')
        if other and not is_ground(other) and other not in power_nets:
            candidates.add((ref,other))
    return next(iter(candidates)) if len(candidates)==1 else (None, '')


def _datasheet_source(project, ref, fallback):
    part = next((p for p in project.datasheets.get('parts', []) if p.get('reference') == ref), {})
    candidate = ([{'title': 'LCSC candidate datasheet — verify ordering code', 'url': part['pdf_url']}]
                 if part.get('pdf_status') == 'read' and part.get('pdf_url') else [])
    return candidate + fallback


def analyze_signal_paths(project, source, pcb_tree=None, pcb_raw=None):
    """Add review items only when the original board bytes still match the checked input.

    pcb_tree/pcb_raw are a pre-parsed tree and its digest-verified bytes,
    shared with the routing pass to avoid re-reading and re-parsing the same
    file (the locations index alone re-parses it a third time). Both are only
    read, never mutated, so sharing them cannot change results.
    """
    project.issues = [i for i in project.issues if i.source != 'signal_path']
    relative = project.selected_files.get('pcb')
    digest = project.provenance.get('source_hashes', {}).get(relative)
    if not relative or not digest:
        project.signal_paths = {'status': 'unavailable', 'message': 'No verified PCB source is available.'}
        return
    board = pcb_tree
    raw = pcb_raw
    if board is None or raw is None:
        try:
            raw = read_project_file(source, relative)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ArchiveError('PCB source changed since this check. Run preflight again.')
            board = parse(raw.decode('utf-8'), 'kicad_pcb')
        except (ArchiveError, UnicodeError, ParseError) as exc:
            project.signal_paths = {'status': 'unavailable', 'message': str(exc)}
            return
    segments, vias = _board_geometry(board)
    from .board_locations import board_locations
    location_index=board_locations(raw)['item_locations']
    offboard_copper=[{'uuid':item['uuid'],'line':item['line'],'point':item['point'],'points':item['points'],
                      'width_mm':item['width_mm'],'layer':item['layers'][0], 'outline_mm':item['outline_mm']}
                     for item in location_index.values() if item.get('kind')=='graphic' and item.get('outside_outline') and not item.get('net')]
    from .board_measurements import measure_board
    measurements = measure_board(board, segments, vias)
    paths = []
    for ref, component in project.components.items():
        value = (component.schematic or component.pcb)[0].value.lower() if component.schematic or component.pcb else ''
        if 'nrf52840' in value:
            antenna_pins = [pin for pin in component.schematic_pins if pin.name.upper() == 'ANT']
            for pin in antenna_pins[:1]:
                feed = _pad_net(project, ref, pin.number)
                if not feed:
                    continue
                series_ref, output = _series_neighbor(project, feed)
                names = [feed] + ([output] if output else [])
                terminal = next((t for net in names if (t := _antenna_terminal(project, net, ref))), None)
                path_id = f'rf-{ref}'
                path = {'id': path_id, 'kind': 'rf', 'label': f'{ref} antenna feed', 'anchor': ref,
                        'nets': [_metrics(net, segments, vias) for net in names],
                        'series_component': series_ref, 'terminal': terminal,
                        'unnetted_copper_outside_outline':offboard_copper,
                        'sources': _datasheet_source(project, ref, [
                            {'title': 'Nordic nRF52840 pin assignments', 'url': NORDIC_PIN},
                            {'title': 'Nordic nRF52840 reference circuitry', 'url': NORDIC_REFERENCE}]),
                        'limits': 'Drawn copper only. RF impedance, ground return, keepout and radiation require stackup and layout review.'}
                paths.append(path)
                if series_ref and not terminal:
                    joined = ', '.join(names)
                    copper = ', '.join(f"{n['net']} {n['copper_mm']} mm" for n in path['nets'])
                    evidence=[f'{relative}: {ref} pin {pin.number} → {series_ref}',f'RF net copper: {copper}']
                    message=f'{ref} ANT reaches {series_ref}, but no antenna or RF connector was identified on {joined}.'
                    if offboard_copper:
                        drawing=offboard_copper[0];x,y=drawing['point']
                        evidence.append(f'{relative}:{drawing["line"]}: unnetted {drawing["layer"]} copper drawing at {x:.3f}, {y:.3f} mm (UUID {drawing["uuid"]}) is outside Edge.Cuts; it is not part of the RF net.')
                        message+=f' A separate, unnetted {drawing["layer"]} drawing lies outside the board outline; it does not form an on-board antenna connection.'
                    else:message+=' An unrecognized copper radiator or off-board connection may exist.'
                    project.issues.append(Issue('warning', 'RF_FEED_NO_TERMINAL',message,
                        evidence,
                        source='signal_path', component=ref, confidence=.8,
                        suggested_fix='Trace the network to its intended radiator or connector. If one exists, identify it in the design; otherwise add and tune the antenna connection using the exact package reference layout.',
                        details={'signal_path_id': path_id, 'sources': path['sources']}))
        if 'max30001' in value:
            pins = {p.name.upper(): p.number for p in component.schematic_pins}
            names = [(_pad_net(project, ref, pins[name]), name) for name in ('ECGP', 'ECGN') if name in pins]
            if len(names) != 2 or not all(net for net, _ in names):
                continue
            paths_here = [_metrics(net, segments, vias) for net, _ in names]
            path_id = f'ecg-{ref}'
            path = {'id': path_id, 'kind': 'sensitive', 'label': f'{ref} ECG inputs', 'anchor': ref,
                    'nets': paths_here, 'sources': _datasheet_source(project, ref, [
                        {'title': 'Analog Devices MAX30001 datasheet', 'url': MAX30001_SHEET}]),
                    'limits': 'Total net copper includes branches and is not electrode-to-pin path length. Noise and CMRR cannot be measured from this file.'}
            paths.append(path)
            first, second = paths_here
            if max(first['vias'], second['vias']) >= 3 and abs(first['vias'] - second['vias']) >= 3:
                project.issues.append(Issue('info', 'SENSITIVE_INPUT_ROUTE_REVIEW',
                    f'{ref} ECG input nets use different via counts ({first["net"]}: {first["vias"]}; {second["net"]}: {second["vias"]}). '
                    'This is not a measured noise or matching failure, and the difference alone does not require a routing change.',
                    [f'{relative}: ECGP {first["copper_mm"]} mm drawn copper, ECGN {second["copper_mm"]} mm; totals may include branches.'],
                    source='signal_path', component=ref, confidence=.6,
                    suggested_fix='No routing change is supported by these counts alone. The AI can assess the actual input connections against the datasheet and stated operating mode.',
                    details={'signal_path_id': path_id, 'sources': path['sources']}))
    from .routing_chains import build_routing_chains
    chain_nets={net for chain in build_routing_chains(project).get('chains',[]) for net in chain['nets']}
    project.signal_paths = {'status': 'reviewed', 'paths': paths,
        'board_measurements': measurements,
        'net_geometry':{net:_metrics(net,segments,vias) for net in sorted(chain_nets)},
        'scope': 'Known nRF52840 antenna and MAX30001 ECG inputs; only drawn segments and vias are measured.',
        'limitations': 'This scan does not simulate RF impedance, antenna performance, EMI, or analog noise.'}
    if 'signal_path_geometry_review' not in project.checks_performed:
        project.checks_performed.append('signal_path_geometry_review')
    project.review = build_review(project)

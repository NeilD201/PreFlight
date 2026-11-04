"""Dated, bounded fabrication profiles and measurements from the checked PCB snapshot."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import math

VERIFIED = '2026-10-10'
VIA_COVERING_VERIFIED = '2026-10-12'
VIA_SOURCES = {'jlcpcb': 'https://jlcpcb.com/help/article/pcb-via-covering',
               'pcbway': 'https://www.pcbway.com/pcb_prototype/PCB_Via_Covering.html'}
PROFILES = {
    'jlcpcb': {'name': 'JLCPCB', 'process': 'Rigid FR4 · through vias',
               'url': 'https://jlcpcb.com/capabilities/pcb-capabilities'},
    'pcbway': {'name': 'PCBWay', 'process': 'Standard rigid FR4 · through vias',
               'url': 'https://www.pcbway.com/capabilities.html'},
}
LIMITS = [
    'Checks use the saved run, not unsaved KiCad edits. Save in KiCad and click Update to check changes.',
    'Copper spacing is checked only at the pad pairs already measured by KiCad DRC. A complete manufacturer DRC has not run.',
    'Board outline, edge clearance, hole spacing, component holes, slots, mask webs, actual via filling, assembly and controlled impedance are not verified. Via-in-pad checks detect drill overlap with supported SMD lands; custom pads and final mask/paste artwork are not checked.',
    'These published limits do not confirm that the whole board is manufacturable. Final stackup and fabrication acceptance come from the manufacturer.',
]


def validate_selection(value):
    if not isinstance(value, dict):
        raise ValueError('Choose a manufacturer and ordering options.')
    selection = {key: value.get(key, default) for key, default in
                 [('manufacturer', ''), ('copper_oz', '1'), ('finish', 'enig')]}
    if any(not isinstance(v, str) for v in selection.values()):
        raise ValueError('Choose values from the manufacturer options.')
    if selection['manufacturer'] not in ('', *PROFILES):
        raise ValueError('Choose JLCPCB or PCBWay.')
    if selection['copper_oz'] not in ('1', '2') or selection['finish'] not in ('enig', 'hasl', 'osp'):
        raise ValueError('Choose a supported copper weight and surface finish.')
    return selection


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def measure_fabrication(board):
    from .via_in_pad import measure_via_in_pad
    layer_node = board.first('layers')
    copper = [n.values[0] for n in layer_node.children if n.values and n.values[0].endswith('.Cu')] if layer_node else []
    general = board.first('general')
    setup = board.first('setup')
    stack = setup.first('stackup') if setup else None
    copper_mm, materials = [], set()
    for layer in stack.all('layer') if stack else []:
        if layer.get('type') == 'copper':
            copper_mm.append(number(layer.get('thickness')))
        elif layer.get('material'):
            materials.add(layer.get('material'))
    vias = Counter()
    invalid = 0
    for via in board.all('via'):
        size, drill = number(via.get('size')), number(via.get('drill'))
        layers = via.first('layers')
        through = not any(v in ('blind', 'micro') for v in via.values) and layers and set(layers.values) == {'F.Cu', 'B.Cu'}
        if size is None or drill is None or not 0 < drill < size:
            invalid += 1
            continue
        vias[(size, drill, bool(through))] += 1
    widths = [number(n.get('width')) for n in board.children if n.tag in ('segment', 'arc')]
    valid_widths = [w for w in widths if w is not None and w > 0]
    return {'version': 2, 'status': 'measured', 'via_in_pad': measure_via_in_pad(board), 'layers': len(copper) or None,
            'thickness_mm': number(general.get('thickness')) if general else None,
            'copper_mm': copper_mm, 'materials': sorted(materials),
            'min_trace_mm': min(valid_widths) if valid_widths else None,
            'trace_count': len(widths), 'invalid_traces': len(widths) - len(valid_widths),
            'invalid_vias': invalid,
            'vias': [{'diameter_mm': s, 'drill_mm': d, 'ring_mm': round((s-d)/2, 6),
                      'through': t, 'count': count} for (s, d, t), count in sorted(vias.items())]}


def measure_source(project, source):
    from .archive import read_project_file, ArchiveError
    from .parsers.sexpr import parse
    relative = project.selected_files.get('pcb')
    digest = project.provenance.get('source_hashes', {}).get(relative)
    if not source or not relative or not digest:
        return {'status': 'unavailable', 'message': 'Click Update with the connected KiCad files to measure this board.'}
    try:
        raw = read_project_file(source, relative)
        if sha256(raw).hexdigest() != digest:
            return {'status': 'unavailable', 'message': 'Your PCB changed since this run. Click Update to check the new version.'}
        return measure_fabrication(parse(raw.decode('utf-8'), 'kicad_pcb'))
    except (OSError, ValueError, ArchiveError):
        return {'status': 'unavailable', 'message': 'The checked PCB could not be read. Reconnect the project files and click Update.'}


def evaluate(data, selection, measured):
    from .footprint_context import footprint_rule_context
    selection = validate_selection(selection)
    maker = selection['manufacturer']
    result = {'selection': selection, 'profiles': PROFILES, 'verified': VERIFIED,
              'status': 'not_selected', 'checks': [], 'order_settings': [], 'kicad_settings': [],
              'limits': LIMITS, 'measurements': measured or {}}
    if not maker:
        result['summary'] = 'Choose where you plan to order this board.'
        return result
    profile = PROFILES[maker]
    result['profile'] = profile
    result['summary'] = 'Checking the saved PCB against ' + profile['name'] + ' limits.'
    if not measured or measured.get('status') != 'measured':
        result.update(status='unavailable', summary=(measured or {}).get('message', 'Click Check manufacturer to measure this saved board.'))
        return result
    checks, order = result['checks'], result['order_settings']
    def add(cid, label, status, actual, limit, advice='', groups=None):
        checks.append({'id': cid, 'label': label, 'status': status, 'actual': actual,
                       'limit': limit, 'advice': advice, 'group_ids': groups or [], 'source': profile['url']})
    layers, thickness = measured.get('layers'), measured.get('thickness_mm')
    oz = int(selection['copper_oz'])
    finish = {'enig': 'ENIG', 'hasl': 'Lead-free HASL', 'osp': 'OSP'}[selection['finish']]
    valid_layers = [1, 2, *range(4, 33 if maker == 'jlcpcb' else 15, 2)]
    add('layers', 'Copper layers', 'supported' if layers in valid_layers else 'outside' if layers else 'unknown',
        str(layers or 'Not saved'), '1, 2 or even layers up to ' + str(valid_layers[-1]))
    thicknesses = [.4, .6, .8, 1, 1.2, 1.6, 2] if maker == 'jlcpcb' else [.2, .4, .6, .8, 1, 1.2, 1.6, 2, 2.4, 2.6, 2.8, 3, 3.2]
    add('thickness', 'Board thickness', 'supported' if thickness in thicknesses else 'unknown',
        f'{thickness:g} mm' if thickness else 'Not saved', 'Published standard thicknesses',
        '' if thickness in thicknesses else 'This thickness is outside this profile; request a custom stackup quote.')
    materials = measured.get('materials', [])
    fr4 = bool(materials) and all(m.lower().replace('-', '').replace(' ', '') == 'fr4' for m in materials)
    add('material', 'Board material', 'supported' if fr4 else 'unknown', ', '.join(materials) or 'Not saved',
        'Rigid FR4', '' if fr4 else 'This profile assumes rigid FR4. Confirm the saved stackup material before ordering.')
    copper = measured.get('copper_mm', [])
    copper_matches = bool(copper) and len(copper) == layers and all(v and abs(v - .035 * oz) < .002 for v in copper)
    copper_text = ', '.join(f'{v:g} mm' if v is not None else '?' for v in sorted(set(copper), key=lambda x: x or 0)) or 'Not saved'
    add('copper', 'Copper weight', 'supported' if copper_matches else 'unknown', copper_text + ' in saved stackup',
        f'{oz} oz selected on all copper layers', '' if copper_matches else 'The selected copper weight differs from, or is missing in, the saved stackup. Confirm the stackup; resistance estimates still use the saved thickness.')
    if maker == 'jlcpcb' and selection['finish'] == 'hasl' and ((layers or 0) >= 6 or (thickness is not None and thickness <= .4)):
        add('finish', 'Surface finish', 'outside', finish, 'ENIG or OSP for 6+ layers or ≤0.4 mm boards', 'Choose ENIG or OSP.')
    else:
        add('finish', 'Surface finish', 'supported', finish, 'Listed finish; package suitability is not checked')
    order.extend(['Material: rigid FR4.', f'Layers: {layers or "confirm in KiCad"}.',
                  f'Board thickness: {thickness:g} mm.' if thickness else 'Confirm board thickness.',
                  f'Outer and inner copper: {oz} oz. Match the saved stackup.', f'Surface finish: {finish}.'])
    width = measured.get('min_trace_mm')
    width_limit = (.1 if layers and layers <= 2 else .09) if oz == 1 else (.16 if layers and layers <= 2 else .15)
    if maker == 'pcbway':
        width_limit = .1
    width_known = bool(layers) and (maker == 'jlcpcb' or oz == 1) and not measured.get('invalid_traces')
    add('trace_width', 'Narrowest drawn trace', 'unknown' if not width_known or width is None else 'supported' if width + 1e-7 >= width_limit else 'outside',
        f'{width:g} mm' if width is not None else 'No valid traces measured',
        f'At least {width_limit:g} mm' if width_known else 'No verified limit for these settings',
        'Includes straight and curved tracks; pours and pads are excluded.')
    via_groups = []
    for group in data.get('review', {}).get('groups', []):
        findings = [data['issues'][int(fid[1:])-1] for fid in group.get('issue_ids', [])
                    if fid.startswith('F') and fid[1:].isdigit() and 0 < int(fid[1:]) <= len(data.get('issues', []))]
        # Component holes use different limits; never dismiss their rule findings
        # using a through-via capability, even in a mixed historical group.
        if findings and all(f.get('code') in ('KICAD_DRC_VIA_DIAMETER', 'KICAD_DRC_DRILL_OUT_OF_RANGE', 'KICAD_DRC_ANNULAR_WIDTH')
                            and f.get('items') and all(item.get('description', '').startswith('Via ') for item in f['items'])
                            for f in findings):
            via_groups.append(group['id'])
    for i, via in enumerate(measured.get('vias', [])):
        d, diameter, ring = via['drill_mm'], via['diameter_mm'], via['ring_mm']
        actual = f'{d:g} mm hole / {diameter:g} mm diameter / {ring:g} mm copper ring'
        status, advice = 'supported', ''
        limit = 'Hole ≥0.15 mm; diameter ≥hole + 0.10 mm' if maker == 'jlcpcb' else 'Hole ≥0.15 mm; copper ring ≥0.15 mm (standard service)'
        if not via['through'] or not layers or layers < 2 or oz != 1:
            status, advice = 'unknown', 'This via profile covers through vias on boards with 2+ layers and 1 oz copper. Ask for a process-specific review.'
        elif maker == 'jlcpcb':
            small = d < .15 - 1e-7
            min_hole = .1 if thickness and thickness <= 1 and selection['finish'] in ('enig', 'osp') else .15
            if d + 1e-7 < min_hole or ring + 1e-7 < .05:
                status = 'outside'
                advice = 'Use a hole of at least 0.15 mm and a diameter at least 0.10 mm larger than the hole.'
                if small:
                    advice += ' The 0.10 mm hole process needs a board ≤1.0 mm thick and ENIG or OSP.'
            elif d < .2 - 1e-7 or (d <= .25 + 1e-7 and diameter < .45 - 1e-7):
                status = 'option'
                advice = f'Select the via-size option covering {d:g} mm holes and {diameter:g} mm diameters in the JLCPCB quote. This adds cost.'
                if small:
                    advice += ' Keep board thickness ≤1.0 mm and use ENIG or OSP.'
                order.append(advice)
            if small:
                limit = '0.10 mm holes: board ≤1.0 mm, ENIG/OSP; diameter ≥hole + 0.10 mm'
        else:
            if d + 1e-7 < .15 or d > 6 or ring + 1e-7 < .15:
                status = 'outside'
                advice = f'This is outside PCBWay’s standard profile. For a {d:g} mm hole, a 0.15 mm ring needs a diameter of at least {d + .3:g} mm. Request an advanced-process quote or resize vias and recheck surrounding copper.'
            elif d < .2 - 1e-7:
                status, advice = 'option', 'Request the small-hole option for holes below 0.20 mm; extra charges apply. Confirm the drill-to-thickness ratio with PCBWay.'
                order.append(advice)
        add('via_' + str(i), f'{via["count"]} vias', status, actual, limit, advice, via_groups)
    if measured.get('invalid_vias'):
        add('invalid_vias', 'Via geometry', 'unknown', str(measured['invalid_vias']) + ' unreadable vias', 'Valid drill and diameter required')
    vip = measured.get('via_in_pad', {})
    if vip.get('count'):
        refs = ', '.join(sorted({m['reference'] for m in vip['locations']}))
        fill_option = ('Choose Via Covering: Epoxy-Filled & Capped in the JLCPCB order.' if maker == 'jlcpcb' else
                  'Request resin/epoxy-filled vias, planarized and copper capped (via-in-pad plated over) from PCBWay.')
        advice = fill_option + (' Specify the listed vias and drill sizes in the fabrication notes and have the manufacturer confirm the production files. '
                   'Small-via pricing, tenting or ordinary ink plugging alone does not provide a flat solderable pad. '
                   'Alternatively, move the via holes outside the solder lands and recheck routing. '
                   'For thermal-pad arrays, an assembler-approved open-via/stencil design may be an alternative; it has not been verified here.')
        add('via_in_pad', 'Vias in component solder pads', 'option', f'{vip["count"]} via holes overlap SMD pads: {refs}',
            'Filled, planarized and copper capped for a flat solderable land (VIPPO/POFV).', advice, via_groups)
        drill_sizes = ', '.join(f'{d:g} mm' for d in sorted({m['drill_mm'] for m in vip['locations']}))
        order_steps = [fill_option,
                       f'In the PCB order notes, specify epoxy filling and copper capping for the detected vias ({drill_sizes} drills). Attach the detected pad locations so the factory can identify them.',
                       'Select Confirm production files and check that the listed vias are filled and capped before approving production.' if maker == 'jlcpcb' else
                       'Ask PCBWay to confirm the filling and capping in the production files before approving production.']
        checks[-1].update(source=VIA_SOURCES[maker], verified='2026-10-23' if maker == 'jlcpcb' else VIA_COVERING_VERIFIED,
                         locations=vip['locations'], fill_option=fill_option, order_steps=order_steps)
        if maker == 'jlcpcb' and any(m['drill_mm'] > .5 for m in vip['locations']):
            checks[-1].update(status='outside', advice=advice+' Some holes exceed JLCPCB’s stated 0.5 mm filling limit; request a process review or change the layout.')
        order.append(checks[-1]['advice'])
        order.append('Via-in-pad drill sizes: '+', '.join(f'{d:g} mm' for d in sorted({m['drill_mm'] for m in vip['locations']}))+'. See the detected locations below; filling has not been confirmed in an order.')
    if not vip or vip.get('status') == 'partial':
        add('vip_coverage', 'Via-in-pad check coverage', 'unknown',
            f'{vip.get("unsupported_pads",0)} unsupported pad shapes; {vip.get("unreadable_vias",0)} unreadable vias' if vip else 'Not measured in this saved run',
            'Check drill holes against exposed component pads',
            'Click Check manufacturer to refresh older measurements. Review unsupported pad shapes and final mask/paste artwork separately.')
    elif not vip.get('count'):
        add('vip_coverage', 'Vias in component solder pads', 'supported', 'No drill overlap found in supported exposed SMD lands',
            'Geometric overlap check only; mask expansion and paste artwork excluded')
    gap_limit = .15 if maker == 'jlcpcb' else .1
    for group in data.get('review', {}).get('groups', []):
        context = footprint_rule_context(group, data)
        if not context:
            continue
        gaps = [f['gap_mm'] for f in context['findings'] if f.get('gap_mm') is not None and len({p['net'] for p in f['pads']}) == 2]
        if not gaps:
            continue
        gap = min(gaps)
        status = 'unknown' if oz != 1 else 'supported' if gap + 1e-7 >= gap_limit else 'outside'
        add('gap_' + group['id'], 'Pad spacing · ' + ', '.join(group.get('components', [])), status,
            f'{gap:g} mm minimum in {len(gaps)} reported pad pairs', f'At least {gap_limit:g} mm at 1 oz',
            'These measured pad gaps meet the selected limit; this saved-rule warning needs no pad edit.' if status == 'supported' else
            'Compare the package land pattern with this process. Request a suitable process before moving or shrinking package pads.', [group['id']])
    if oz == 1 and layers and layers >= 2:
        tiny_hole_process = (maker == 'jlcpcb' and thickness and thickness <= 1 and
                             selection['finish'] in ('enig', 'osp') and any(v['drill_mm'] < .15 for v in measured.get('vias', [])))
        min_drill = .1 if tiny_hole_process else .15
        result['kicad_settings'] = [
            f'Minimum track width: {width_limit:g} mm.',
            f'Minimum through-via drill: {min_drill:g} mm. Minimum via diameter: {min_drill + .1:g} mm. Minimum via annular ring: 0.05 mm. These are process minima; the small-via order option is required.' if maker == 'jlcpcb' else
            'Minimum through-via drill: 0.15 mm (extra cost below 0.20 mm). Minimum via annular ring: 0.15 mm; diameter must be at least the drill + 0.30 mm.',
            'Keep electrical and netclass spacing requirements. Do not replace every clearance with the manufacturer minimum.',
            'Component through-hole pads have separate limits. Do not apply the via ring limit to those pads.',
        ]
    counts = Counter(c['status'] for c in checks)
    result['counts'] = dict(counts)
    result['status'] = 'outside' if counts['outside'] else 'option' if counts['option'] else 'checked'
    result['summary'] = (f'{counts["outside"]} measured checks fall outside this profile.' if counts['outside'] else
                         'The checked dimensions need additional ordering options.' if counts['option'] else 'The available measurements have been checked.')
    if counts['unknown']:
        result['summary'] += f" {counts['unknown']} checks still need confirmation."
    return result


def compact_evidence(result):
    if not result or not result.get('selection', {}).get('manufacturer'):
        return {}
    return {'selection': result['selection'], 'status': result['status'], 'verified': result['verified'],
            'source': result.get('profile', {}).get('url'),
            'checks': [{k: c[k] for k in ('label', 'status', 'actual', 'limit', 'advice', 'source')} for c in result.get('checks', [])],
            'order_settings': result.get('order_settings', []),
            'limits': 'Only listed measurements were checked. Not a complete fabrication approval.'}


def apply_manufacturing(plan, data):
    """Use local process evidence after AI/legacy footprint classification."""
    result = data.get('manufacturing', {})
    plan = deepcopy(plan)
    for action in plan.get('actions', []):
        related = [c for c in result.get('checks', []) if action['group_id'] in c.get('group_ids', [])]
        if not related:
            continue
        worst = max(related, key=lambda c: {'supported': 0, 'option': 1, 'unknown': 2, 'outside': 3}[c['status']])
        status = worst['status']
        action['manufacturer_check'] = worst
        # Ordering options require action at the factory, not an automatic CAD edit.
        # A mixed group with any unknown/outside check must retain its review path.
        action['action_kind'] = 'order_setting' if status == 'option' else 'review'
        action['summary'] = f'{result["profile"]["name"]}: {worst["actual"]}. {worst["advice"]}'
        action['reason'] = worst['limit']
        action['basis'] = 'Published manufacturer limits'
        action['steps'] = list(dict.fromkeys(c['advice'] for c in related if c['advice'] and c['status'] != 'supported'))
        action['disposition'] = 'no_change' if status == 'supported' else 'investigate'
        action['reference_only'] = status == 'supported'
        action['consequence'] = {'certainty': 'possible', 'text': 'The selected process may need another ordering option or a design change.' if status != 'supported' else 'These measured dimensions meet the selected process limits.'}
        if status == 'option' and all(c['id'].startswith('via_') and c['id'] != 'via_in_pad' for c in related):
            sizes = [c for c in related if c['status'] == 'option']
            count = sum(int(c['label'].split()[0]) for c in sizes)
            action['title'] = f'Keep the {count} small vias; choose the matching order option'
            action['summary'] = f'{count} vias are below the saved KiCad limits but within {result["profile"]["name"]}’s published small-via dimensions. Their sizes alone do not require a board edit. The order needs the small-via option and costs extra.'
            action['steps'] = ['In Manufacturer → Settings to use when ordering, use a via-size option that covers all the listed hole/diameter pairs. Keep these vias if the extra cost is acceptable.']
            action['reason'] = '; '.join(c['label'] + ': ' + c['actual'] for c in sizes) + '. All meet the selected published limits.'
            action['consequence'] = {'certainty': 'possible', 'text': 'Ordering with the matching small-via option addresses this size concern. A standard-price quote may require a surcharge or a change to the vias. These measurements alone do not show an electrical fault.'}
        vip = next((c for c in related if c['id'] == 'via_in_pad'), None)
        if vip:
            action['title'] = 'Vias in solder pads need a filling and capping process'
            action['summary'] = vip['actual'] + '. Via size and solder-pad processing both need to be addressed.'
            action['steps'] = [vip['fill_option'], *[s for s in action['steps'] if s != vip['advice']],
                               vip['advice'][len(vip['fill_option']):].strip()]
            if status == 'option':
                action['steps'] = [*vip.get('order_steps', [vip['fill_option']]),
                                   *[c['advice'] for c in related if c['id'] != 'via_in_pad' and c['status'] == 'option' and c['advice']]]
                action['summary'] = vip['actual'] + '. Choose the filling and capping process when ordering the PCB. This finding does not require a KiCad file edit.'
            action['manufacturer_check'] = dict(vip, status=status, advice=' '.join(action['steps']))
            action['reason'] = vip['limit']
            action['consequence'] = {'certainty':'possible','text':'An open via hole can draw solder away from the component during assembly, leaving a weak or unreliable joint. A small drill alone does not prevent this; filling and copper capping close the hole and restore a flat soldering surface.'}
        if status != 'supported':
            action.pop('footprint_context', None)
        elif action.get('footprint_context'):
            action['footprint_context']['summary'] = action['summary']
            action['footprint_context']['reason'] = worst['limit']
    return plan

"""Deterministic checks and calculations with explicit, revision-bound assumptions."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import re


I2C_SOURCE = 'https://www.ti.com/lit/pdf/slva689'


def fingerprint(data, target):
    # context_signature stays hashed here on purpose: a filled entry whose
    # operating context changed must go stale and recalculate, never pass
    # silently. Interview task signatures are stabilized separately in
    # review_inputs so unanswered topics survive unrelated answer saves.
    return hashlib.sha256(json.dumps([data.get('provenance', {}).get('source_hashes', {}), target],
                                    sort_keys=True).encode()).hexdigest()


def field(key, label, unit, minimum=0, positive=False):
    return dict(key=key, label=label, unit=unit, minimum=minimum, positive=positive)


def targets(data):
    """Describe inputs without guessing load, bus capacitance, or device limits."""
    result = []
    for bus in data.get('circuit', {}).get('bus_candidates', []):
        pullups = bus.get('parallel_pullups', [])
        if len(pullups) != 1 or any(p.get('ohms') is None for p in bus.get('pullups', [])):
            continue
        resistance = pullups[0].get('ohms')
        if not isinstance(resistance, (int, float)) or not math.isfinite(resistance) or resistance <= 0:
            continue
        result.append(dict(kind='i2c', key='i2c:'+bus['net'], label=bus['net'], section='interfaces',
            resistance=resistance, rail=pullups[0]['rail'],
            description=f'{resistance:g} Ω of external pull-up resistance to {pullups[0]["rail"]}. Enter limits that apply to every device on this line. Include pads, traces, cables and input pins in the capacitance.',
            fields=[field('voltage', 'Highest pull-up supply voltage', 'V', positive=True),
                    field('tolerance', 'Resistor tolerance', '%'),
                    field('capacitance', 'Maximum total line capacitance', 'pF', positive=True),
                    field('rise', 'Maximum allowed rise time', 'ns', positive=True),
                    field('sink', 'Lowest guaranteed sink current across the devices', 'mA', positive=True),
                    field('low', 'Maximum low-level voltage at that sink current', 'V', positive=True)]))
    rails = {}
    def pin_name(pin):
        return re.sub(r'[^A-Z0-9]', '', pin.get('name', '').upper())
    ground_nets = {p.get('net') for c in data.get('components', {}).values()
                   for p in c.get('schematic_pins', []) if re.fullmatch(r'(?:[ADP]?GND\d*|VSS(?:PA|IO|\d+)?)', pin_name(p))}
    for ref, comp in sorted(data.get('components', {}).items()):
        records = comp.get('schematic', [])
        if not records or any(r.get('dnp') or r.get('on_board') is False for r in records):
            continue
        for pin in comp.get('schematic_pins', []):
            net = pin.get('net', '')
            supply = pin.get('electrical_type') == 'power_in' or re.fullmatch(r'(?:[ADO]?VDD(?:IO|H|NC|\d+)?|VCC\d*|VIN\d*|VBUS|VLED)', pin_name(pin))
            if (not net or net in ground_nets or pin.get('no_connect') or not supply
                    or re.search(r'GND|VSS', pin_name(pin))):
                continue
            rails.setdefault(net, {})[ref] = records[0].get('value', '')
    for net, loads in sorted(rails.items()):
        result.append(dict(kind='power', key='power:'+net, label=net, section='power', loads=loads,
            description='Enter simultaneous worst-case current from this rail for each listed component. Include charging, startup and external loads where relevant. Zero is allowed only when the component draws no current in the operating case you describe.',
            fields=[field('available', 'Current the source can safely supply at worst case', 'mA', positive=True),
                    field('extra', 'Additional loads not listed below', 'mA'),
                    field('reserve', 'Required unused current margin', '%')]
                   +[field('load_'+str(i), ref+' · '+value, 'mA') for i,(ref,value) in enumerate(loads.items())]))
    from .engineering_checks import targets as engineering_targets
    result.extend(engineering_targets(data, [t for t in result if t['kind']=='power']))
    for target in result:
        target['signature'] = fingerprint(data, target)
    return result


def validate_values(target, values, note):
    if not isinstance(values, dict) or set(values) != {f['key'] for f in target['fields']}:
        raise ValueError('Fill in every limit for this calculation; leave unknown limits unsaved.')
    if not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise ValueError('Describe the operating case and where these limits came from (up to 2,000 characters).')
    for f in target['fields']:
        value = values[f['key']]
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError('Enter a finite number for '+f['label']+'.')
        if value < f['minimum'] or (f['positive'] and value == 0):
            raise ValueError(f['label']+' must be '+('greater than zero.' if f['positive'] else 'zero or greater.'))
    if target['kind'] == 'i2c':
        if values['tolerance'] >= 100 or values['low'] >= values['voltage']:
            raise ValueError('Tolerance must be below 100%; low-level voltage must be below the supply voltage.')
    if target['kind'] == 'power' and values['reserve'] >= 100:
        raise ValueError('Unused current margin must be below 100%.')
    from .engineering_checks import validate
    validate(target, values)


def calculate(target, values):
    if target['kind'] not in ('i2c','power'):
        from .engineering_checks import calculate as engineering_calculate
        return engineering_calculate(target, values)
    if target['kind'] == 'i2c':
        r = target['resistance']; tolerance = values['tolerance']/100
        actual_rise = .8473*r*(1+tolerance)*values['capacitance']/1000
        sink = (values['voltage']-values['low'])/(r*(1-tolerance))*1000
        ok = actual_rise <= values['rise'] and sink <= values['sink']
        return dict(status='checked' if ok else 'attention',
            detail=f'Calculated rise time {actual_rise:.1f} ns (limit {values["rise"]:g} ns); required sink current {sink:.3f} mA (guaranteed {values["sink"]:g} mA).',
            evidence=['Rise time = 0.8473 × maximum resistance × capacitance.',
                      'Sink current = (maximum supply − allowed low voltage) / minimum resistance.',
                      'Uses resistor tolerance in both directions. External passive pull-ups only; buffers, internal pulls, logic thresholds, addresses and clock stretching need separate checks.', I2C_SOURCE],
            metrics={'rise_ns':actual_rise,'sink_ma':sink})
    demand = values['extra'] + sum(values['load_'+str(i)] for i in range(len(target['loads'])))
    usable = values['available']*(1-values['reserve']/100)
    return dict(status='checked' if demand <= usable else 'attention',
        detail=f'Worst-case demand {demand:g} mA; usable source budget {usable:g} mA after {values["reserve"]:g}% reserve. Margin {usable-demand:g} mA.',
        evidence=['Loads on this rail are counted once per component, including components with several supply pins.',
                  'Budget = source current × (1 − reserve / 100). Demand = listed loads + additional loads.',
                  'This checks the entered current budget. Voltage range, regulator stability, heating, battery charge limits and sequencing require separate checks.'],
        metrics={'demand_ma':demand,'usable_ma':usable,'margin_ma':usable-demand})


def evaluate(data):
    state = data.get('verification', {})
    entries = state.get('entries', {})
    rows = []
    for target in targets(data):
        entry = entries.get(target['key'], {})
        row = dict(target, status='missing', detail='Enter the limits below to calculate this check.', values=entry.get('values', {}), note=entry.get('note', ''))
        from .verification_suggestions import suggestions
        row['suggested_values']=suggestions(data,target)
        if entry:
            if entry.get('signature') != target['signature']:
                row.update(stale=True, detail='The design changed. Review these saved limits and calculate again for this revision.')
            else:
                try:
                    validate_values(target, entry.get('values'), entry.get('note'))
                    row.update(calculate(target, entry['values']))
                except (ValueError, KeyError, TypeError):
                    row['detail'] = 'Saved limits are incomplete. Review and save them again.'
        rows.append(row)
    return {'revision':state.get('revision', 0), 'calculations':rows}


def compact_evidence(data):
    rows = evaluate(data)['calculations']
    return {'results':[{**{k:r[k] for k in ('key','label','kind','status','detail','metrics','note','evidence') if k in r},
                        'inputs':[{ 'label':f['label'],'value':r['values'][f['key']],'unit':f['unit']} for f in r['fields']]}
                       for r in rows if r['status'] in ('checked','attention')],
            'unresolved_calculations':sum(r['status']=='missing' for r in rows),
            'unconfirmed_file_values':[{'target':r['key'],'suggestions':r['suggested_values']} for r in rows if r.get('suggested_values') and r['status']=='missing'],
            'limits':'Calculated results depend on entered limits and operating assumptions. Missing/stale inputs do not pass. No EMC, ESD or RF qualification is implied.'}


def edit(data, request, state):
    target = next((t for t in targets(data) if t['key'] == request.get('target')), None)
    if not target or request.get('signature') != target['signature']:
        raise ValueError('This calculation changed. Refresh the project and choose the current circuit.')
    entries = dict(state.get('entries', {}))
    if request.get('clear') is True:
        entries.pop(target['key'], None)
    else:
        validate_values(target, request.get('values'), request.get('note'))
        from .verification_suggestions import suggestions
        candidates=suggestions(data,target)
        used={k:v for k,v in candidates.items() if request['values'].get(k)==v['value']}
        if used and request.get('confirm_suggestions') is not True:
            raise ValueError('Confirm the values suggested from your files before using them as calculation limits.')
        entries[target['key']] = dict(signature=target['signature'], values=request['values'], note=request['note'].strip(),
                                     confirmed_suggestions=used,
                                     saved_at=datetime.now(timezone.utc).isoformat())
    return {'entries':entries}


def placement_check(data):
    a = data.get('assembly_export', {}); placement = a.get('placement', {})
    pcb = data.get('selected_files', {}).get('pcb')
    digest = data.get('provenance', {}).get('source_hashes', {}).get(pcb)
    if placement.get('status') != 'generated' or not digest or digest != placement.get('pcb_sha256'):
        return 'missing', ['Generate placement files from this saved PCB revision.']
    parts, rows = a.get('parts', []), a.get('cpl', [])
    refs = [p['reference'] for p in parts]
    eligible = {p['reference'] for p in parts if not p.get('placement_excluded')}
    bom = [ref for group in a.get('bom', []) for ref in group.get('references', [])]
    cpl = [r.get('reference') for r in rows]
    problems = list(a.get('blockers', []))
    if Counter(refs) != Counter(bom) or any(n != 1 for n in Counter(refs).values()):
        problems.append('BOM population does not match the fitted component list exactly.')
    if Counter(cpl) != Counter({ref:1 for ref in eligible}):
        problems.append('Placement rows are duplicated, missing, or include excluded parts.')
    for r in rows:
        if (r.get('side') not in ('top', 'bottom') or any(type(r.get(k)) not in (int,float) or not math.isfinite(r[k]) for k in ('x_mm','y_mm','rotation'))
                or not 0 <= r.get('rotation', -1) < 360):
            problems.append('Invalid placement coordinates, side or rotation: '+str(r.get('reference')))
    if a.get('status') == 'blocked' or problems:
        return 'attention', problems
    return 'checked', [f'{len(refs)} fitted components match the BOM; {len(rows)} required placements match exactly.',
                       f'{len(refs)-len(eligible)} footprints intentionally excluded from placement files.',
                       'Coordinates are finite, sides valid and rotations normalized. PCB SHA-256 matches the saved design.',
                       'Assembler orientation and selected orderable parts are checked separately.']


def debug_access(data):
    """Verify direct SWD breakout without inventing access through ICs or vias."""
    components = data.get('components', {}); devices = []; evidence = []; problems = []
    def fitted(c):
        return bool(c.get('pcb')) and not any(r.get('dnp') or r.get('on_board') is False for r in c.get('schematic', []))
    def board_has(c, pin):
        return any(p.get('number') == pin.get('number') and p.get('net') == pin.get('net')
                   for r in c.get('pcb', []) for p in r.get('pads', []))
    for ref,c in components.items():
        pins = {p.get('name', '').upper():p for p in c.get('schematic_pins', [])}
        if not fitted(c) or not {'SWDIO', 'SWDCLK'}.intersection(pins):
            continue
        devices.append(ref)
        if not {'SWDIO','SWDCLK'}.issubset(pins):
            problems.append(ref+': both SWDIO and SWDCLK pins must be identified.');continue
        if pins['SWDIO'].get('net') == pins['SWDCLK'].get('net'):
            problems.append(ref+': SWDIO and SWDCLK share the same net.');continue
        for name in ('SWDIO', 'SWDCLK'):
            pin = pins[name]; access = []
            if pin.get('net') and not pin.get('no_connect') and board_has(c,pin):
                for other, connector in components.items():
                    if not re.fullmatch(r'(?:J|TP)\d+', other) or not fitted(connector):
                        continue
                    for peer in connector.get('schematic_pins', []):
                        if peer.get('net') == pin['net'] and not peer.get('no_connect') and board_has(connector,peer):
                            access.append(other+'.'+peer['number'])
            if access:
                evidence.append(ref+'.'+pin['number']+' '+name+' → '+', '.join(sorted(access))+'; schematic and PCB net agree.')
            else:
                problems.append(ref+' '+name+': no matching direct connector/test-pad connection was verified. Series components or a different debug interface need separate review.')
    if not devices:
        return 'missing', ['No dedicated SWD pin pair identified. Select and verify the programming interface required by this design.']
    return ('partial' if problems else 'checked'), evidence+problems+['Verifies direct SWD signal breakout only. Connector access, ground, target voltage, reset requirements and successful programming still need checking.']

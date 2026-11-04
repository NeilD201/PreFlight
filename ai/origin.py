"""ORIGIN reverse engineering: Living Product Model and intent review.

Stage one rebuilds the probable product architecture from packet facts
alone, never reading ledger claims, so reconstruction cannot narrow
discovery. Stage two reviews collected claims against user-confirmed
intentions, flagging possible contradictions without ever changing a
status or removing a finding. Observed facts, heuristic inferences and
confirmed intentions stay in separate fields.
"""
import hashlib
import re

from .facts import is_supply_net, looks_like_regulator, power_dissipators

VERSION = 1
RECONCILE_PREFIX = 'Does this finding conflict with your stated intent?'

_PURPOSE_SIGNALS = [
    ('radio/wireless function', r'ESP32|NRF5|CC26|SX12|ANTENNA|BLUETOOTH|WI-?FI|RF_AMP|WLAN'),
    ('battery charging or power management', r'CHARGER|MCP738|TP4056|BATTERY|\bBMS\b|FUEL.?GAUGE'),
    ('sensing', r'SENSOR|ACCEL|GYRO|IMU|\bTEMP\b|HUMIDITY|PRESSURE|MAGNETOMETER|ALS\b|ADC'),
    ('microcontroller-based control', r'\bMCU\b|STM32|ESP32|RP2040|ATMEGA|NXP|MICROCHIP|SAMD|GD32'),
    ('host communication', r'\bUSB\b|\bUART\b|RS485|\bCAN\b|ETHERNET|CP210|CH340|FTDI'),
    ('motor or load driving', r'MOTOR|DRIVER|DRV\d|H-BRIDGE|RELAY|SOLENOID|FAN controls?'),
    ('user interface', r'DISPLAY|OLED|LCD|TFT|BUTTON|KEYSWITCH|ENCODER|BUZZER|LED_STRIP'),
]
_TEMP_KEYS = ('temperature', 'temp_range', 'ambient_temp', 'operating_temp', 'max_ambient')
_INTERFACE_PINS = (('I2C', ('SDA', 'SCL')), ('SPI', ('SCK', 'SCLK', 'MOSI', 'MISO', 'SDI', 'SDO')),
                   ('UART', ('TX', 'RX', 'TXD', 'RXD')), ('USB', ('USB_DP', 'USB_DM', 'D+', 'D-')),
                   ('SWD', ('SWDIO', 'SWDCLK')), ('crystal', ('XI', 'XO', 'OSC_IN', 'OSC_OUT')),
                   ('analog', ('ADC', 'AIN')), ('RF', ('ANT', 'RF')))


def _components(packet):
    parts = {}
    for entry in packet.get('components', []):
        ref = entry.get('reference', '')
        pins = [{'number': str(p.get('number', '')), 'name': str(p.get('name', '')),
                 'net': str(p.get('net', ''))} for p in entry.get('pins', [])]
        parts[ref] = {'value': str(entry.get('value', '')), 'footprint': str(entry.get('footprint', '')),
                      'mpn': str(entry.get('mpn', '') or ''), 'dnp': bool(entry.get('dnp')), 'pins': pins}
    return parts


def part_roles(parts):
    """Evidence-based role per fitted part; unknown stays unknown."""
    roles = {}
    for ref, comp in sorted(parts.items()):
        if comp['dnp']:
            continue
        text = f"{comp['value']} {comp['footprint']} {comp['mpn']}".upper()
        names = {p['name'].upper() for p in comp['pins']}
        if ref.startswith(('J', 'P')):
            roles[ref] = 'connector'
        elif ref.startswith(('R', 'C', 'L')):
            roles[ref] = 'passive'
        elif ref.startswith('Y') or names & {'XI', 'XO', 'OSC_IN', 'OSC_OUT'}:
            roles[ref] = 'frequency'
        elif looks_like_regulator(comp['value'], comp['footprint'], comp['mpn']):
            roles[ref] = 'power'
        elif 'ANTENNA' in text or any(n.startswith(('ANT', 'RF_')) for n in names):
            roles[ref] = 'antenna'
        elif ref.startswith(('U', 'Q', 'D')):
            roles[ref] = 'active'
        else:
            roles[ref] = 'unknown'
    return roles


def interfaces(parts):
    """Named-protocol nets with the pin evidence behind each guess."""
    found = []
    for protocol, wanted in _INTERFACE_PINS:
        hits = {}
        for ref, comp in parts.items():
            if comp['dnp']:
                continue
            for pin in comp['pins']:
                name = pin['name'].upper()
                if (name in wanted or (protocol in ('analog', 'RF') and name.startswith(wanted))) and pin['net']:
                    hits.setdefault(pin['net'], []).append(f"{ref}.{pin['number']}")
        if hits:
            found.append({'protocol': protocol, 'nets': sorted(hits),
                          'basis': [f"{net}: {', '.join(sorted(pins))}" for net, pins in sorted(hits.items())]})
    return found


def supply_domains(parts):
    """Supply nets with their fitted loads, from net names."""
    domains = {}
    for ref, comp in parts.items():
        if comp['dnp']:
            continue
        for pin in comp['pins']:
            if pin['net'] and is_supply_net(pin['net']):
                domains.setdefault(pin['net'], set()).add(ref)
    return {net: sorted(refs) for net, refs in sorted(domains.items())}


def purpose_candidates(parts):
    """Low-confidence product guesses with the exact refs behind each one."""
    matches = []
    for purpose, pattern in _PURPOSE_SIGNALS:
        refs = sorted(ref for ref, comp in parts.items() if not comp['dnp'] and re.search(
            pattern, f"{comp['value']} {comp['footprint']} {comp['mpn']} {' '.join(p['name'] for p in comp['pins'])}",
            re.IGNORECASE))
        if refs:
            matches.append({'purpose': purpose, 'confidence': 'low', 'basis_refs': refs})
    return matches


def _question(kind, question, context):
    qid = hashlib.sha256(f'intent-v1:{kind}:{question}'.encode()).hexdigest()[:20]
    return {'id': qid, 'kind': kind, 'question': question, 'context': context}


def build_model(packet):
    """Stage one: reconstruct the product from packet facts, not from claims."""
    from .design_scan import facts as scan_facts
    from .design_scan import subsystems
    parts = _components(packet)
    context = packet.get('design_context', {})
    operating = context.get('operating_context', {})
    operating = operating if isinstance(operating, dict) else {}
    description = str(context.get('description', '') or '').strip()
    intentions = [{'group_id': i.get('group_id', ''), 'note': str(i.get('note', ''))[:500]}
                  for i in context.get('intentions', []) if isinstance(i, dict)]
    model = {'version': VERSION,
             'observed': {'subsystems': subsystems(scan_facts(packet)),
                          'supply_domains': supply_domains(parts),
                          'interfaces': interfaces(parts),
                          'part_roles': part_roles(parts),
                          'fitted_refs': sorted(parts)},
             'confirmed': {'description': description, 'intentions': intentions,
                           'operating_keys': sorted(operating)},
             'inferences': [], 'assumptions': [], 'intent_questions': []}
    if description:
        model['confirmed']['purpose'] = description
    else:
        model['inferences'] = purpose_candidates(parts)
        model['assumptions'].append('No product description is stated; purpose candidates are low-confidence guesses.')
        model['intent_questions'].append(_question(
            'purpose', 'What is this product meant to do?',
            'No description is saved. ' + ('Observed signals suggest: ' + ', '.join(
                f"{c['purpose']} ({', '.join(c['basis_refs'])})" for c in model['inferences']) + '.'
                if model['inferences'] else 'No strong product signals observed.')))
    hot = power_dissipators(parts)
    if hot and not any(operating.get(k) not in (None, '') for k in _TEMP_KEYS):
        model['intent_questions'].append(_question(
            'environment', 'What ambient temperature range must this board tolerate?',
            f"Heating parts observed: {', '.join(sorted(hot))}. No operating temperature is stated."))
    return model


def review_claims(ledger, model):
    """Stage two: flag claims that touch user-confirmed intentions. Never edits status."""
    from .ledger import record_event
    intentions = model.get('confirmed', {}).get('intentions', [])
    noted = {e.get('claim_id') for e in ledger['events'] if e.get('kind') == 'origin_noted'}
    reviewed, flagged = 0, 0
    for claim in ledger['claims']:
        if claim['status'] not in ('supported', 'unresolved') or claim['id'] in noted:
            continue
        reviewed += 1
        refs = claim.get('scope', {}).get('components', [])
        for intention in intentions:
            note = intention.get('note', '')
            hit = next((r for r in refs if re.search(rf'\b{re.escape(r)}\b', note)), None)
            if hit is None:
                continue
            flagged += 1
            excerpt = note[:200]
            claim.setdefault('open_questions', []).append(
                f"{RECONCILE_PREFIX} ({intention.get('group_id', '?')}: {excerpt})")
            record_event(ledger, 'origin_noted',
                         f"{claim['id']} touches {hit}, named in a confirmed intention.",
                         actor='origin', claim_id=claim['id'])
            break
    record_event(ledger, 'origin_reviewed',
                 f"ORIGIN reviewed {reviewed} claims against {len(intentions)} confirmed intentions; "
                 f"{flagged} flagged, none removed.",
                 actor='origin', data={'reviewed': reviewed, 'flagged': flagged})
    return {'reviewed': reviewed, 'flagged': flagged}


def summarize(model):
    """One-line product statement preferring confirmed facts over guesses."""
    if model.get('confirmed', {}).get('purpose'):
        return model['confirmed']['purpose'][:300]
    candidates = model.get('inferences', [])
    if candidates:
        return 'Possibly ' + ', '.join(c['purpose'] for c in candidates[:3]) + ' (unconfirmed guess).'
    return 'Product purpose unknown.'

"""Verified, beginner-friendly component descriptions from KiCad connectivity.

Every phrase is built only from saved schematic symbols, footprints, values,
pin names and nets. Function words such as pull-up or decoupling are used
only when the surrounding topology satisfies a strict rule; anything else
gets a neutral description. Nothing here changes engineering conclusions:
these phrases only change how already-identified parts are named in
findings, questions, chat and fix text.
"""

import re

GROUND_NAMES = {'GND', 'AGND', 'DGND', 'GNDA', 'GNDD', 'PGND', 'SGND'}
SUPPLY_PIN_NAMES = {'VCC', 'VDD', 'V+', 'VDDA', 'VDDD', 'AVCC', 'DVCC', 'VBAT',
                    'VBUS', 'VIN', 'VOUT', 'VREF', 'VCORE', 'VDDIO'}
VOLTAGE_NET = re.compile(r'^[+]?\d+(\.\d+)?V\d*$', re.IGNORECASE)
AUTO_NET = re.compile(r'^Net-\(.+\)$')
REF_PATTERN = re.compile(r'^[A-Z]{1,3}\d{1,4}$')
UNIT_PATTERN = re.compile(r'^(\d+(?:\.\d+)?)([a-zA-Z\xb5\u03bc\u03a9\u2126]+)?$')
RESISTOR_R = re.compile(r'^(\d+)R(\d+)$', re.IGNORECASE)

NAMING_INSTRUCTIONS = (
    "When you name a non-IC part (resistor, capacitor, diode, LED, switch, "
    "connector and similar), use its supplied component description: value, "
    "function, connected pins, signals, nets and the connected chip, keeping "
    "the exact KiCad reference in parentheses, for example 'the 10 k\u03a9 "
    "pull-down resistor (R12) connected between U1 pin RESET and ground'. "
    "Never invent values, functions or connections; when the supplied "
    "description is neutral, keep the sentence neutral."
)


def _records(component):
    return (component.get('schematic') or []) + (component.get('pins') or [])


def kind_noun(ref, symbol, description):
    """Human noun for a reference prefix, refined by symbol/description."""
    prefix = re.match(r'^[A-Z]+', ref or '').group(0) if re.match(r'^[A-Z]+', ref or '') else ''
    text = f"{symbol or ''} {description or ''}".lower()
    if prefix == 'R':
        return 'resistor'
    if prefix == 'C':
        return 'capacitor'
    if prefix == 'L':
        return 'ferrite bead' if 'ferrite' in text or 'bead' in text else 'inductor'
    if prefix == 'D':
        return 'LED' if 'led' in text else 'diode'
    if prefix == 'Q':
        return 'MOSFET' if 'mosfet' in text else 'transistor'
    if prefix in ('SW', 'S', 'K'):
        if 'rotary' in text or 'encoder' in text:
            return 'rotary encoder'
        if 'tact' in text or 'push' in text or 'button' in text:
            return 'push button'
        return 'switch'
    if prefix in ('J', 'P', 'X', 'CN'):
        return 'connector'
    if prefix == 'Y':
        return 'oscillator' if 'oscillator' in text else 'crystal'
    if prefix in ('U', 'F', 'IC'):
        return 'chip'
    if prefix == 'BT':
        return 'battery'
    if prefix in ('TP', 'T'):
        return 'test point'
    return 'part'


def prettify_value(value, noun):
    """Readable value, or '' when the field holds no real value."""
    raw = (value or '').strip().replace('\u03bc', '\u00b5').replace('\u2126', '\u03a9')
    if not raw or raw in ('~', '-', '*'):
        return ''
    if len(raw) <= 2 and not raw[0].isdigit():
        return ''
    if ' ' in raw:
        return ''
    if noun == 'chip':
        return raw  # A chip value is its part number, underscores included.
    if '_' in raw:
        return ''
    if noun == 'resistor':
        match = RESISTOR_R.fullmatch(raw)
        if match:
            return f"{match.group(1)}.{match.group(2)} \u03a9"
        match = re.fullmatch(r'(\d+)([kKmM])(\d+)', raw)
        if match:
            return f"{match.group(1)}.{match.group(3)} {'k' if match.group(2).lower() == 'k' else 'M'}\u03a9"
        match = UNIT_PATTERN.fullmatch(raw)
        core = ((match.group(2) or '') if match else '').replace('\u03a9', '').lower()
        if match and core in ('', 'r', 'k', 'm'):
            number = match.group(1)
            suffix = {'': ' \u03a9', 'r': ' \u03a9', 'k': ' k\u03a9', 'm': ' M\u03a9'}[core]
            return f"{number}{suffix}"
        return raw if any(ch.isdigit() for ch in raw) else ''
    match = UNIT_PATTERN.fullmatch(raw)
    if not match:
        return raw if any(ch.isdigit() for ch in raw) else ''
    number, unit = match.group(1), match.group(2) or ''
    bases = {'capacitor': 'F', 'inductor': 'H', 'ferrite bead': 'H',
             'crystal': 'Hz', 'oscillator': 'Hz'}
    base = bases.get(noun)
    if not base:
        return raw
    if not unit:
        return f"{number} {base}"
    if len(unit) == 1 and unit.lower() in ('p', 'n', 'u', '\u00b5', 'm'):
        gaps = {'p': ' p', 'n': ' n', 'u': ' \u00b5', '\u00b5': ' \u00b5', 'm': ' m'}
        return f"{number}{gaps[unit.lower()]}{base}"
    head, tail = unit[:-1], unit[-1]
    if noun in ('crystal', 'oscillator'):
        if tail.lower() == 'z' and head:
            return f"{number} {head}{tail}"
        return raw
    if tail.lower() != base.lower():
        return raw
    gaps = {'p': ' p', 'n': ' n', 'u': ' \u00b5', '\u00b5': ' \u00b5', 'm': ' m'}
    gap = gaps.get(head.lower(), '')
    if head and not gap:
        return raw
    return f"{number}{gap}{base}"


def is_ground_net(net):
    upper = (net or '').upper()
    return upper in GROUND_NAMES or upper.endswith('GND')


def pin_index(components):
    """(reference, pin number) -> pin name for every schematic pin."""
    index = {}
    for ref, component in (components or {}).items():
        for pin in component.get('schematic_pins') or []:
            if pin.get('number'):
                index[(ref, str(pin['number']))] = pin.get('name') or ''
    return index


def is_supply_net(net, components):
    upper = (net or '').upper()
    if upper in SUPPLY_PIN_NAMES or VOLTAGE_NET.match(upper or ''):
        return True
    for ref, component in (components or {}).items():
        for pin in component.get('schematic_pins') or []:
            if pin.get('net') == net and (pin.get('name') or '').upper() in SUPPLY_PIN_NAMES:
                return True
    return False


def _is_ic(ref):
    return bool(re.match(r'^(U|F|IC)\d', ref or ''))


def _short_part(part):
    """Strip a trailing package suffix such as _ML or _AU from a part number."""
    if '_' in (part or ''):
        head, _, tail = part.rpartition('_')
        if head and re.fullmatch(r'[A-Z0-9]{1,4}', tail or '') and any(ch.isdigit() for ch in head):
            return head
    return part or ''


def _part_name(components, ref):
    component = (components or {}).get(ref, {})
    records = component.get('schematic') or component.get('pcb') or []
    return _short_part(records[0].get('value') or '') if records else ''


def endpoints_for(project, ref):
    """Ordered connection endpoints with verified neighbours on each net."""
    components = project.get('components') or {}
    component = components.get(ref)
    if not component:
        return []
    nets = project.get('schematic_nets') or {}
    index = pin_index(components)
    seen, endpoints = set(), []
    for pin in component.get('schematic_pins') or []:
        net = pin.get('net') or ''
        if not net or net in seen:
            continue
        seen.add(net)
        others = []
        for endpoint in nets.get(net) or []:
            if '.' not in endpoint:
                continue
            other_ref, other_pin = endpoint.split('.', 1)
            if other_ref == ref:
                continue
            others.append({'ref': other_ref, 'pin': other_pin,
                           'name': index.get((other_ref, other_pin), ''),
                           'part': _part_name(components, other_ref)})
        others.sort(key=lambda o: (not _is_ic(o['ref']), o['ref'], o['pin']))
        endpoints.append({'net': net, 'ground': is_ground_net(net),
                          'supply': is_supply_net(net, components), 'others': others})
    # Signal endpoints first, then ground, then supplies.
    endpoints.sort(key=lambda e: (e['ground'] or e['supply'],
                                  not any(_is_ic(o['ref']) for o in e['others'])))
    return endpoints


def _clean_pin_name(name):
    """Strip KiCad inversion markup such as ~{RESET} or a leading ~ or /."""
    text = (name or '').strip()
    text = re.sub(r'~\{([^}]*)\}', r'\1', text)
    return text.lstrip('~/') or name


def _render_other(other):
    name = _clean_pin_name(other.get('name') or other.get('pin'))
    part = f" ({other['part']})" if other.get('part') else ''
    return f"{other['ref']}{part} pin {name}"


def _render_endpoint(endpoint):
    if endpoint['ground']:
        return 'ground'
    if endpoint['supply'] and endpoint['others'] and not AUTO_NET.match(endpoint['net']):
        return endpoint['net']
    if endpoint['others']:
        grouped = {}
        for other in endpoint['others']:
            grouped.setdefault((other['ref'], other.get('part') or ''), []).append(
                other.get('name') or other.get('pin'))
        first = next(iter(grouped))
        pins = ' pin ' + ', '.join(grouped[first]) if len(grouped) == 1 else ''
        if len(grouped) == 1 and _is_ic(first[0]):
            part = f" ({first[1]})" if first[1] else ''
            pin_word = 'pin' if len(grouped[first]) == 1 else 'pins'
            return f"{first[0]}{part} {pin_word} {', '.join(grouped[first])}"
        if not AUTO_NET.match(endpoint['net']) and len(endpoint['others']) > 1:
            return endpoint['net']
        return _render_other(endpoint['others'][0])
    if not AUTO_NET.match(endpoint['net']):
        return endpoint['net']
    return ''


def _function(noun, endpoints):
    nets = [e for e in endpoints if e['net']]
    if noun == 'resistor' and len(nets) == 2:
        ground = [e for e in nets if e['ground']]
        live = [e for e in nets if not e['ground']]
        if len(ground) == 1 and any(_is_ic(o['ref']) for o in live[0]['others']):
            return 'pull-down'
        if not ground and any(e['supply'] for e in nets):
            signal = [e for e in nets if not e['supply']]
            if signal and any(_is_ic(o['ref']) for o in signal[0]['others']):
                return 'pull-up'
    if noun == 'decoupling capacitor' or (noun == 'capacitor' and len(nets) == 2
            and sum(e['ground'] for e in nets) == 1 and sum(e['supply'] for e in nets) == 1):
        return 'decoupling capacitor'
    return ''


def describe_component(project, ref):
    """Verified phrase data for one reference, or None when evidence is missing."""
    components = project.get('components') or {}
    component = components.get(ref)
    if not component or not REF_PATTERN.match(ref or ''):
        return None
    records = component.get('schematic') or component.get('pcb') or []
    record = records[0] if records else {}
    noun = kind_noun(ref, record.get('symbol'), (record.get('properties') or {}).get('Description'))
    value = prettify_value(record.get('value'), noun)
    if noun == 'chip':
        value = _short_part(value)
    endpoints = endpoints_for(project, ref)
    function = _function(noun, endpoints)
    label = function or noun
    short = f"the {value + ' ' if value else ''}{label} ({ref})"
    signals = [e for e in endpoints if not e['ground']]
    ground = [e for e in endpoints if e['ground']]
    # Merge pins of the same connected chip across endpoints, keep ground visible.
    chip_groups, singles = {}, []
    for endpoint in signals:
        if endpoint['supply']:
            text = _render_endpoint(endpoint)
            if text:
                singles.append(text)
            continue
        chip_others = [o for o in endpoint['others'] if _is_ic(o['ref'])]
        if chip_others and len(chip_others) == len(endpoint['others']):
            key = (chip_others[0]['ref'], chip_others[0]['part'])
            pins = chip_groups.setdefault(key, [])
            for other in chip_others:
                name = _clean_pin_name(other['name'] or other['pin'])
                if name not in pins:
                    pins.append(name)
        else:
            text = _render_endpoint(endpoint)
            if text:
                singles.append(text)
    merged = []
    for (chip_ref, chip_part), pins in chip_groups.items():
        part = f" ({chip_part})" if chip_part else ''
        pin_word = 'pin' if len(pins) == 1 else 'pins'
        merged.append(f"{chip_ref}{part} {pin_word} {', '.join(pins)}")
    merged.extend(singles)
    dropped = len(merged) - 2 if len(merged) > 2 else 0
    merged = merged[:2]
    if ground:
        merged.append('ground')
    more = f" (plus {dropped} more connection{'s' if dropped > 1 else ''})" if dropped else ''
    pin_count = len(component.get('schematic_pins') or [])
    if len(endpoints) == 2 and len(merged) == 2 and pin_count <= 2 and not ground:
        full = f"{short} connected between {merged[0]} and {merged[1]}"
    elif len(merged) == 2 and ground and merged[-1] == 'ground':
        full = f"{short} connected between {merged[0]} and ground"
    elif len(merged) == 1:
        full = f"{short} connected to {merged[0]}"
    elif merged:
        full = f"{short} with connections to {', '.join(merged[:-1])} and {merged[-1]}{more}"
    else:
        full = short
    return {'ref': ref, 'noun': noun, 'value': value, 'function': function,
            'short': short, 'phrase': full,
            'endpoints': [{'net': e['net'], 'ground': e['ground'],
                           'others': [[o['ref'], o['pin']] for o in e['others']]} for e in endpoints]}


def describe_all(project):
    """Phrase data for every recorded component, keyed by reference."""
    return {ref: described for ref in (project.get('components') or {})
            if (described := describe_component(project, ref)) is not None}


def _record_field(record, name, default=''):
    if record is None:
        return default
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def display_name(ref, component):
    """Short human label such as 'USB-C connector (J1)' or '4.7 kΩ resistor (R16)'.

    Accepts Component objects or plain dicts. Never invents a purpose: unknown
    connectors stay 'connector (Jx)', push-button text wins over the RST prefix,
    and default KiCad values (value == symbol, or symbol-like names) are dropped.
    """
    records = _record_field(component, 'schematic', []) or _record_field(component, 'pcb', [])
    record = records[0] if records else None
    symbol = _record_field(record, 'symbol') or ''
    value = (_record_field(record, 'value') or '').strip()
    properties = _record_field(record, 'properties', {}) or {}
    description = properties.get('Description', '') if isinstance(properties, dict) else ''
    noun = kind_noun(ref, symbol, description)
    text = f'{symbol} {description} {value}'.lower()
    if noun == 'connector' and 'usb' in text:
        noun = 'USB-C connector' if ('type-c' in text or 'usb_c' in text) else 'USB connector'
    if noun == 'part' and ('push button' in text or 'tact' in text or (ref or '').startswith('RST')):
        noun = 'push button'
    if noun == 'chip' and ('microcontroller' in text or 'regulator' in text or 'ld1117' in text):
        noun = 'microcontroller' if 'microcontroller' in text else 'voltage regulator'
    shown = ''
    if value and value not in ('~', '-', '*') and value != symbol:
        if noun == 'chip':
            shown = _short_part(value)
        elif '_' in value:
            shown = ''
        else:
            shown = prettify_value(value, noun)
    if shown:
        return f'{shown} {noun} ({ref})'
    return f'{noun} ({ref})'

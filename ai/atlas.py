"""Engineering Failure Atlas: taxonomy-driven hypothesis discovery.

Each mode pairs a stable identifier with applicability logic over observed
design facts. Triggered modes record ledger claims; quiet modes record why
they stayed out. Uncertainty produces a candidate investigation, never a
silent exclusion or a verdict. New domains extend the registry without
rewriting the discovery engine.
"""
import re

from .facts import is_ground_net as _is_ground
from .facts import is_supply_net as _is_supply
from .facts import is_unconnected_net as _synthetic_open
from .facts import power_dissipators as _shared_dissipators

VERSION = 1
MAX_CLAIMS = 40

_MODES = []


def register(entry):
    """Add a domain failure mode. Required keys: id, category, title, assess."""
    if not all(k in entry for k in ('id', 'category', 'title', 'assess')):
        raise ValueError('Atlas entries need id, category, title and assess.')
    if any(m['id'] == entry['id'] for m in _MODES):
        raise ValueError(f"Duplicate atlas mode: {entry['id']}")
    _MODES.append(entry)
    return entry


def modes():
    return list(_MODES)


def _ohms(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([kKmM])?\s*(?:ohms?|Ω)?\s*', str(value or ''))
    if match:
        return float(match[1]) * {'k': 1e3, 'm': 1e6}.get((match[2] or '').lower(), 1)
    match = re.fullmatch(r'\s*(\d+)[kK](\d+)\s*', str(value or ''))
    if match:
        return float(match[1]) * 1e3 + float(match[2])
    return None





def design_facts(packet):
    """Observed components, nets and coverage. Unknowns stay unknown."""
    components, nets = {}, {}
    for entry in packet.get('components', []):
        ref = entry.get('reference', '')
        pins = [{'number': str(p.get('number', '')), 'name': str(p.get('name', '')),
                 'net': str(p.get('net', '')),
                 'no_connect': bool(p.get('no_connect'))} for p in entry.get('pins', [])]
        components[ref] = {'value': str(entry.get('value', '')), 'footprint': str(entry.get('footprint', '')),
                           'mpn': str(entry.get('mpn', '') or ''), 'dnp': bool(entry.get('dnp')),
                           'pins': pins, 'nets': sorted({p['net'] for p in pins if p['net']})}
        for pin in pins:
            if pin['net']:
                nets.setdefault(pin['net'], []).append({'ref': ref, 'number': pin['number'], 'name': pin['name']})
    for net in packet.get('nets', []):
        nets.setdefault(net.get('name', ''), [])
    return {'components': components, 'nets': nets,
            'coverage': {p.get('reference'): {'status': p.get('status'), 'mpn': p.get('mpn'),
                                              'code': p.get('code')} for p in packet.get('datasheet_coverage', [])},
            'bus': packet.get('bus_candidates', []), 'bypass': packet.get('bypass_capacitors', [])}


def _resistors_to_supply(facts, net):
    found = []
    for member in facts['nets'].get(net, []):
        ref = member['ref']
        if not ref.startswith('R'):
            continue
        other = [n for n in facts['components'][ref]['nets'] if n != net]
        if any(_is_supply(n) for n in other):
            found.append(ref)
    return sorted(set(found))


def _finding(scope, standing, basis, missing=(), assumptions=()):
    return {'scope': {'components': sorted(scope.get('components', ())), 'nets': sorted(scope.get('nets', ())),
                       'pins': sorted(scope.get('pins', ()))},
            'standing': standing, 'basis': basis, 'missing': list(missing), 'assumptions': list(assumptions)}


def _assess_power_decoupling(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        if not ref.startswith('U') or comp['dnp']:
            continue
        for pin in comp['pins']:
            if not _is_supply(pin['net']):
                continue
            caps = [m['ref'] for m in facts['nets'].get(pin['net'], []) if m['ref'].startswith('C')]
            if caps:
                notes.append(f"{ref} {pin['net']}: {', '.join(sorted(set(caps)))} observed on the supply net.")
            else:
                findings.append(_finding({'components': [ref], 'nets': [pin['net']], 'pins': [f"{ref}.{pin['number']}"]},
                    'uncertain', f'{ref} pin {pin["number"]} ({pin["net"]}) has no visible decoupling capacitor.',
                    missing=['Required capacitance and placement from the part datasheet.'],
                    assumptions=['Net names identify supply rails; unlisted rails are missed.']))
    if not findings and not notes:
        notes.append('No fitted IC supply pins observed.')
    return findings, notes


def _assess_regulator(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        text = f"{comp['value']} {comp['footprint']} {comp['mpn']}".upper()
        if comp['dnp'] or not re.search(r'\b(LDO|REGULATOR|BUCK|BOOST|DCDC|DC-DC|AMS1117|LM317|AP2112|TP4056|MCP1700)\b', text):
            continue
        findings.append(_finding({'components': [ref]}, 'uncertain',
            f'{ref} ({comp["value"] or "regulator"}) needs input/output voltage, load current and dropout checks.',
            missing=['Input and output voltages under load.', 'Maximum load current.', 'Dropout and thermal data.']))
    if not findings:
        notes.append('No regulator-like parts identified by value, footprint or MPN.')
    return findings, notes


def _assess_supply_loading(facts):
    findings, notes = [], []
    for net, members in sorted(facts['nets'].items()):
        if not _is_supply(net):
            continue
        loads = sorted({m['ref'] for m in members if m['ref'][:1] in 'UQYM' and not facts['components'][m['ref']]['dnp']})
        if len(loads) >= 2:
            findings.append(_finding({'components': loads, 'nets': [net]}, 'uncertain',
                f'Supply net {net} feeds {len(loads)} parts; no current budget is established.',
                missing=['Per-part load currents and the supply current limit.']))
        else:
            notes.append(f'Supply net {net}: fewer than two fitted loads observed.')
    if not [n for n in facts['nets'] if _is_supply(n)]:
        notes.append('No supply rails identified by net name.')
    return findings, notes


def _assess_supply_protection(facts):
    findings, notes = [], []
    for net, members in sorted(facts['nets'].items()):
        if not _is_supply(net) or not any(m['ref'][:1] in 'JP' for m in members):
            continue
        guards = [m['ref'] for m in members if m['ref'].startswith(('D', 'F'))]
        if guards:
            notes.append(f'{net}: {", ".join(sorted(set(guards)))} observed on the exposed supply.')
        else:
            refs = sorted({m['ref'] for m in members})
            findings.append(_finding({'components': refs, 'nets': [net]}, 'uncertain',
                f'Exposed supply net {net} has no visible reverse-polarity or overvoltage part.',
                missing=['Whether the connector can receive external power.', 'Required protection ratings.']))
    if not findings and not notes:
        notes.append('No connector-fed supply nets observed.')
    return findings, notes


def _i2c_nets(facts):
    hit = set()
    for net, members in facts['nets'].items():
        if net.upper() in ('SDA', 'SCL') or net.upper().startswith(('SDA_', 'SCL_')):
            hit.add(net)
        if any(m['name'].upper() in ('SDA', 'SCL') for m in members):
            hit.add(net)
    return sorted(hit)


def _assess_i2c_pullups(facts):
    findings, notes = [], []
    for net in _i2c_nets(facts):
        pulls = _resistors_to_supply(facts, net)
        if pulls:
            def _label(ref):
                ohms = _ohms(facts['components'][ref]['value'])
                return f"{ref} ({facts['components'][ref]['value'] or '?'}" + (f" = {ohms:g} ohms)" if ohms else ")")
            notes.append(f"{net}: pull-up {', '.join(_label(r) for r in pulls)} to supply observed; "
                         'value still needs rise-time validation.')
        else:
            pins = [f"{m['ref']}.{m['number']}" for m in facts['nets'][net]]
            findings.append(_finding({'components': sorted({m['ref'] for m in facts['nets'][net]}),
                                      'nets': [net], 'pins': pins}, 'applicable',
                f'I2C net {net} has no visible pull-up resistor to a supply rail.',
                missing=['Whether any driver provides an internal pull-up.']))
    if not [n for n in _i2c_nets(facts) if n in facts['nets']]:
        notes.append('No SDA/SCL nets observed.')
    return findings, notes


def _assess_i2c_timing(facts):
    findings, notes = [], []
    for net in _i2c_nets(facts):
        if not _resistors_to_supply(facts, net):
            continue
        findings.append(_finding({'nets': [net]}, 'uncertain',
            f'I2C net {net} pull-up value is unverified against bus speed and capacitance.',
            missing=['Bus clock rate and total bus capacitance.', 'Driver sink-current rating.']))
    if not findings:
        notes.append('No I2C nets with pull-ups need timing validation.')
    return findings, notes


def _nets_with_pin_names(facts, *wanted):
    hits = {}
    for ref, comp in facts['components'].items():
        if comp['dnp']:
            continue
        for pin in comp['pins']:
            if pin['name'].upper() in wanted and pin['net']:
                hits.setdefault(pin['net'], set()).add(ref)
    return {net: sorted(refs) for net, refs in hits.items()}


def _assess_uart_levels(facts):
    findings, notes = [], []
    for net in sorted(_nets_with_pin_names(facts, 'TX', 'RX', 'TXD', 'RXD')):
        parties = sorted({m['ref'] for m in facts['nets'].get(net, [])})
        if len(parties) >= 2:
            findings.append(_finding({'components': parties, 'nets': [net]}, 'uncertain',
                f'UART net {net} joins {", ".join(parties)}; logic-level compatibility is unverified.',
                missing=['IO supply voltage of each side.']))
    if not findings:
        notes.append('No multi-part UART nets observed.')
    return findings, notes


def _assess_usb(facts):
    findings, notes = [], []
    hit = _nets_with_pin_names(facts, 'USB_DP', 'USB_DM', 'D+', 'D-') or \
        {n: sorted({m['ref'] for m in members}) for n, members in facts['nets'].items()
         if n.upper() in ('USB_DP', 'USB_DM', 'D+', 'D-')}
    connected = {n: r for n, r in hit.items() if not _synthetic_open(n)}
    for net, refs in sorted(connected.items()):
        findings.append(_finding({'components': refs, 'nets': [net]}, 'uncertain',
            f'USB data net {net} needs impedance, termination and layout review.',
            missing=['Stackup and trace geometry.', 'USB speed and connector wiring.']))
    opened = sorted({r for n, r in hit.items() if _synthetic_open(n) for r in r})
    if opened and not connected:
        findings.append(_finding({'components': opened}, 'uncertain',
            f"USB data pins on {', '.join(opened)} are unconnected; confirm USB is intentionally unused.",
            missing=['Whether USB is required.', 'Termination needs of unused USB pins.']))
    if not findings:
        notes.append('No USB data nets observed.')
    return findings, notes


def _assess_spi(facts):
    findings, notes = [], []
    for net in sorted(_nets_with_pin_names(facts, 'SCK', 'SCLK', 'MOSI', 'MISO', 'SDI', 'SDO')):
        parties = sorted({m['ref'] for m in facts['nets'].get(net, [])})
        if len(parties) >= 2:
            findings.append(_finding({'components': parties, 'nets': [net]}, 'uncertain',
                f'SPI net {net} joins {", ".join(parties)}; topology and speed effects are unverified.',
                missing=['Bus clock rate and trace lengths.']))
    if not findings:
        notes.append('No multi-part SPI nets observed.')
    return findings, notes


def _assess_adc_reference(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        if comp['dnp']:
            continue
        pins = [p for p in comp['pins'] if p['name'].upper().startswith(('ADC', 'AIN')) and p['net']]
        if pins:
            findings.append(_finding({'components': [ref], 'nets': sorted({p['net'] for p in pins}),
                                      'pins': [f"{ref}.{p['number']}" for p in pins]}, 'uncertain',
                f'{ref} analog inputs need reference-voltage and filtering review.',
                missing=['Reference source and accuracy needs.', 'Expected signal bandwidth.']))
    if not findings:
        notes.append('No ADC/AIN pins observed.')
    return findings, notes


_XTAL_PINS = ('XI', 'XO', 'XC1', 'XC2', 'XL1', 'XL2', 'XTAL1', 'XTAL2',
                'XTAL_IN', 'XTAL_OUT', 'OSC_IN', 'OSC_OUT')


def _assess_crystal_load(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        if comp['dnp']:
            continue
        pins = [p for p in comp['pins'] if p['name'].upper() in _XTAL_PINS and p['net']]
        if not pins:
            continue
        caps = [m['ref'] for p in pins for m in facts['nets'].get(p['net'], []) if m['ref'].startswith('C')]
        if caps:
            notes.append(f"{ref} crystal nets: {', '.join(sorted(set(caps)))} observed; values need datasheet checks.")
        else:
            findings.append(_finding({'components': [ref], 'nets': sorted({p['net'] for p in pins})}, 'applicable',
                f'{ref} crystal pins have no visible load capacitors.',
                missing=['Whether the oscillator is internal or the crystal provides its own loading.']))
    for ref, comp in sorted(facts['components'].items()):
        if not ref.startswith('Y') or comp['dnp']:
            continue
        wired = [p for p in comp['pins'] if p['net'] and not _synthetic_open(p['net'])]
        fed = sorted({p['net'] for p in wired if _is_supply(p['net'])})
        signals = sorted({p['net'] for p in wired
                          if not _is_supply(p['net']) and not _is_ground(p['net'])})
        text = f"{comp['value']} {comp['footprint']} {comp['mpn']}".upper()
        if fed and ('CRYSTAL' in text or 'XTAL' in text):
            findings.append(_finding({'components': [ref], 'nets': fed}, 'uncertain',
                f"{ref} ({comp['value'] or 'unmarked'}) is marked as a crystal but connects to "
                f"supply {', '.join(fed)}; confirm oscillator versus crystal and the clock wiring.",
                missing=['Exact part number and datasheet.', 'Whether the driven clock input is compatible.']))
        elif fed:
            notes.append(f'{ref} connects to supply {", ".join(fed)}; verify the oscillator part.')
        elif len(signals) < 2:
            findings.append(_finding({'components': [ref], 'nets': signals}, 'uncertain',
                f'{ref} crystal shows {len(signals)} connected signal nets; confirm both terminals are wired.',
                missing=['Whether the second terminal is intentionally unused.']))
        else:
            notes.append(f"{ref} crystal terminals: {', '.join(signals)} observed.")
    if not findings and not notes:
        notes.append('No crystal pins observed.')
    return findings, notes


def _assess_reset(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        if comp['dnp']:
            continue
        for pin in comp['pins']:
            if pin['name'].upper() not in ('RESET', 'RST', 'NRST', 'NRESET') or not pin['net']:
                continue
            pulls = _resistors_to_supply(facts, pin['net'])
            if pulls:
                notes.append(f"{ref} reset net {pin['net']}: pull {', '.join(pulls)} observed.")
            else:
                findings.append(_finding({'components': [ref], 'nets': [pin['net']],
                                          'pins': [f"{ref}.{pin['number']}"]}, 'uncertain',
                    f'{ref} reset net {pin["net"]} has no visible pull resistor.',
                    missing=['Whether the part provides an internal pull or supervisor.']))
    if not findings and not notes:
        notes.append('No reset pins observed.')
    return findings, notes


def _assess_boot_strapping(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        if comp['dnp']:
            continue
        pins = [p for p in comp['pins'] if p['name'].upper().startswith(('BOOT', 'CFG', 'STRAP')) and p['net']]
        if pins:
            findings.append(_finding({'components': [ref], 'nets': sorted({p['net'] for p in pins}),
                                      'pins': [f"{ref}.{p['number']}" for p in pins]}, 'uncertain',
                f'{ref} boot/configuration strapping levels are unverified against the required mode.',
                missing=['Required boot mode and strapping table.']))
    if not findings:
        notes.append('No boot/configuration pins observed.')
    return findings, notes


def _assess_esd_exposed(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        if not ref[:1] in 'JP' or comp['dnp']:
            continue
        for pin in comp['pins']:
            if not pin['net'] or _is_supply(pin['net']) or _is_ground(pin['net']):
                continue
            guards = [m['ref'] for m in facts['nets'].get(pin['net'], [])
                      if m['ref'].startswith('D') or 'TVS' in facts['components'][m['ref']]['value'].upper()
                      or 'ESD' in facts['components'][m['ref']]['value'].upper()]
            if guards:
                notes.append(f"{ref}.{pin['number']} net {pin['net']}: {', '.join(sorted(set(guards)))} observed.")
            else:
                findings.append(_finding({'components': [ref], 'nets': [pin['net']],
                                          'pins': [f"{ref}.{pin['number']}"]}, 'uncertain',
                    f'Exposed net {pin["net"]} on {ref} has no visible ESD protection part.',
                    missing=['Whether the pin is user- or cable-accessible.', 'Required protection level.']))
    if not findings and not notes:
        notes.append('No connector signal nets observed.')
    return findings, notes


def _assess_floating_input(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        if comp['dnp']:
            continue
        for pin in comp['pins']:
            if pin['net'] and not _synthetic_open(pin['net']):
                continue
            label = f"{ref}.{pin['number']}"
            if pin.get('no_connect'):
                notes.append(f'{label} ({pin["name"] or "unnamed"}) is marked no-connect.')
                continue
            if (pin['name'] or '').upper().replace('/', '').replace('-', '') in ('NC', 'DNC', 'NOTCONNECTED') \
                    or (pin['name'] or '').upper().startswith('NC_'):
                notes.append(f'{label} is a no-connect pin by name.')
                continue
            findings.append(_finding({'components': [ref], 'pins': [label]}, 'applicable',
                f'{ref} pin {pin["number"]} ({pin["name"] or "unnamed"}) has no net; confirm it is intentionally unconnected.',
                missing=['Whether the pin is an input that must not float.']))
    if not findings and not notes:
        notes.append('No unconnected fitted pins observed.')
    return findings, notes


def _power_dissipators(facts):
    return _shared_dissipators(facts['components'])


def _assess_thermal(facts):
    findings, notes = [], []
    for ref in _power_dissipators(facts):
        findings.append(_finding({'components': [ref]}, 'uncertain',
            f'{ref} may dissipate significant power; temperature rise is unverified.',
            missing=['Operating power and duty cycle.', 'Thermal resistance and copper spreading.']))
    if not findings:
        notes.append('No power-dissipating parts identified by value, footprint or MPN.')
    return findings, notes


def _assess_antenna(facts):
    findings, notes = [], []
    for net in sorted(facts['nets']):
        if net.upper().startswith(('ANT', 'RF_', 'RF-')) or net.upper() in ('RF',):
            refs = sorted({m['ref'] for m in facts['nets'][net]})
            findings.append(_finding({'components': refs, 'nets': [net]}, 'uncertain',
                f'RF net {net} needs feed, matching and layout review.',
                missing=['Antenna type and matching network.', 'Reference design requirements.']))
    for ref, comp in sorted(facts['components'].items()):
        if not comp['dnp'] and 'ANTENNA' in f"{comp['value']} {comp['footprint']} {comp['mpn']}".upper():
            findings.append(_finding({'components': [ref]}, 'uncertain',
                f'{ref} is antenna-related; its feed and keep-out need review.',
                missing=['Antenna datasheet and placement rules.']))
    if not findings:
        notes.append('No RF nets or antenna parts observed.')
    return findings, notes


def _assess_high_speed(facts):
    findings, notes = [], []
    for net, members in sorted(facts['nets'].items()):
        if not re.search(r'USB|HDMI|ETH|PCIE|DDR|MIPI|SDIO|RMII|RGMII', net.upper()):
            continue
        if _synthetic_open(net):
            notes.append(f'{net}: unconnected pad, not a routed net.')
            continue
        active = sorted({m['ref'] for m in members if m['ref'][:1] in 'UQJY'})
        if len(active) < 2:
            notes.append(f'{net}: fewer than two active endpoints; likely a decoupling or test net.')
            continue
        findings.append(_finding({'components': sorted({m['ref'] for m in members}), 'nets': [net]}, 'uncertain',
            f'High-speed net {net} needs impedance and termination review.',
            missing=['Stackup, trace lengths and signaling rate.']))
    if not findings and not notes:
        notes.append('No high-speed nets identified by name.')
    return findings, notes


def _assess_part_identity(facts):
    findings, notes = [], []
    for ref, comp in sorted(facts['components'].items()):
        if not ref.startswith(('U', 'Q')) or comp['dnp']:
            continue
        cover = facts['coverage'].get(ref, {})
        if cover.get('code') and cover.get('status') in ('ready', 'provisional'):
            notes.append(f'{ref}: matched document {cover["code"]} ({cover["status"]}).')
        elif comp['mpn']:
            notes.append(f'{ref}: MPN {comp["mpn"]} declared without a matched document.')
            findings.append(_finding({'components': [ref]}, 'uncertain',
                f'{ref} MPN {comp["mpn"]} has no matched datasheet; variant requirements are unverified.',
                missing=['Manufacturer datasheet for the exact variant.']))
        else:
            findings.append(_finding({'components': [ref]}, 'uncertain',
                f'{ref} ({comp["value"] or "unmarked"}) has no matched manufacturer part number; specifications cannot be checked.',
                missing=['Manufacturer part number and variant.']))
    if not findings and not notes:
        notes.append('No fitted U/Q parts observed.')
    return findings, notes


def _assess_switching_near_analog(facts):
    findings, notes = [], []
    switchers = [r for r in _power_dissipators(facts)
                 if re.search(r'\b(BUCK|BOOST|DCDC|DC-DC)\b',
                              f"{facts['components'][r]['value']} {facts['components'][r]['mpn']}".upper())]
    analog = [ref for ref, comp in facts['components'].items() if not comp['dnp']
              and any(p['name'].upper().startswith(('ADC', 'AIN')) for p in comp['pins'])]
    if switchers and analog:
        findings.append(_finding({'components': sorted(set(switchers + analog))}, 'uncertain',
            f'Switching supplies ({", ".join(switchers)}) share the board with analog inputs ({", ".join(sorted(analog))}); coupling is unverified.',
            missing=['Placement distance and return-path layout.']))
    else:
        notes.append('No switching-supply plus analog-input combination observed.')
    return findings, notes


def _assess_shared_supply(facts):
    findings, notes = [], []
    for net, members in sorted(facts['nets'].items()):
        if not _is_supply(net):
            continue
        analog = sorted({m['ref'] for m in members
                         if any(p['name'].upper().startswith(('ADC', 'AIN', 'VREF'))
                                for p in facts['components'][m['ref']]['pins'])})
        digital = sorted({m['ref'] for m in members if m['ref'] not in analog
                          and m['ref'].startswith('U') and not facts['components'][m['ref']]['dnp']})
        if analog and digital:
            findings.append(_finding({'components': sorted(set(analog + digital)), 'nets': [net]}, 'uncertain',
                f'Supply net {net} is shared by analog ({", ".join(analog)}) and digital ({", ".join(digital)}) parts; isolation is unverified.',
                missing=['Filtering between the analog and digital loads.']))
    if not findings:
        notes.append('No analog/digital shared supply nets observed.')
    return findings, notes


_SEED = [
    ('PWR-001', 'power', 'decoupling', _assess_power_decoupling,
     'Supply decoupling', 'Requires part datasheet capacitance and placement rules.'),
    ('PWR-002', 'power', 'regulation', _assess_regulator,
     'Regulator operating conditions', 'Requires input/output/load evidence and dropout data.'),
    ('PWR-003', 'power', 'distribution', _assess_supply_loading,
     'Supply loading', 'Requires a current budget against the supply limit.'),
    ('PWR-004', 'power', 'protection', _assess_supply_protection,
     'Supply protection', 'Requires exposure facts and protection ratings.'),
    ('IF-001', 'interfaces', 'i2c', _assess_i2c_pullups,
     'I2C pull-ups', 'Requires observed pull-up or a documented internal one.'),
    ('IF-002', 'interfaces', 'i2c', _assess_i2c_timing,
     'I2C timing', 'Requires rise-time calculation from speed and capacitance.'),
    ('IF-003', 'interfaces', 'uart', _assess_uart_levels,
     'UART level compatibility', 'Requires both sides IO voltages.'),
    ('IF-004', 'interfaces', 'usb', _assess_usb,
     'USB data integrity', 'Requires impedance, termination and layout evidence.'),
    ('IF-005', 'interfaces', 'spi', _assess_spi,
     'SPI topology', 'Requires clock rate and trace lengths.'),
    ('AN-001', 'analog', 'reference', _assess_adc_reference,
     'ADC reference', 'Requires reference source and accuracy needs.'),
    ('AN-002', 'analog', 'clock', _assess_crystal_load,
     'Crystal loading', 'Requires load-capacitor values or an internal oscillator.'),
    ('CLK-001', 'clock_reset', 'reset', _assess_reset,
     'Reset pull', 'Requires an observed pull or documented internal one.'),
    ('CLK-002', 'clock_reset', 'boot', _assess_boot_strapping,
     'Boot strapping', 'Requires the strapping table and intended mode.'),
    ('PRT-001', 'protection', 'esd', _assess_esd_exposed,
     'ESD on exposed nets', 'Requires exposure facts and protection level.'),
    ('PRT-002', 'protection', 'floating', _assess_floating_input,
     'Floating pins', 'Requires intended no-connect confirmation.'),
    ('THM-001', 'thermal', 'dissipation', _assess_thermal,
     'Component heating', 'Requires power, duty and thermal resistance.'),
    ('RF-001', 'rf', 'antenna', _assess_antenna,
     'Antenna and RF feed', 'Requires antenna and matching documentation.'),
    ('SI-001', 'signal_integrity', 'high_speed', _assess_high_speed,
     'High-speed routing', 'Requires stackup, lengths and rate.'),
    ('MFG-001', 'identity', 'part_match', _assess_part_identity,
     'Part identity', 'Requires an exact MPN and matched document.'),
    ('X-001', 'cross_domain', 'switching_analog', _assess_switching_near_analog,
     'Switching near analog', 'Requires placement and return-path layout.'),
    ('X-002', 'cross_domain', 'supply_sharing', _assess_shared_supply,
     'Shared analog/digital supply', 'Requires filtering evidence.'),
]

for _mode_id, _category, _subcategory, _assess, _title, _verify in _SEED:
    register({'id': _mode_id, 'category': _category, 'subcategory': _subcategory,
              'title': _title, 'verify': _verify, 'assess': _assess,
              'methods': ['trace_connections', 'read_datasheet', 'calculate_limits', 'inspect_layout']})
del _mode_id, _category, _subcategory, _assess, _title, _verify


def discover(packet, ledger, *, origin='scope', limit=MAX_CLAIMS):
    """Evaluate every mode against observed facts; record hypotheses in order."""
    from .ledger import add_claim, record_event
    facts = design_facts(packet)
    summary = {'modes': len(_MODES), 'claims': [], 'triggered': [], 'excluded': [], 'notes': {},
               'truncated': 0, 'dropped': []}
    for mode in sorted(_MODES, key=lambda m: m['id']):
        findings, notes = mode['assess'](facts)
        if notes:
            summary['notes'][mode['id']] = notes
        if not findings:
            summary['excluded'].append(mode['id'])
            continue
        summary['triggered'].append(mode['id'])
        for finding in findings:
            if len(summary['claims']) >= limit:
                summary['truncated'] += 1
                summary['dropped'].append(f"[{mode['id']}] {finding['basis']}"[:200])
                continue
            scope = finding['scope']
            evidence = [f"C:{r}" for r in scope['components']] + [f"N:{n}" for n in scope['nets']]
            statement = f"[{mode['id']}] {mode['title']}: {finding['basis']}"
            claim_id = add_claim(ledger, origin=origin, statement=statement, scope=scope,
                                 evidence_ids=evidence, open_questions=finding['missing'],
                                 assumptions=[f"Standing: {finding['standing']}.",
                                              f"Verification needs: {mode['verify']}"] + finding['assumptions'])
            summary['claims'].append(claim_id)
    detail = (f"{len(summary['claims'])} hypotheses from {len(summary['triggered'])} of "
              f"{summary['modes']} modes; {len(summary['excluded'])} modes excluded with reasons.")
    if summary['truncated']:
        noun = 'hypothesis' if summary['truncated'] == 1 else 'hypotheses'
        detail += f" {summary['truncated']} {noun} exceeded the recording budget and stay unrecorded."
        ledger['limitations'].append('Atlas discovery hit its recording budget; rerun with a higher limit.')
    record_event(ledger, 'atlas_assessed', detail, actor=origin,
                 data={'triggered': summary['triggered'], 'excluded': summary['excluded'],
                       'notes': summary['notes'], 'truncated': summary['truncated'],
                       'dropped': summary['dropped']})
    return summary

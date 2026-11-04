"""Design-driven discovery: hypotheses from connectivity structure and findings.

Complements the taxonomy atlas with what the actual schematic shows: open
nets, high-fanout nets, unpaired signals, local check findings promoted to
tracked hypotheses, and the connected-subsystem partition. Reads packet
facts only, never other streams' claims, so streams stay independent until
their hypotheses merge in the ledger.
"""
import re

from .facts import is_ground_net, is_supply_net

VERSION = 1
MAX_CLAIMS = 40
HUB_PINS = 6

_CHECKS = [
    ('DSG-001', 'structure', 'Lonely net', 'Requires the intended partners or an intentional single point.'),
    ('DSG-002', 'structure', 'High-fanout net', 'Requires loading and integrity review of the shared net.'),
    ('DSG-003', 'structure', 'Unpaired signal', 'Requires the mate connection or an intentional single-ended use.'),
    ('DSG-004', 'findings', 'Local finding', 'Requires investigation of the deterministic check result.'),
]
_VERIFY = {cid: verify for cid, _, _, verify in _CHECKS}


def facts(packet):
    """Observed connectivity, pins and review groups. Unknowns stay unknown."""
    components, nets = {}, {}
    for entry in packet.get('components', []):
        ref = entry.get('reference', '')
        pins = [{'number': str(p.get('number', '')), 'name': str(p.get('name', '')),
                 'net': str(p.get('net', '')),
                 'no_connect': bool(p.get('no_connect'))} for p in entry.get('pins', [])]
        components[ref] = {'dnp': bool(entry.get('dnp')), 'pins': pins}
        for pin in pins:
            if pin['net']:
                nets.setdefault(pin['net'], []).append({'ref': ref, **pin})
    return {'components': components, 'nets': nets, 'groups': packet.get('review_groups', [])}


def _finding(check, scope, standing, basis, missing=(), assumptions=()):
    return {'check': check, 'scope': {'components': sorted(scope.get('components', ())),
                                      'nets': sorted(scope.get('nets', ())), 'pins': sorted(scope.get('pins', ()))},
            'standing': standing, 'basis': basis, 'missing': list(missing), 'assumptions': list(assumptions)}


def lonely_nets(data):
    """Nets with a single member pin are open or incompletely exported."""
    findings, notes = [], []
    for net, members in sorted(data['nets'].items()):
        if len(members) != 1:
            continue
        member = members[0]
        label = f"{member['ref']}.{member['number']}"
        if member['no_connect'] or data['components'][member['ref']]['dnp']:
            notes.append(f'{net}: single pin {label} is marked no-connect or DNP.')
        else:
            findings.append(_finding('DSG-001', {'components': [member['ref']], 'nets': [net], 'pins': [label]},
                'applicable', f'Net {net} has a single connected pin ({label}).',
                missing=['Whether the export missed partners or the net is intentionally single-point.']))
    if not findings and not notes:
        notes.append('No single-member nets observed.')
    return findings, notes


def hub_nets(data):
    """Nets joining many pins deserve loading and integrity review."""
    findings, notes = [], []
    for net, members in sorted(data['nets'].items()):
        refs = sorted({m['ref'] for m in members})
        if len(members) >= HUB_PINS:
            if is_supply_net(net) or is_ground_net(net):
                notes.append(f'{net}: shared rail across {len(refs)} parts; loading is a supply question.')
                continue
            findings.append(_finding('DSG-002', {'components': refs, 'nets': [net]}, 'uncertain',
                f'Net {net} joins {len(members)} pins across {len(refs)} parts.',
                missing=['Driver strength and loading.', 'Whether the shared net meets timing needs.']))
    if not findings and not notes:
        notes.append(f'No nets with {HUB_PINS} or more member pins observed.')
    return findings, notes


def _mates(name, *, plus_minus=True):
    """Expected partner names for differential and crystal signals.

    Trailing +/- pairing applies to net names only: a pin named VLED+
    on a supply net is a polarity label, not a differential pair.
    """
    upper = str(name or '').upper()
    match = re.fullmatch(r'(.{2,})[_\-]P', upper)
    if match:
        return [match[1] + '_N', match[1] + '-N']
    match = re.fullmatch(r'(.{2,})[_\-]N', upper)
    if match:
        return [match[1] + '_P', match[1] + '-P']
    if upper.endswith('DP') and len(upper) > 2:
        return [upper[:-2] + 'DM']
    if upper.endswith('DM') and len(upper) > 2:
        return [upper[:-2] + 'DP']
    if plus_minus and upper.endswith('+'):
        return [upper[:-1] + '-']
    if plus_minus and upper.endswith('-'):
        return [upper[:-1] + '+']
    return []


def unpaired_signals(data):
    """Differential pairs and crystals need both ends to function."""
    findings, notes = [], []
    pool = {}
    for net in data['nets']:
        pool.setdefault(net.upper(), (net, True))
    for comp in data['components'].values():
        for pin in comp['pins']:
            if pin['name']:
                pool.setdefault(pin['name'].upper(), (pin['name'], False))
    seen = set()
    for name in sorted(pool):
        original, from_net = pool[name]
        for mate in _mates(name, plus_minus=from_net):
            if mate in pool or (name, mate) in seen or (mate, name) in seen:
                continue
            seen.add((name, mate))
            findings.append(_finding('DSG-003', {'nets': [original] if original in data['nets'] else []},
                'uncertain', f'Signal {original} has no visible {mate} partner.',
                missing=['Whether the mate is unconnected, renamed, or intentionally unused.'],
                assumptions=['Separator-terminated P/N suffixes denote differential pairs.']))
    for ref, comp in sorted(data['components'].items()):
        if comp['dnp']:
            continue
        names = {p['name'].upper() for p in comp['pins']}
        for first, second in (('XI', 'XO'), ('XC1', 'XC2'), ('XL1', 'XL2'), ('OSC_IN', 'OSC_OUT')):
            if first in names and second not in names:
                findings.append(_finding('DSG-003', {'components': [ref]}, 'applicable',
                    f'{ref} has {first} but no {second} pin; confirm the clock source is complete.',
                    missing=['Whether an internal oscillator or external clock replaces the crystal.']))
    if not findings:
        notes.append('No unpaired differential or crystal signals observed.')
    return findings, notes


def review_findings(data):
    """Promote deterministic fix/review findings into tracked hypotheses."""
    findings, notes = [], []
    for group in data['groups']:
        refs = [r for r in group.get('components', []) if isinstance(r, str)]
        if group.get('priority') in ('fix', 'review'):
            summary = str(group.get('summary', '') or group.get('title', ''))[:500]
            findings.append(_finding('DSG-004', {'components': refs}, 'applicable',
                f"Local {group.get('priority')} finding {group.get('id', '?')}: "
                f"{group.get('title', 'untitled')}. {summary}".strip(),
                missing=['Specialist investigation of the check result.'],
                assumptions=['Deterministic finding stands until contradicted by evidence.']))
        else:
            notes.append(f"{group.get('id', '?')}: priority {group.get('priority', '?')} stays informational.")
    if not findings and not notes:
        notes.append('No review groups observed.')
    return findings, notes


def subsystems(data):
    """Connected parts sharing nets; the partition feeds coverage, not claims."""
    parent = {}

    def find(ref):
        parent.setdefault(ref, ref)
        while parent[ref] != ref:
            parent[ref] = parent[parent[ref]]
            ref = parent[ref]
        return ref

    for members in data['nets'].values():
        refs = [m['ref'] for m in members if not data['components'][m['ref']]['dnp']]
        for ref in refs[1:]:
            parent[find(refs[0])] = find(ref)
    groups = {}
    for ref, comp in data['components'].items():
        if not comp['dnp']:
            groups.setdefault(find(ref), []).append(ref)
    return sorted(sorted(refs) for refs in groups.values())


_RUNNERS = {'DSG-001': lonely_nets, 'DSG-002': hub_nets,
              'DSG-003': unpaired_signals, 'DSG-004': review_findings}


def assess(check, data):
    """Run one structural check; Gate A reuses this to re-verify findings."""
    return _RUNNERS[check](data)


def discover(packet, ledger, *, origin='circuit', limit=MAX_CLAIMS):
    """Run structural checks against packet facts; record hypotheses in order."""
    from .ledger import add_claim, record_event
    data = facts(packet)
    summary = {'checks': len(_CHECKS), 'claims': [], 'triggered': [], 'excluded': [],
               'notes': {}, 'truncated': 0, 'dropped': [], 'subsystems': subsystems(data)}
    for check, _, title, _ in sorted(_CHECKS):
        findings, notes = assess(check, data)
        if notes:
            summary['notes'][check] = notes
        if not findings:
            summary['excluded'].append(check)
            continue
        summary['triggered'].append(check)
        for finding in findings:
            if len(summary['claims']) >= limit:
                summary['truncated'] += 1
                summary['dropped'].append(f"[{check}] {finding['basis']}"[:200])
                continue
            scope = finding['scope']
            evidence = [f"C:{r}" for r in scope['components']] + [f"N:{n}" for n in scope['nets']]
            claim_id = add_claim(ledger, origin=origin,
                                 statement=f"[{check}] {title}: {finding['basis']}", scope=scope,
                                 evidence_ids=evidence, open_questions=finding['missing'],
                                 assumptions=[f"Standing: {finding['standing']}.",
                                              f"Verification needs: {_VERIFY[check]}"] + finding['assumptions'])
            summary['claims'].append(claim_id)
    detail = (f"{len(summary['claims'])} hypotheses from {len(summary['triggered'])} of "
              f"{summary['checks']} checks; {len(summary['excluded'])} checks excluded with reasons; "
              f"{len(summary['subsystems'])} connected subsystems mapped.")
    if summary['truncated']:
        noun = 'hypothesis' if summary['truncated'] == 1 else 'hypotheses'
        detail += f" {summary['truncated']} {noun} exceeded the recording budget and stay unrecorded."
        ledger['limitations'].append('Design discovery hit its recording budget; rerun with a higher limit.')
    record_event(ledger, 'design_assessed', detail, actor=origin,
                 data={'triggered': summary['triggered'], 'excluded': summary['excluded'],
                       'notes': summary['notes'], 'truncated': summary['truncated'],
                       'dropped': summary['dropped'], 'subsystems': summary['subsystems']})
    return summary

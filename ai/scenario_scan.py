"""Scenario-driven discovery: startup, operation, misuse, faults, environment.

Walks foreseeable operating scenarios against observed design facts and
records what each scenario leaves unverified. Implements its own fact
reading rather than importing the other streams, so all three discovery
streams stay independent until their hypotheses merge in the ledger.
"""
from .facts import is_supply_net as _is_supply
from .facts import power_dissipators as _shared_dissipators

VERSION = 1
MAX_CLAIMS = 40

_CHECKS = [
    ('SCN-001', 'startup', 'Startup power', 'Requires sequencing and inrush evidence per supply domain.'),
    ('SCN-002', 'startup', 'Reset timing', 'Requires power-on reset duration against the slowest supply.'),
    ('SCN-003', 'operation', 'Sustained heating', 'Requires continuous-operation power and temperature evidence.'),
    ('SCN-004', 'misuse', 'Connector misuse', 'Requires tolerance to foreseeable shorts and misconnection.'),
    ('SCN-005', 'environment', 'Operating environment', 'Requires stated ambient and cooling conditions.'),
    ('SCN-006', 'assembly', 'DNP intent', 'Requires confirmation that DNP marks intent, not a missing part.'),
]
_VERIFY = {cid: verify for cid, _, _, verify in _CHECKS}
_TEMP_KEYS = ('temperature', 'temp_range', 'ambient_temp', 'operating_temp', 'max_ambient')


def facts(packet):
    """Observed parts, nets and stated operating context. Unknowns stay unknown."""
    components, nets = {}, {}
    for entry in packet.get('components', []):
        ref = entry.get('reference', '')
        pins = [{'number': str(p.get('number', '')), 'name': str(p.get('name', '')),
                 'net': str(p.get('net', ''))} for p in entry.get('pins', [])]
        components[ref] = {'value': str(entry.get('value', '')), 'footprint': str(entry.get('footprint', '')),
                           'mpn': str(entry.get('mpn', '') or ''), 'dnp': bool(entry.get('dnp')), 'pins': pins}
        for pin in pins:
            if pin['net']:
                nets.setdefault(pin['net'], []).append(ref)
    context = packet.get('design_context', {})
    operating = context.get('operating_context', {})
    return {'components': components, 'nets': nets,
            'operating': operating if isinstance(operating, dict) else {}}


def _dissipators(data):
    return _shared_dissipators(data['components'])


def _finding(check, scope, standing, basis, missing=(), assumptions=()):
    return {'check': check, 'scope': {'components': sorted(scope.get('components', ())),
                                      'nets': sorted(scope.get('nets', ())), 'pins': sorted(scope.get('pins', ()))},
            'standing': standing, 'basis': basis, 'missing': list(missing), 'assumptions': list(assumptions)}


def startup_power(data):
    """Every supply domain needs sequencing and inrush evidence at power-on."""
    findings, notes = [], []
    domains = {}
    for net, refs in data['nets'].items():
        if not _is_supply(net):
            continue
        loads = sorted({r for r in refs if r[:1] in 'UQYM' and not data['components'][r]['dnp']})
        if loads:
            domains[net] = loads
    if len(domains) >= 2:
        nets = sorted(domains)
        findings.append(_finding('SCN-001', {'nets': nets}, 'uncertain',
            f"Power-on sequencing across {', '.join(nets)} is unverified.",
            missing=['Supply ramp order and timing.', 'Inrush current per domain.']))
    elif len(domains) == 1:
        net, loads = next(iter(domains.items()))
        if len(loads) >= 2:
            findings.append(_finding('SCN-001', {'components': loads, 'nets': [net]}, 'uncertain',
                f'Supply net {net} starts {len(loads)} parts at once; inrush is unverified.',
                missing=['Combined inrush current and the supply limit.']))
        else:
            notes.append(f'Single supply net {net} with one load; no sequencing question observed.')
    else:
        notes.append('No supply domains with fitted loads observed.')
    return findings, notes


def reset_timing(data):
    """Reset must stay asserted until every supply is stable."""
    findings, notes = [], []
    for ref, comp in sorted(data['components'].items()):
        if comp['dnp']:
            continue
        pins = [p for p in comp['pins']
                if p['name'].upper() in ('RESET', 'RST', 'NRST', 'NRESET') and p['net']]
        if pins:
            findings.append(_finding('SCN-002', {'components': [ref], 'nets': sorted({p['net'] for p in pins}),
                                                'pins': [f"{ref}.{p['number']}" for p in pins]}, 'uncertain',
                f"{ref} reset release timing against supply ramp is unverified.",
                missing=['Reset hold time and the slowest supply ramp.']))
    if not findings:
        notes.append('No reset nets observed.')
    return findings, notes


def sustained_heating(data):
    """Dissipating parts must survive continuous operation, not just power-on."""
    findings, notes = [], []
    for ref in _dissipators(data):
        findings.append(_finding('SCN-003', {'components': [ref]}, 'uncertain',
            f'{ref} continuous-operation temperature rise is unverified.',
            missing=['Sustained power dissipation and duty cycle.', 'Junction-to-ambient thermal path.']))
    if not findings:
        notes.append('No power-dissipating parts identified by value, footprint or MPN.')
    return findings, notes


def connector_misuse(data):
    """Exposed pins face shorts, misplugs and handling faults."""
    findings, notes = [], []
    for ref, comp in sorted(data['components'].items()):
        if ref[:1] not in 'JP' or comp['dnp']:
            continue
        nets = sorted({p['net'] for p in comp['pins'] if p['net']})
        if nets:
            findings.append(_finding('SCN-004', {'components': [ref], 'nets': nets}, 'uncertain',
                f'{ref} exposed nets ({", ".join(nets)}) face foreseeable short and misplug faults.',
                missing=['Which faults the circuit must survive.', 'Protection or tolerance evidence.']))
    if not findings:
        notes.append('No fitted connectors with connected nets observed.')
    return findings, notes


def environment(data):
    """Heating parts need a stated ambient; unstated means unknown."""
    findings, notes = [], []
    hot = _dissipators(data)
    stated = {k: data['operating'].get(k) for k in _TEMP_KEYS if data['operating'].get(k) not in (None, '')}
    if not hot:
        notes.append('No power-dissipating parts need ambient review.')
    elif stated:
        key, value = min(stated.items())
        notes.append(f'Operating {key} stated: {str(value)[:120]}.')
    else:
        findings.append(_finding('SCN-005', {'components': hot}, 'uncertain',
            f"{', '.join(hot)} dissipate power but no operating temperature is stated.",
            missing=['What ambient temperature range must this board tolerate?']))
    return findings, notes


def dnp_intent(data):
    """A DNP part on live nets may be intent or a missing assembly step."""
    findings, notes = [], []
    for ref, comp in sorted(data['components'].items()):
        if not comp['dnp']:
            continue
        live = sorted({p['net'] for p in comp['pins'] if p['net']
                       and any(r != ref and not data['components'].get(r, {}).get('dnp')
                               for r in data['nets'].get(p['net'], []))})
        if live:
            findings.append(_finding('SCN-006', {'components': [ref], 'nets': live}, 'uncertain',
                f'DNP part {ref} sits on live nets ({", ".join(live)}); confirm the omission is intentional.',
                missing=['Whether the DNP marking reflects a tested assembly variant.']))
    if not findings:
        notes.append('No DNP parts on nets shared with fitted parts observed.')
    return findings, notes


_RUNNERS = {'SCN-001': startup_power, 'SCN-002': reset_timing, 'SCN-003': sustained_heating,
            'SCN-004': connector_misuse, 'SCN-005': environment, 'SCN-006': dnp_intent}


def assess(check, data):
    """Run one scenario check; reinvestigation reuses this against fresh facts."""
    return _RUNNERS[check](data)


def discover(packet, ledger, *, origin='scenario', limit=MAX_CLAIMS):
    """Run scenario checks against packet facts; record hypotheses in order."""
    from .ledger import add_claim, record_event
    data = facts(packet)
    summary = {'checks': len(_CHECKS), 'claims': [], 'triggered': [], 'excluded': [],
               'notes': {}, 'truncated': 0, 'dropped': []}
    runners = {'SCN-001': startup_power, 'SCN-002': reset_timing, 'SCN-003': sustained_heating,
               'SCN-004': connector_misuse, 'SCN-005': environment, 'SCN-006': dnp_intent}
    for check, _, title, _ in sorted(_CHECKS):
        findings, notes = runners[check](data)
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
              f"{summary['checks']} scenario checks; {len(summary['excluded'])} checks excluded with reasons.")
    if summary['truncated']:
        noun = 'hypothesis' if summary['truncated'] == 1 else 'hypotheses'
        detail += f" {summary['truncated']} {noun} exceeded the recording budget and stay unrecorded."
        ledger['limitations'].append('Scenario discovery hit its recording budget; rerun with a higher limit.')
    record_event(ledger, 'scenario_assessed', detail, actor=origin,
                 data={'triggered': summary['triggered'], 'excluded': summary['excluded'],
                       'notes': summary['notes'], 'truncated': summary['truncated'],
                       'dropped': summary['dropped']})
    return summary

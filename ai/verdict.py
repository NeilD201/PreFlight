"""Gate A finding verification: deterministic verdicts for ledger claims.

Re-runs the originating detector against current packet facts and confirms
that every scoped object and evidence link resolves. Observation claims
advance to supported or contradicted; judgment-needing claims stay
unresolved with their open questions; claims about absent objects are not
applicable with a recorded justification. Refuses stale revisions outright.
Gate B fix verification and model judgment arrive in a later increment.
"""
import re
from functools import partial

VERSION = 1

# Modes whose trigger is a deterministic observation that re-checking settles.
OBSERVATION_MODES = frozenset({'DSG-001', 'DSG-002', 'DSG-003', 'PWR-001', 'PWR-003',
                               'PWR-004', 'IF-001', 'MFG-001', 'PRT-002', 'AN-002',
                               'CLK-001', 'PRT-001'})


def gate_facts(packet):
    """Current components, nets and excerpt IDs. Unknowns stay unknown."""
    components, nets = {}, {}
    for entry in packet.get('components', []):
        ref = entry.get('reference', '')
        pins = [{'number': str(p.get('number', '')), 'name': str(p.get('name', '')),
                 'net': str(p.get('net', ''))} for p in entry.get('pins', [])]
        components[ref] = {'dnp': bool(entry.get('dnp')), 'pins': pins,
                           'mpn': str(entry.get('mpn', '') or '')}
        for pin in pins:
            if pin['net']:
                nets.setdefault(pin['net'], []).append(ref)
    excerpts = {d.get('evidence_id', '') for d in packet.get('datasheet_excerpts', [])}
    return {'components': components, 'nets': nets, 'excerpts': excerpts}


def excerpt_texts(packet):
    """Map excerpt IDs to their retrieved text, MPN and matched reference."""
    texts = {}
    for entry in packet.get('datasheet_excerpts', []):
        eid = entry.get('evidence_id', '')
        if eid:
            texts[eid] = {'text': str(entry.get('text', '') or ''),
                          'mpn': str(entry.get('mpn', '') or ''),
                          'references': list(entry.get('references', []) or []),
                          'part_match': str(entry.get('part_match', '') or '')}
    return texts


def _runners():
    from . import atlas, design_scan
    table = {}
    for mode in atlas.modes():
        table[mode['id']] = (atlas.design_facts, mode['assess'])
    for check in ('DSG-001', 'DSG-002', 'DSG-003', 'DSG-004'):
        table[check] = (design_scan.facts, partial(design_scan.assess, check))
    return table


def _signature(scope):
    parts = [tuple(sorted(scope.get(key, ()))) for key in ('components', 'nets', 'pins')]
    return (parts[0], parts[1], parts[2])


def check_scope(data, claim):
    """Every scoped object must exist in this revision."""
    scope = claim.get('scope', {})
    for ref in scope.get('components', []):
        comp = data['components'].get(ref)
        if comp is None:
            return False, f'{ref} does not exist in this revision.'
        if comp['dnp']:
            return False, f'{ref} is DNP in this revision.'
    for label in scope.get('pins', []):
        ref, dot, number = label.partition('.')
        numbers = {p['number'] for p in data['components'].get(ref, {}).get('pins', [])}
        if not dot or number not in numbers:
            return False, f'Pin {label} does not exist in this revision.'
    for net in scope.get('nets', []):
        if net not in data['nets']:
            return False, f'Net {net} does not exist in this revision.'
    return True, ''


def check_links(data, claim):
    """Every evidence link must resolve to a recorded object."""
    bad = []
    for link in claim.get('evidence', []):
        target = link.get('id', '')
        if target.startswith('C:'):
            ok = target[2:] in data['components']
        elif target.startswith('N:'):
            ok = target[2:] in data['nets']
        elif target.startswith('D:'):
            ok = target in data['excerpts']
        else:
            ok = False
        if not ok:
            bad.append(target)
    if bad:
        return False, f"Evidence {', '.join(sorted(set(bad)))} does not resolve in this revision."
    return True, ''


def check_pin_net(data, claim):
    """Scoped pins must sit on the scoped nets when both are named.

    A claim about pin U1.3 on net VCC is contradicted when U1.3 is actually
    on GND. Claims scoping only pins or only nets have nothing to check.
    """
    scope = claim.get('scope', {})
    pins, nets = scope.get('pins', []), set(scope.get('nets', []))
    if not pins or not nets:
        return 'unverifiable', 'Pin-to-net consistency needs both pins and nets in scope.'
    problems = []
    for label in pins:
        ref, dot, number = label.partition('.')
        found = [p['net'] for p in data['components'].get(ref, {}).get('pins', [])
                 if dot and p['number'] == number]
        if not found:
            problems.append(f'Pin {label} has no recorded net.')
        elif found[0] not in nets:
            problems.append(f'Pin {label} is on {found[0] or "no net"}, not on {sorted(nets)[0]}.')
    if problems:
        return 'contradiction', ' '.join(problems)
    return 'verified', f'{len(pins)} scoped pins sit on the scoped nets.'


_STOPWORDS = frozenset('''about after also because been before between both claim
could does during either else from have here into more most only other should
than that this using what when where which while with within would'''.split())


def _terms(text):
    return set(re.findall(r'[a-z0-9]{4,}', str(text or '').casefold())) - _STOPWORDS


def check_passages(packet, claim):
    """A citation ID is not evidence: the passage must match the variant and
    actually discuss the claim. Returns per-excerpt outcomes."""
    texts = excerpt_texts(packet)
    data = gate_facts(packet)
    scope_refs = set(claim.get('scope', {}).get('components', []))
    terms = _terms(claim.get('statement', ''))
    outcomes = []
    for link in claim.get('evidence', []):
        target = link.get('id', '')
        if not target.startswith('D:'):
            continue
        excerpt = texts.get(target)
        if excerpt is None or not excerpt['text'].strip():
            outcomes.append({'id': target, 'result': 'missing',
                             'reason': 'No passage text was retrieved for this citation.'})
            continue
        # Variant check: the excerpt MPN must match a scoped component's MPN
        # when both are known; a mismatch disproves applicability.
        variant = 'unverifiable'
        for ref in scope_refs & set(excerpt['references']):
            comp_mpn = (data['components'].get(ref, {}).get('mpn') or '').strip().casefold()
            doc_mpn = excerpt['mpn'].strip().casefold()
            if comp_mpn and doc_mpn:
                variant = 'verified' if comp_mpn == doc_mpn else 'contradiction'
                break
        if variant == 'contradiction':
            outcomes.append({'id': target, 'result': 'contradiction',
                             'reason': f"Passage MPN {excerpt['mpn']} does not match the fitted part."})
            continue
        overlap = terms & _terms(excerpt['text'])
        if len(overlap) >= 2:
            outcomes.append({'id': target, 'result': 'verified',
                             'reason': f'Passage discusses the claim ({len(overlap)} shared terms).'})
        elif overlap:
            outcomes.append({'id': target, 'result': 'unverifiable',
                             'reason': 'Passage shares one term; relevance needs specialist review.'})
        else:
            outcomes.append({'id': target, 'result': 'unverifiable',
                             'reason': 'Passage text does not discuss the claim; citation is irrelevant.'})
    return outcomes


def check_calc_rows(calculations):
    """Ledger-level calculation sanity: checked rows need finite inputs with
    units and a result; stale rows are missing evidence, not proof."""
    import math
    outcomes = []
    for row in calculations or []:
        label = row.get('label', row.get('key', '?'))
        if row.get('stale'):
            outcomes.append({'id': str(row.get('key', label)), 'result': 'missing',
                             'reason': f'{label}: inputs changed since this result; re-run the calculation.'})
            continue
        if row.get('status') not in ('checked', 'attention'):
            outcomes.append({'id': str(row.get('key', label)), 'result': 'missing',
                             'reason': f'{label}: calculation is {row.get("status", "incomplete")}.'})
            continue
        inputs = row.get('inputs')
        if inputs is None:
            fields, values = row.get('fields', []), row.get('values', {})
            inputs = [{'label': f.get('label', ''), 'value': values.get(f.get('key')),
                       'unit': f.get('unit', '')} for f in fields]
        bad = [i for i in inputs if not isinstance(i.get('value'), (int, float))
               or not math.isfinite(i['value']) or not str(i.get('unit', '')).strip()]
        if bad:
            outcomes.append({'id': str(row.get('key', label)), 'result': 'contradiction',
                             'reason': f'{label}: {len(bad)} inputs are non-numeric or unitless.'})
        elif not str(row.get('detail', '')).strip() and not row.get('metrics'):
            outcomes.append({'id': str(row.get('key', label)), 'result': 'unverifiable',
                             'reason': f'{label}: result has no recorded detail or metrics.'})
        else:
            outcomes.append({'id': str(row.get('key', label)), 'result': 'verified',
                             'reason': f'{label}: inputs finite with units; result recorded.'})
    return outcomes


def recheck(packet, claim):
    """Re-run the originating detector; compare against the recorded scope."""
    match = re.match(r'\[([A-Z]+-\d+)\]', claim.get('statement', ''))
    mode = match[1] if match else ''
    if mode not in OBSERVATION_MODES:
        return 'unresolved', 'No deterministic validator; needs specialist review.'
    try:
        facts_fn, assess = _runners()[mode]
        findings, _ = assess(facts_fn(packet))
    except (ValueError, TypeError, KeyError, AttributeError):
        return 'unresolved', 'Deterministic validator could not complete; needs specialist review.'
    wanted = _signature(claim.get('scope', {}))
    for finding in findings:
        if _signature(finding['scope']) == wanted:
            return 'supported', f'{mode} condition confirmed: {finding["basis"]}'
    return 'contradicted', f'{mode} condition no longer observed in current facts.'


def judge(packet, data, claim):
    """Scope, then links, then the originating detector."""
    ok, reason = check_scope(data, claim)
    if not ok:
        return 'not_applicable', reason
    ok, reason = check_links(data, claim)
    if not ok:
        return 'unresolved', reason
    return recheck(packet, claim)


def verify_fix(packet, ledger, claim_id):
    """Gate B: structurally verify a proposed fix without endorsing values.

    Outcomes: rejected (names absent objects), needs_testing (gaps, overlaps
    or unconfirmed values), structure_ok (named objects check out). Values
    are never approved here; hypothetical re-checks stay explicitly infeasible
    until design-mutation machinery exists.
    """
    from .ledger import record_event
    claim = next((c for c in ledger['claims'] if c['id'] == claim_id), None)
    fix = (claim or {}).get('proposed_fix')
    if claim is None or not fix:
        return None
    data = gate_facts(packet)
    reasons, retest = [], list(fix.get('verify_steps', []))
    ref = str(fix.get('reference', '')).strip()
    comp = data['components'].get(ref)
    if not str(fix.get('action', '')).strip():
        reasons.append('rejected: no action specified.')
    if comp is None:
        reasons.append(f'rejected: {ref or "no reference"} does not exist in this revision.')
    elif comp['dnp']:
        reasons.append(f'rejected: {ref} is DNP in this revision.')
    else:
        pin = str(fix.get('pin', '')).strip()
        if pin:
            if pin not in {p['number'] for p in comp['pins']}:
                reasons.append(f'rejected: pin {pin} is not on {ref}.')
        else:
            reasons.append('needs_testing: pin unspecified.')
        net = str(fix.get('net', '')).strip()
        if net:
            if net not in data['nets']:
                reasons.append(f'rejected: net {net} does not exist in this revision.')
        else:
            reasons.append('needs_testing: net unspecified.')
    value = str(fix.get('value', '')).strip()
    if value:
        retest.append(f'Confirm value {value} against the datasheet or measurement; it is not approved here.')
    else:
        reasons.append('needs_testing: value unspecified.')
    if not any(r.startswith('rejected') for r in reasons):
        touched = {ref, str(fix.get('net', '')).strip()} - {''}
        overlapping = sorted(other['id'] for other in ledger['claims']
                             if other['id'] != claim_id and other.get('status') in ('supported', 'unresolved')
                             and (touched & set(other.get('scope', {}).get('components', ()))
                                  or touched & set(other.get('scope', {}).get('nets', ()))))
        if overlapping:
            reasons.append(f"needs_testing: overlaps {', '.join(overlapping)}; assess joint consequences.")
    reasons.append('Hypothetical re-check against a modified design is not yet implemented.')
    if any(r.startswith('rejected') for r in reasons):
        outcome = 'rejected'
    elif any(r.startswith('needs_testing') for r in reasons):
        outcome = 'needs_testing'
    else:
        outcome = 'structure_ok'
    claim['fix_gate'] = {'status': outcome, 'reasons': reasons, 'retest': retest}
    claim.setdefault('open_questions', []).extend(q for q in retest if q not in claim['open_questions'])
    headline = reasons[0] if outcome != 'structure_ok' else 'named objects resolve; values unapproved.'
    record_event(ledger, 'fix_verified', f'{claim_id} fix {outcome}: {headline}',
                 actor='verdict', claim_id=claim_id)
    return outcome


def apply_challenge_verdict(packet, ledger, claim_id, critique):
    """Constrained model-backed rejection: a refuted-with-evidence critique can
    close an unresolved claim as contradicted. Deterministic verdicts
    (supported, contradicted, not_applicable) never move on model opinion, and
    no model input can mark a claim supported. Returns the new status or None.
    """
    from .ledger import set_status
    claim = next((c for c in ledger['claims'] if c['id'] == claim_id), None)
    if claim is None or claim.get('status') != 'unresolved':
        return None
    if critique.get('verdict') != 'refuted' or not critique.get('counterevidence', '').strip():
        return None
    ok, _ = check_links(gate_facts(packet),
                        {'evidence': [{'id': e} for e in critique.get('cited_ids', [])]})
    if not ok:
        return None
    set_status(ledger, claim_id, 'under_investigation',
               reason='Reopened by a substantiated challenge.', actor='verdict')
    set_status(ledger, claim_id, 'contradicted',
               reason=f"Challenge refuted with cited evidence: {critique['counterevidence'].strip()[:500]}",
               actor='verdict')
    return 'contradicted'


def verify(packet, ledger, source_hashes):
    """Advance every hypothesized claim; refuse stale revisions outright."""
    from .ledger import UNDER_INVESTIGATION, check_revision, record_event, set_status
    results = {'verified': 0, 'supported': 0, 'contradicted': 0, 'unresolved': 0,
               'not_applicable': 0, 'skipped': 0}
    if not check_revision(ledger, source_hashes):
        record_event(ledger, 'gate_a_refused',
                     'Ledger revision does not match; verification refused.', actor='verdict')
        return results
    data = gate_facts(packet)
    for claim in ledger['claims']:
        if claim['status'] != 'hypothesized':
            results['skipped'] += 1
            continue
        set_status(ledger, claim['id'], UNDER_INVESTIGATION,
                   reason='Gate A finding verification.', actor='verdict')
        verdict, reason = judge(packet, data, claim)
        set_status(ledger, claim['id'], verdict, reason=reason, actor='verdict')
        claim['verification'] = 'deterministic'
        results['verified'] += 1
        results[verdict] += 1
    record_event(ledger, 'gate_a_completed',
                 f"Gate A verified {results['verified']} claims: {results['supported']} supported, "
                 f"{results['contradicted']} contradicted, {results['unresolved']} unresolved, "
                 f"{results['not_applicable']} not applicable.",
                 actor='verdict', data=results)
    return results

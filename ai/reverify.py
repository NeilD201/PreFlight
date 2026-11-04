"""Per-finding verification: deterministic Gate 1, independent Meta Gate 2,
bounded reinvestigation, and final cross-finding consistency.

Every material ledger claim earns its own verification record instead of
sharing one batch verdict. Gate 1 re-checks scope, evidence links, pin-net
membership, datasheet passage relevance and the originating detector against
current packet facts. Gate 2 puts every finding past the existing FAULT
adversarial judge in bounded batches. Failures return to the originating
detector for targeted correction (never a whole-pipeline restart), then both
gates run again. A final consistency pass compares accepted findings against
each other before each finding settles as verified, unresolved or rejected.
"""
import re

VERSION = 1
MAX_REINVESTIGATIONS = 2
GATE2_BATCH = 4
MAX_CONSISTENCY_PASSES = 2


def _mode_of(statement):
    match = re.match(r'\[([A-Z]+-\d+)\]', str(statement or ''))
    return match[1] if match else ''


def _runners():
    """Originating detectors importable for targeted reinvestigation."""
    from . import atlas, design_scan, scenario_scan
    from functools import partial
    table = {}
    for mode in atlas.modes():
        table[mode['id']] = (atlas.design_facts, mode['assess'])
    for check in ('DSG-001', 'DSG-002', 'DSG-003', 'DSG-004'):
        table[check] = (design_scan.facts, partial(design_scan.assess, check))
    for check in ('SCN-001', 'SCN-002', 'SCN-003', 'SCN-004', 'SCN-005', 'SCN-006'):
        table[check] = (scenario_scan.facts, partial(scenario_scan.assess, check))
    return table


def gate1(packet, ledger, claim_id, *, calculations=None):
    """Deterministic per-finding checks. Records claim['gate1'] and returns it.

    Outcome is 'pass' (every applicable check verified), 'incomplete'
    (missing or unverifiable evidence, no contradiction) or 'fail' (a proven
    contradiction). Never moves the claim status; the caller routes failures
    to reinvestigation and finalizes verdicts afterwards.
    """
    from .ledger import record_event
    from .verdict import (check_calc_rows, check_links, check_passages,
                          check_pin_net, check_scope, gate_facts, recheck)
    claim = next((c for c in ledger['claims'] if c['id'] == claim_id), None)
    if claim is None:
        raise KeyError(f'Unknown ledger claim: {claim_id}')
    data = gate_facts(packet)
    checks = []
    ok, reason = check_scope(data, claim)
    checks.append({'name': 'scope', 'result': 'verified' if ok else 'contradiction',
                   'reason': reason or 'Every scoped object exists in this revision.'})
    ok, reason = check_links(data, claim)
    checks.append({'name': 'links', 'result': 'verified' if ok else 'missing',
                   'reason': reason or 'Every evidence link resolves in this revision.'})
    scope = claim.get('scope', {})
    if scope.get('pins') and scope.get('nets'):
        result, reason = check_pin_net(data, claim)
        checks.append({'name': 'pin_net', 'result': result, 'reason': reason})
    for outcome in check_passages(packet, claim):
        checks.append({'name': f"passage:{outcome['id']}", 'result': outcome['result'],
                       'reason': outcome['reason']})
    verdict, reason = recheck(packet, claim)
    checks.append({'name': 'detector', 'result': {'supported': 'verified', 'contradicted': 'contradiction'}.get(
        verdict, 'unverifiable'), 'reason': reason})
    if calculations is not None:
        # Only rows touching this finding count; unrelated calculations are
        # ledger-level context, not per-finding evidence.
        refs = set(claim.get('scope', {}).get('components', ()))
        labels = set(claim.get('scope', {}).get('nets', ()))
        relevant = [r for r in calculations
                    if r.get('label') in labels
                    or refs.intersection(r.get('loads', {}) or {})
                    or any(ref in str(r.get('key', '')).split(':') for ref in refs)]
        for outcome in check_calc_rows(relevant):
            checks.append({'name': f"calc:{outcome['id']}", 'result': outcome['result'],
                           'reason': outcome['reason']})
    results = {c['result'] for c in checks}
    if 'contradiction' in results:
        outcome = 'fail'
    elif results <= {'verified'}:
        outcome = 'pass'
    else:
        outcome = 'incomplete'
    record = {'outcome': outcome, 'checks': checks}
    claim['gate1'] = record
    record_event(ledger, 'gate1_recorded',
                 f'{claim_id} Gate 1 {outcome}: ' +
                 ', '.join(f"{c['name']}={c['result']}" for c in checks)[:1500],
                 actor='verdict', claim_id=claim_id, data={'outcome': outcome})
    return record


def _challenged(ledger, claim_id):
    """Verdict word from an earlier FAULT challenge event, if any."""
    for event in ledger['events']:
        if event.get('kind') == 'challenge' and event.get('claim_id') == claim_id:
            word = str(event.get('detail', '')).split(':', 1)[0].strip()
            if word in ('stands', 'weakened', 'refuted'):
                return word
    return None


def gate2(packet, ledger, claim_ids, *, key, model, provider, progress=None):
    """Independent Meta judge verdict for every listed finding, in batches.

    Findings challenged by the earlier FAULT pass reuse that recorded
    verdict instead of spending another request. Returns merged usage.
    """
    from .fault import challenge
    from .ledger import record_event
    from .usage import current_budget
    usage = {}
    pending = []
    prior_failures = [e for e in ledger['events'] if e.get('kind') == 'fault_unavailable']
    # A validation failure or exhausted timeout must not trigger an immediate
    # identical retry; only a transient connection-class failure may re-attempt.
    judge_healthy = not prior_failures or bool(
        (prior_failures[-1].get('data') or {}).get('diagnostics', {}).get('retryable'))
    for cid in claim_ids:
        claim = next((c for c in ledger['claims'] if c['id'] == cid), None)
        if claim is None or claim.get('status') not in ('supported', 'unresolved'):
            continue
        prior = _challenged(ledger, cid)
        if prior is not None:
            claim['judge'] = {'verdict': prior, 'reason': 'Recorded by the earlier challenge pass.'}
            record_event(ledger, 'judge_reused', f'{cid} judge {prior} (earlier pass).',
                         actor='fault', claim_id=cid, data={'verdict': prior})
        else:
            pending.append(cid)
    if pending and not judge_healthy:
        # The earlier challenge pass already failed this run; retrying the
        # same judge immediately would burn requests on the same failure.
        for cid in pending:
            claim = next(c for c in ledger['claims'] if c['id'] == cid)
            claim['judge'] = {'verdict': 'unavailable',
                              'reason': 'Earlier challenge pass failed; not retried.'}
            record_event(ledger, 'judge_skipped', f'{cid}: judge unhealthy, not retried.',
                         actor='fault', claim_id=cid)
        return usage
    for start in range(0, len(pending), GATE2_BATCH):
        batch = pending[start:start + GATE2_BATCH]
        budget = current_budget()
        if budget is not None and budget.limit - budget.requests < 1:
            for cid in pending[start:]:
                claim = next(c for c in ledger['claims'] if c['id'] == cid)
                claim['judge'] = {'verdict': 'skipped',
                                  'reason': 'Request budget exhausted; judge verdict unavailable.'}
                record_event(ledger, 'judge_skipped', f'{cid}: budget exhausted.', actor='fault',
                             claim_id=cid)
            break
        collected = []
        events_before = len(ledger['events'])
        try:
            sample = challenge(packet, ledger, key=key, model=model, provider=provider,
                               progress=progress, limit=len(batch), claim_ids=batch, collect=collected)
        except (ValueError, TypeError, KeyError, AttributeError):
            collected, sample = [], {}
        if not collected and any(e.get('kind') == 'fault_unavailable'
                                 for e in ledger['events'][events_before:]):
            # This batch just failed; further batches would fail the same way.
            for cid in pending[start:]:
                claim = next(c for c in ledger['claims'] if c['id'] == cid)
                if 'judge' not in claim:
                    claim['judge'] = {'verdict': 'unavailable',
                                      'reason': 'Judge batch failed; remaining batches not retried.'}
                    record_event(ledger, 'judge_skipped', f'{cid}: batch failed, stopping.',
                                 actor='fault', claim_id=cid)
            break
        for field, value in (sample or {}).items():
            if type(value) is int and value >= 0:
                prior = usage.get(field)
                usage[field] = (prior if type(prior) is int and prior >= 0 else 0) + value
        judged = {c['claim_id']: c for c in collected}
        for cid in batch:
            claim = next(c for c in ledger['claims'] if c['id'] == cid)
            critique = judged.get(cid)
            if critique is None:
                claim['judge'] = {'verdict': 'unavailable',
                                  'reason': 'Judge request failed or was skipped; see ledger events.'}
                record_event(ledger, 'judge_skipped', f'{cid}: judge verdict unavailable.',
                             actor='fault', claim_id=cid)
            else:
                claim['judge'] = {'verdict': critique['verdict'],
                                  'reason': (critique['counterevidence'].strip()
                                             or 'No error substantiated.')[:500]}
    return usage


def reinvestigate(packet, ledger, claim_id, failures):
    """Return one finding to its originating detector for targeted correction.

    Returns True when the claim materially changed (withdrawn or with new
    evidence attached); False when attempts are exhausted, no detector can
    re-run, or nothing new was learned. The finding ID and history persist.
    """
    from .ledger import record_event, set_status
    from .verdict import _signature
    claim = next((c for c in ledger['claims'] if c['id'] == claim_id), None)
    if claim is None:
        raise KeyError(f'Unknown ledger claim: {claim_id}')
    attempts = claim.setdefault('reinvestigations', [])
    if len(attempts) >= MAX_REINVESTIGATIONS:
        record_event(ledger, 'reinvest_exhausted',
                     f'{claim_id}: {MAX_REINVESTIGATIONS} attempts spent; keeping current evidence.',
                     actor='vector', claim_id=claim_id)
        return False
    attempt = {'failures': list(failures)}
    mode = _mode_of(claim.get('statement', ''))
    runners = _runners()
    if mode not in runners:
        attempt.update(action='no_rerunnable_detector',
                       detail=f'No deterministic detector for {mode or "unclassified"}; needs a specialist.')
        attempts.append(attempt)
        record_event(ledger, 'reinvestigated', f'{claim_id}: {attempt["detail"]}', actor='vector',
                     claim_id=claim_id, data=attempt)
        return False
    try:
        facts_fn, assess = runners[mode]
        findings, _ = assess(facts_fn(packet))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        attempt.update(action='detector_failed', detail=f'Detector re-run failed: {exc}')
        attempts.append(attempt)
        record_event(ledger, 'reinvestigated', f'{claim_id}: {attempt["detail"]}', actor='vector',
                     claim_id=claim_id, data=attempt)
        return False
    wanted = _signature(claim.get('scope', {}))
    match = next((f for f in findings if _signature(f['scope']) == wanted), None)
    if match is None:
        attempt.update(action='withdrawn',
                       detail=f'{mode} no longer observes this scope; withdrawing the finding.')
        attempts.append(attempt)
        if claim.get('status') in ('supported', 'unresolved'):
            set_status(ledger, claim_id, 'under_investigation',
                       reason='Reinvestigation: originating detector withdrew.', actor='vector')
            set_status(ledger, claim_id, 'contradicted', reason=attempt['detail'], actor='vector')
        record_event(ledger, 'reinvestigated', f'{claim_id}: {attempt["detail"]}', actor='vector',
                     claim_id=claim_id, data=attempt)
        return True
    fresh = [f"C:{r}" for r in match['scope']['components']] + [f"N:{n}" for n in match['scope']['nets']]
    known = {e['id'] for e in claim.get('evidence', [])}
    added = [e for e in fresh if e not in known]
    for evidence_id in added:
        claim['evidence'].append({'id': evidence_id, 'supports': True})
    if not added:
        attempt.update(action='no_new_evidence',
                       detail=f'{mode} confirms the scope but adds no new evidence; stopping retries.')
        attempts.append(attempt)
        record_event(ledger, 'reinvestigated', f'{claim_id}: {attempt["detail"]}', actor='vector',
                     claim_id=claim_id, data=attempt)
        return False
    attempt.update(action='evidence_added', detail=f'{mode} re-run attached {len(added)} evidence links.',
                   added=added)
    attempts.append(attempt)
    record_event(ledger, 'reinvestigated', f'{claim_id}: {attempt["detail"]}', actor='vector',
                 claim_id=claim_id, data=attempt)
    return True


def finalize_verdicts(ledger):
    """Settle every claim as verified, unresolved or rejected with reasons."""
    from .ledger import record_event, set_status
    for claim in ledger['claims']:
        cid = claim['id']
        status = claim.get('status')
        gate = (claim.get('gate1') or {}).get('outcome')
        judge = (claim.get('judge') or {}).get('verdict')
        if status == 'contradicted':
            final = ('rejected', 'Disproven by evidence; see reinvestigation history.'
                     if claim.get('reinvestigations') else 'Detector or challenge disproved the claim.')
        elif status == 'not_applicable':
            final = ('rejected', 'Not applicable to this revision.')
        elif status in ('supported', 'unresolved') and gate == 'fail':
            scope_fail = any(c['name'] == 'scope' and c['result'] == 'contradiction'
                             for c in (claim.get('gate1') or {}).get('checks', []))
            set_status(ledger, cid, 'under_investigation',
                       reason='Gate 1 found a proven contradiction.', actor='verdict')
            if scope_fail:
                set_status(ledger, cid, 'not_applicable',
                           reason='Scoped objects do not exist in this revision.', actor='verdict')
                final = ('rejected', 'Scoped objects do not exist in this revision.')
            else:
                reason = next((c['reason'] for c in claim['gate1']['checks']
                               if c['result'] == 'contradiction'), 'Gate 1 contradiction.')
                set_status(ledger, cid, 'contradicted', reason=reason, actor='verdict')
                final = ('rejected', reason)
        elif status in ('supported', 'unresolved') and gate == 'pass' and judge == 'stands':
            final = ('verified', 'Deterministic checks pass and the independent judge found no error.')
        else:
            parts = []
            if gate in (None, 'incomplete'):
                parts.append('evidence incomplete or interpretation-dependent')
            if judge in ('weakened', 'refuted'):
                parts.append(f'independent judge {judge} the finding')
            elif judge in (None, 'skipped', 'unavailable'):
                parts.append('independent judge verdict unavailable')
            if status not in ('supported', 'unresolved'):
                parts.append(f'status {status}')
            final = ('unresolved', '; '.join(parts) + '.' if parts else 'Unresolved.')
        claim['final'] = {'verdict': final[0], 'reason': final[1]}
        record_event(ledger, 'verdict_recorded', f'{cid}: {final[0]} — {final[1]}', actor='verdict',
                     claim_id=cid, data={'verdict': final[0]})
    counts = {}
    for claim in ledger['claims']:
        verdict = (claim.get('final') or {}).get('verdict', '?')
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def final_review(packet, ledger):
    """Compare accepted findings against each other.

    Detects near-duplicate findings, supported-vs-contradicted pairs on the
    same scope, and conflicting proposed fixes. Concrete contradictions
    reopen the relevant finding for one more targeted reinvestigation.
    Returns the reopened claim IDs.
    """
    from .ledger import record_event, set_status
    reopened = []
    claims = ledger['claims']
    for index, first in enumerate(claims):
        for second in claims[index + 1:]:
            same_scope = _mode_of(first.get('statement')) == _mode_of(second.get('statement')) \
                and first.get('scope') == second.get('scope')
            pair = f"{first['id']} vs {second['id']}"
            if same_scope and first.get('statement') != second.get('statement'):
                for claim in (first, second):
                    claim.setdefault('open_questions', []).append(
                        f'Possible duplicate of a sibling finding ({pair}); confirm they differ.')
                record_event(ledger, 'duplicate_noted',
                             f'{pair}: same scope, different statements; both kept.', actor='verdict')
            statuses = {first.get('status'), second.get('status')}
            if same_scope and statuses == {'supported', 'contradicted'}:
                loser = first if first.get('status') == 'contradicted' else second
                record_event(ledger, 'consistency_conflict',
                             f'{pair}: supported and contradicted findings share a scope; '
                             f'reopening {loser["id"]}.', actor='verdict')
                if loser['id'] not in reopened:
                    set_status(ledger, loser['id'], 'under_investigation',
                               reason='Final review found a conflicting sibling.', actor='verdict')
                    set_status(ledger, loser['id'], 'unresolved',
                               reason='Reopened for targeted reinvestigation.', actor='verdict')
                    reopened.append(loser['id'])
            fixes = [c.get('proposed_fix') for c in (first, second)]
            if all(fixes) and fixes[0].get('reference') == fixes[1].get('reference') \
                    and fixes[0].get('reference'):
                same_pin = fixes[0].get('pin', '') == fixes[1].get('pin', '')
                same_value = fixes[0].get('value', '') == fixes[1].get('value', '')
                same_action = fixes[0].get('action', '') == fixes[1].get('action', '')
                if same_pin and (not same_value or not same_action):
                    for claim in (first, second):
                        claim.setdefault('open_questions', []).append(
                            f'Fix conflicts with {pair}; only one can apply.')
                    record_event(ledger, 'fix_conflict',
                                 f'{pair}: fixes on {fixes[0]["reference"]} pin '
                                 f'{fixes[0].get("pin", "?")} disagree; both kept for review.',
                                 actor='verdict')
    for cid in reopened:
        reinvestigate(packet, ledger, cid, ['Final review found a conflicting sibling finding.'])
        gate1(packet, ledger, cid)
    record_event(ledger, 'final_reviewed',
                 f'Consistency pass complete; {len(reopened)} findings reopened.', actor='verdict',
                 data={'reopened': reopened})
    return reopened


def verify_all(packet, ledger, source_hashes, *, key, model, provider, progress=None,
               calculations=None):
    """Run the full per-finding loop: Gate 1, Gate 2, reinvestigation,
    final verdicts, then cross-finding consistency. Returns merged usage."""
    from .ledger import check_revision, record_event
    from .usage import current_budget
    usage = {}
    if not check_revision(ledger, source_hashes):
        record_event(ledger, 'gate1_refused',
                     'Ledger revision does not match; per-finding verification refused.',
                     actor='verdict')
        return usage

    def merge(sample):
        for field, value in (sample or {}).items():
            if type(value) is int and value >= 0:
                prior = usage.get(field)
                usage[field] = (prior if type(prior) is int and prior >= 0 else 0) + value

    material = [c['id'] for c in ledger['claims'] if c.get('status') in ('supported', 'unresolved')]
    if progress:
        progress(f'Verifying {len(material)} findings individually…')
    failed = []
    for cid in material:
        try:
            record = gate1(packet, ledger, cid, calculations=calculations)
        except (ValueError, TypeError, KeyError, AttributeError):
            record = None
        if record is None or record['outcome'] == 'fail':
            failed.append(cid)
    merge(gate2(packet, ledger, material, key=key, model=model, provider=provider,
                progress=progress))
    for cid in failed:
        claim = next(c for c in ledger['claims'] if c['id'] == cid)
        failures = [c['reason'] for c in (claim.get('gate1') or {}).get('checks', [])
                    if c['result'] == 'contradiction'] or ['Gate 1 failed.']
        while failures:
            try:
                revised = reinvestigate(packet, ledger, cid, failures)
            except (ValueError, TypeError, KeyError, AttributeError):
                break
            if not revised:
                break
            if claim.get('status') not in ('supported', 'unresolved'):
                break
            try:
                record = gate1(packet, ledger, cid, calculations=calculations)
            except (ValueError, TypeError, KeyError, AttributeError):
                break
            failures = [c['reason'] for c in record['checks'] if c['result'] == 'contradiction']
    revised_ids = [c['id'] for c in ledger['claims']
                   if c.get('reinvestigations') and c.get('status') in ('supported', 'unresolved')]
    if revised_ids:
        merge(gate2(packet, ledger, revised_ids, key=key, model=model, provider=provider,
                    progress=progress))
    counts = finalize_verdicts(ledger)
    for _ in range(MAX_CONSISTENCY_PASSES):
        reopened = final_review(packet, ledger)
        if not reopened:
            break
        counts = finalize_verdicts(ledger)
    budget = current_budget()
    record_event(ledger, 'verification_completed',
                 f"Per-finding verification settled {len(material)} findings: "
                 f"{counts.get('verified', 0)} verified, {counts.get('unresolved', 0)} unresolved, "
                 f"{counts.get('rejected', 0)} rejected.",
                 actor='verdict', data={**counts, 'requests': dict(usage)})
    return usage

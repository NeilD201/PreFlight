"""Coverage Proof and Investigation Replay: display projections of records.

Both builders read saved pipeline data only and never infer beyond it.
Counts and timelines always derive from recorded ledger claims, events
and tool tasks; missing sections mean the run predates the ledger.
"""
import re

VERSION = 1
MAX_LISTED = 100

_TITLES = {
    'hypothesized': 'Hypothesis recorded', 'status': 'Status change', 'evidence': 'Evidence linked',
    'merged': 'Duplicate merged', 'tool_executed': 'Evidence gathered', 'tool_unavailable': 'Tool unavailable',
    'atlas_assessed': 'Taxonomy discovery', 'design_assessed': 'Structural discovery',
    'scenario_assessed': 'Scenario discovery', 'doubt_assessed': 'Blind review',
    'gate_a_completed': 'Finding verification', 'gate_a_refused': 'Verification refused',
    'origin_noted': 'Intent flag', 'origin_reviewed': 'Product review',
    'revision_invalidated': 'Revision changed', 'investigation_skipped': 'Investigation skipped',
    'plan_unavailable': 'Plan unavailable', 'challenge': 'Adversarial challenge',
    'challenge_completed': 'Challenge pass', 'fault_skipped': 'Challenge skipped',
    'fault_unavailable': 'Challenge unavailable', 'fix_proposed': 'Fix proposed',
    'fix_verified': 'Fix verification', 'audit': 'Confidence audit',
    'audit_completed': 'Audit pass', 'gauge_skipped': 'Audit skipped',
    'gauge_unavailable': 'Audit unavailable', 'consultation': 'Consultation answer',
    'consultation_completed': 'Consultation pass', 'consult_skipped': 'Consultation skipped',
    'consult_unavailable': 'Consultation unavailable',
    'gate1_recorded': 'Per-finding evidence check', 'gate1_refused': 'Per-finding check refused',
    'judge_reused': 'Judge verdict reused', 'judge_skipped': 'Judge verdict missing',
    'reinvestigated': 'Targeted reinvestigation', 'reinvest_exhausted': 'Retries exhausted',
    'verdict_recorded': 'Finding verdict', 'duplicate_noted': 'Possible duplicate',
    'consistency_conflict': 'Conflicting findings', 'fix_conflict': 'Conflicting fixes',
    'final_reviewed': 'Cross-finding review', 'verification_completed': 'Verification pass',
    'verification_unavailable': 'Verification unavailable',
}


def _mode(statement):
    match = re.match(r'\[([A-Z]+-\d+)\]', str(statement or ''))
    return match[1] if match else 'unclassified'


def _cap(items):
    return {'items': list(items)[:MAX_LISTED], 'overflow': max(0, len(items) - MAX_LISTED)}


def coverage_proof(pipeline):
    """What was assessed, unresolved, excluded or never evaluated."""
    ledger = (pipeline or {}).get('ledger')
    if not isinstance(ledger, dict):
        return None
    claims = ledger.get('claims', [])
    by_status = {s: 0 for s in ('hypothesized', 'under_investigation', 'supported',
                                'contradicted', 'unresolved', 'not_applicable')}
    by_origin, by_mode = {}, {}
    for claim in claims:
        by_status[claim.get('status', 'hypothesized')] = by_status.get(claim.get('status'), 0) + 1
        origin = claim.get('origin', '?')
        by_origin[origin] = by_origin.get(origin, 0) + 1
        mode = _mode(claim.get('statement'))
        by_mode[mode] = by_mode.get(mode, 0) + 1
    covered = ledger.get('coverage', {})
    gaps = {}
    for event in ledger.get('events', []):
        if event.get('kind') == 'doubt_assessed':
            gaps = event.get('data', {}).get('gaps', {})
    tasks = [{'id': t.get('id', ''), 'tool': t.get('tool', ''), 'references': t.get('references', []),
              'status': t.get('status', ''), 'limitations': t.get('limitations', [])}
             for t in (pipeline or {}).get('tasks', [])]
    model = (pipeline or {}).get('product_model', {}) or {}
    verification = []
    for claim in claims:
        if claim.get('status') not in ('supported', 'unresolved', 'contradicted', 'not_applicable'):
            continue
        gate = claim.get('gate1') or {}
        verification.append({'id': claim['id'], 'statement': claim.get('statement', ''),
                             'status': claim.get('status', ''),
                             'final': (claim.get('final') or {}).get('verdict', 'unrecorded'),
                             'final_reason': (claim.get('final') or {}).get('reason', ''),
                             'gate1': gate.get('outcome', 'unrecorded'),
                             'gate1_checks': [{'name': c.get('name', ''), 'result': c.get('result', ''),
                                               'reason': c.get('reason', '')}
                                              for c in gate.get('checks', [])],
                             'judge': (claim.get('judge') or {}).get('verdict', 'unrecorded'),
                             'judge_reason': (claim.get('judge') or {}).get('reason', ''),
                             'reinvestigations': [{'action': a.get('action', ''),
                                                   'detail': a.get('detail', '')}
                                                  for a in claim.get('reinvestigations', [])],
                             'gauge_sureness': claim.get('gauge_sureness'),
                             'open_questions': claim.get('open_questions', [])})
    return {'claims_total': len(claims), 'by_status': by_status, 'by_origin': by_origin, 'by_mode': by_mode,
            'examined_components': _cap(covered.get('examined_components', [])),
            'examined_nets': _cap(covered.get('examined_nets', [])),
            'verification': verification[:MAX_LISTED],
            'verification_overflow': max(0, len(verification) - MAX_LISTED),
            'unresolved': [{'id': c['id'], 'statement': c.get('statement', ''),
                            'open_questions': c.get('open_questions', [])}
                           for c in claims if c.get('status') == 'unresolved'][:MAX_LISTED],
            'unresolved_overflow': max(0, sum(c.get('status') == 'unresolved' for c in claims) - MAX_LISTED),
            'gaps': gaps, 'tool_runs': tasks,
            'intent_questions': model.get('intent_questions', []),
            'limitations': list(dict.fromkeys((ledger.get('limitations', []) or [])
                                              + ((pipeline or {}).get('limitations', []) or [])))}


def replay_timeline(pipeline):
    """Chronological investigation history from recorded events."""
    ledger = (pipeline or {}).get('ledger')
    if not isinstance(ledger, dict):
        return []
    statements = {c['id']: str(c.get('statement', '')) for c in ledger.get('claims', [])}
    timeline = []
    for event in sorted(ledger.get('events', []), key=lambda e: e.get('seq', 0)):
        entry = {'seq': event.get('seq', 0), 'kind': event.get('kind', ''),
                 'actor': event.get('actor', ''), 'title': _TITLES.get(event.get('kind'), event.get('kind', '')),
                 'detail': event.get('detail', ''), 'claim_id': event.get('claim_id')}
        if entry['claim_id'] in statements:
            entry['statement'] = statements[entry['claim_id']][:200]
        timeline.append(entry)
    model = (pipeline or {}).get('product_model', {}) or {}
    for question in model.get('intent_questions', []):
        timeline.append({'seq': len(timeline) + 1, 'kind': 'intent_question', 'actor': 'origin',
                         'title': 'Intent question', 'detail': question.get('question', ''),
                         'claim_id': None, 'context': question.get('context', '')})
    return timeline

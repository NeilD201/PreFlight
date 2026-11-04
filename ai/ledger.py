"""Revision-bound Evidence Ledger: hypotheses, evidence links, honest coverage.

Claims progress hypothesized -> under_investigation -> supported, contradicted,
unresolved or not_applicable. Every transition needs a reason; terminal claims
reopen only through under_investigation. Counts always derive from recorded
entries, never from model assertions about completeness.
"""
import hashlib
import json
import threading
from copy import deepcopy

# Guards claim/event ID generation when independent specialists (consult and
# gauge) run concurrently. Ledger entries are small local mutations; the lock
# is never held across network calls, so it cannot serialize Meta requests.
_lock = threading.RLock()

VERSION = 1
HYPOTHESIZED = 'hypothesized'
UNDER_INVESTIGATION = 'under_investigation'
SUPPORTED = 'supported'
CONTRADICTED = 'contradicted'
UNRESOLVED = 'unresolved'
NOT_APPLICABLE = 'not_applicable'
STATUSES = (HYPOTHESIZED, UNDER_INVESTIGATION, SUPPORTED, CONTRADICTED, UNRESOLVED, NOT_APPLICABLE)
TERMINAL = (SUPPORTED, CONTRADICTED, UNRESOLVED, NOT_APPLICABLE)


def fingerprint(source_hashes):
    """Stable revision identity; stale findings must never reuse it."""
    return hashlib.sha256(json.dumps(source_hashes or {}, sort_keys=True).encode()).hexdigest()


def new_ledger(source_hashes, model=''):
    return {'version': VERSION, 'model': model, 'source_hashes': dict(source_hashes or {}),
            'revision': fingerprint(source_hashes), 'claims': [], 'events': [],
            'limitations': ['Counts reflect recorded ledger entries only, not complete engineering assurance.'],
            'coverage': {}}


def _signature(statement, scope):
    text = ' '.join(str(statement).split()).casefold()
    parts = [text] + sorted(scope.get('components', [])) + sorted(scope.get('nets', []))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:20]


def add_claim(ledger, *, origin, statement, scope=None, evidence_ids=(), assumptions=(), agent_sureness=None,
              open_questions=()):
    """Record a hypothesis; duplicates merge without losing their origin."""
    with _lock:
        return _add_claim_locked(ledger, origin=origin, statement=statement, scope=scope,
                                 evidence_ids=evidence_ids, assumptions=assumptions,
                                 agent_sureness=agent_sureness, open_questions=open_questions)


def _add_claim_locked(ledger, *, origin, statement, scope=None, evidence_ids=(), assumptions=(),
                      agent_sureness=None, open_questions=()):
    if not str(statement).strip():
        raise ValueError('A ledger claim needs a non-empty statement.')
    scope = {'components': sorted(set(scope.get('components', ()))) if scope else [],
             'nets': sorted(set(scope.get('nets', ()))) if scope else [],
             'pins': sorted(set(scope.get('pins', ()))) if scope else []}
    signature = _signature(statement, scope)
    for claim in ledger['claims']:
        if claim['signature'] == signature:
            record_event(ledger, 'merged', f'Duplicate of {claim["id"]} from {origin}.',
                         actor=origin, claim_id=claim['id'])
            return claim['id']
    claim = {'id': f'H{len(ledger["claims"])+1:03}', 'signature': signature, 'origin': origin,
             'statement': ' '.join(str(statement).split()),
             'scope': scope, 'evidence': [{'id': e, 'supports': True} for e in dict.fromkeys(evidence_ids)],
             'assumptions': list(assumptions), 'agent_sureness': agent_sureness, 'gauge_sureness': None,
             'status': HYPOTHESIZED, 'verification': 'pending', 'open_questions': list(open_questions)}
    ledger['claims'].append(claim)
    record_event(ledger, 'hypothesized', claim['statement'], actor=origin, claim_id=claim['id'])
    return claim['id']


def record_event(ledger, kind, detail, *, actor, claim_id=None, data=None):
    """Append-only execution history; the future replay reads these entries."""
    with _lock:
        if claim_id is not None and not any(c['id'] == claim_id for c in ledger['claims']):
            raise KeyError(f'Unknown ledger claim: {claim_id}')
        ledger['events'].append({'seq': len(ledger['events'])+1, 'kind': kind, 'detail': str(detail)[:2000],
                                 'actor': actor, 'claim_id': claim_id, 'data': deepcopy(data) if data is not None else {}})
        return ledger['events'][-1]['seq']


def set_status(ledger, claim_id, status, *, reason, actor):
    """Move a claim; rejected or closed claims require a recorded justification."""
    if status not in STATUSES:
        raise ValueError(f'Unknown ledger status: {status}')
    if not str(reason).strip():
        raise ValueError('A ledger status change needs a recorded reason.')
    claim = next((c for c in ledger['claims'] if c['id'] == claim_id), None)
    if claim is None:
        raise KeyError(f'Unknown ledger claim: {claim_id}')
    previous = claim['status']
    allowed = status == previous or (previous == HYPOTHESIZED and status in (UNDER_INVESTIGATION, NOT_APPLICABLE)) \
        or (previous == UNDER_INVESTIGATION and status in TERMINAL) \
        or (previous in TERMINAL and status == UNDER_INVESTIGATION)
    if not allowed:
        raise ValueError(f'Ledger claim {claim_id} cannot move from {previous} to {status}.')
    claim['status'] = status
    record_event(ledger, 'status', f'{previous} -> {status}: {reason}', actor=actor, claim_id=claim_id)
    return claim


def attach_evidence(ledger, claim_id, evidence_id, *, supports=True):
    """Link a C:, N:, T: or D: evidence ID for or against a claim."""
    claim = next((c for c in ledger['claims'] if c['id'] == claim_id), None)
    if claim is None:
        raise KeyError(f'Unknown ledger claim: {claim_id}')
    link = {'id': evidence_id, 'supports': bool(supports)}
    if link not in claim['evidence']:
        claim['evidence'].append(link)
        record_event(ledger, 'evidence', f"{evidence_id} {'supports' if link['supports'] else 'contradicts'} {claim_id}.",
                     actor='vector', claim_id=claim_id)
    return claim


def check_revision(ledger, source_hashes):
    """Confirm the ledger still describes these sources; record when it does not."""
    if fingerprint(source_hashes) == ledger['revision']:
        return True
    record_event(ledger, 'revision_invalidated', 'Sources changed; recorded findings need reevaluation.', actor='vector')
    return False


def finalize(ledger):
    """Stamp coverage derived from recorded entries; call when work stops."""
    by_status = {status: 0 for status in STATUSES}
    components, nets = set(), set()
    for claim in ledger['claims']:
        by_status[claim['status']] += 1
        components.update(claim['scope']['components'])
        nets.update(claim['scope']['nets'])
    for event in ledger['events']:
        components.update(event['data'].get('references', []))
    ledger['coverage'] = {'claims_total': len(ledger['claims']), 'by_status': by_status,
                          'examined_components': sorted(components), 'examined_nets': sorted(nets),
                          'events': len(ledger['events'])}
    return ledger

"""Human approval, rejection, export, and reload persistence (Milestone D).

Thin stateless layer over fix_workspace candidates: every function reads its
state from disk, so approval and export decisions survive restarts and
reloads with no in-memory session. The customer's original files are never
written; export produces a separate corrected project directory only after
explicit approval, and any post-approval modification of the candidate
blocks the export.

States per candidate: validated → approved | rejected. Export requires
approved + a candidate identical to the approved one.
"""
import json
import shutil
from pathlib import Path

from . import file_fixes as fixes
from . import fix_workspace as ws


def _candidate_dir(root, cx):
    return Path(root) / 'candidates' / cx


def _events(root, cx):
    log = _candidate_dir(root, cx) / 'transactions.jsonl'
    if not log.is_file():
        raise fixes.FixError('Candidate %s has no transaction log.' % cx)
    return [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines() if line.strip()]


def _validated_snapshot(root, cx):
    events = _events(root, cx)
    if not any(e.get('event') in ('candidate_validated', 'plan_validated') for e in events):
        raise fixes.FixError('Candidate %s is not validated; only validated candidates can be decided.' % cx)
    created = next(e for e in events if e.get('event') == 'candidate_created')
    return created.get('snapshot')


def _approved_files(root, cx):
    diff = ws.candidate_diff(root, cx)
    files = {}
    for rel, entry in diff.items():
        if entry['changed']:
            target = _candidate_dir(root, cx) / 'work' / rel
            files[rel] = fixes.digest(target.read_bytes())
    return files


def preview_record(root, cx):
    """Human-readable before/after record for review. Read-only."""
    tx = _validated_snapshot(root, cx)
    ws.verify_snapshot(root, tx)
    diff = ws.candidate_diff(root, cx)
    checks = []
    for event in _events(root, cx):
        for check in (event.get('recheck') or {}).get('checks', []) if isinstance(event.get('recheck'), dict) else []:
            checks.append(check)
    return {'candidate': cx, 'snapshot': tx,
            'files_changed': sorted(r for r, d in diff.items() if d['changed']),
            'diffs': {r: d['diff'] for r, d in diff.items() if d['changed']},
            'validation_checks': checks,
            'decision': current_decision(root, cx)}


def current_decision(root, cx):
    decision_file = _candidate_dir(root, cx) / 'decision.json'
    if not decision_file.is_file():
        return {'state': 'undecided'}
    return json.loads(decision_file.read_text(encoding='utf-8'))


def approve(root, cx, *, by='user'):
    """Record explicit human approval of the validated candidate."""
    _validated_snapshot(root, cx)
    if current_decision(root, cx).get('state') == 'approved':
        raise fixes.FixError('Candidate %s is already approved.' % cx)
    record = {'state': 'approved', 'by': by, 'files': _approved_files(root, cx)}
    from datetime import datetime, timezone
    record['at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    (_candidate_dir(root, cx) / 'decision.json').write_text(json.dumps(record, indent=2))
    ws.log_event(root, cx, 'approved', by=by, files=sorted(record['files']))
    return record


def reject(root, cx, reason='', *, by='user'):
    """Record explicit human rejection and restore the candidate to pristine."""
    if current_decision(root, cx).get('state') == 'approved':
        raise fixes.FixError('Candidate %s is already approved; approval stands.' % cx)
    from datetime import datetime, timezone
    record = {'state': 'rejected', 'by': by, 'reason': str(reason)[:500],
              'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    (_candidate_dir(root, cx) / 'decision.json').write_text(json.dumps(record, indent=2))
    ws.log_event(root, cx, 'rejected', by=by, reason=str(reason)[:200])
    ws.rollback_candidate(root, cx)
    return record


def export_approved(root, cx, dest_dir):
    """Copy the approved candidate to a separate corrected project directory.

    Refuses when the candidate was never approved, was rejected, or changed
    after approval. The customer's original is verified untouched.
    """
    decision = current_decision(root, cx)
    if decision.get('state') != 'approved':
        raise fixes.FixError('Candidate %s has no approval; nothing is exported.' % cx)
    cand = _candidate_dir(root, cx)
    current = _approved_files(root, cx)
    if current != decision.get('files'):
        raise fixes.FixError('The candidate changed after approval; re-review before exporting.')
    dest = Path(dest_dir)
    if dest.exists():
        raise fixes.FixError('The export destination already exists; choose an empty location.')
    dest.mkdir(parents=True)
    shutil.copytree(cand / 'work', dest / 'project')
    (dest / 'manifest.json').write_text(json.dumps(
        {'candidate': cx, 'decision': decision,
         'diff': {r: d['diff'] for r, d in ws.candidate_diff(root, cx).items() if d['changed']}},
        indent=2))
    ws.log_event(root, cx, 'exported', dest=str(dest))
    return {'dest': str(dest), 'files': sorted(current)}

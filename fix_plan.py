"""Coordinated multi-step repair on one isolated candidate (Milestone C).

`run_plan` executes an ordered list of supported single-fix steps against one
candidate work directory: per-step recheck via fix_candidate.execute_step,
step snapshots for rollback-to-last-good, stale-step pruning when an earlier
step already resolved the target, bounded per-step retries (no unbounded
loops), declared intermediate expectations, and a final whole-design gate:
every targeted finding must be gone and no net-new findings may remain
relative to the original baseline. Intermediate states live only in the
isolated candidate; a rejected plan rolls back and preserves its reason.

Deterministic; no AI calls. KiCad runs only through the existing
file_fixes/board_edits validation paths.
"""
import shutil
from pathlib import Path

from copy import deepcopy

from . import file_fixes as fixes
from . import fix_workspace as ws
from .fix_candidate import (_issue_code_counts, _baseline_counts, execute_step,
                             execute_board_edits_step, snapshot_rels)


MAX_STEP_ATTEMPTS = 2


def final_validation(original_counts, final_counts, targets, expected, staged_resolved=()):
    """Whole-design gate for a finished candidate. Pure function over counts.

    Accepts only when every targeted code decreased and no code is more
    frequent than in the original baseline. Targets resolved by staged KiCad
    validation (real DRC on before/after copies, for finding classes the
    analysis cannot see) count as resolved when their staged checks passed.
    Expected intermediates must be gone finally; anything else new rejects
    the candidate with its reason.
    """
    removed = {c: original_counts.get(c, 0) - final_counts.get(c, 0) for c in targets}
    staged = set(staged_resolved or ())
    missing = sorted(c for c in targets if removed.get(c, 0) <= 0 and c not in staged)
    added = sorted(set(final_counts) - set(original_counts))
    lingering = sorted(set(expected or ()) & set(final_counts))
    if missing or added or lingering:
        reasons = []
        if missing:
            reasons.append('unresolved: %s' % ', '.join(missing))
        if added:
            reasons.append('newly introduced: %s' % ', '.join(added))
        if lingering:
            reasons.append('expected intermediates still present: %s' % ', '.join(lingering))
        raise fixes.FixError('Final validation failed (%s). Candidate rejected.' % '; '.join(reasons))
    return {'target_removed': removed, 'added_codes': [], 'expected_cleared': sorted(expected or ())}


def _snapshot_step(root, cx, index):
    dest = Path(root) / 'candidates' / cx / 'steps' / str(index)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(Path(root) / 'candidates' / cx / 'work', dest)
    return str(dest)


def _rollback_to_step(root, cx, index):
    cand = Path(root) / 'candidates' / cx
    if index < 0:
        ws.rollback_candidate(root, cx)
        return
    backup = cand / 'work.step-rollback-bak'
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    try:
        (cand / 'work').rename(backup)
        shutil.copytree(cand / 'steps' / str(index), cand / 'work')
    except Exception as exc:
        if backup.exists():
            if (cand / 'work').exists():
                shutil.rmtree(cand / 'work', ignore_errors=True)
            backup.rename(cand / 'work')
        raise fixes.FixError('Step rollback failed and the previous tree was restored: %s' % exc)
    shutil.rmtree(backup, ignore_errors=True)


def run_plan(source_dir, project, steps, root, *, kicad_cli=None, max_steps=8, progress=lambda m: None):
    """Execute ordered single-fix steps on one isolated candidate.

    steps: [{group, expect_introduced=[...], label}]. Returns {candidate,
    snapshot, steps, final, diff, source_untouched}. Raises fixes.FixError
    with the preserved reason when any step cannot be completed within its
    bounded attempts; the candidate is rolled back to its last good state.
    """
    source = Path(source_dir)
    rels = snapshot_rels(project)
    if not rels:
        raise fixes.FixError('No tracked KiCad files to correct.')
    if not steps:
        raise fixes.FixError('An empty repair plan corrects nothing.')
    if len(steps) > max_steps:
        raise fixes.FixError('The plan has %d steps; at most %d are executed.' % (len(steps), max_steps))
    manifest = ws.create_snapshot(source, rels, root)
    candidate = ws.create_candidate(root, manifest['tx'], label='multi-fix:%d-steps' % len(steps))
    cx = candidate['candidate']
    work = Path(candidate['work'])
    # Each step's prepare() verifies its input against project provenance
    # hashes. Step 1 legitimately changes the candidate, so later steps must
    # verify against evolving hashes refreshed from the work dir — never the
    # pristine snapshot. A private working copy keeps the caller's project
    # untouched.
    working = deepcopy(project)
    original_counts, _ = _baseline_counts(source, project, kicad_cli)
    current_counts = dict(original_counts)
    targets, expected_all, staged_resolved, records = set(), set(), set(), []
    ws.log_event(root, cx, 'plan_started', steps=len(steps))
    try:
        for index, step in enumerate(steps):
            group = step.get('group') or {}
            gid = group.get('id', '?')
            codes = {i.code for i in fixes.selected_issues(project, group)}
            if codes and all(c not in current_counts for c in codes) and \
                    all(c in original_counts for c in codes):
                records.append({'group_id': gid, 'status': 'pruned-already-resolved',
                                'detail': 'An earlier step already resolved this finding.'})
                ws.log_event(root, cx, 'step_pruned', group=gid)
                continue
            attempts, done = 0, None
            while attempts < MAX_STEP_ATTEMPTS:
                attempts += 1
                staging = Path(root) / 'candidates' / cx / ('staging-%d-try-%d' % (index, attempts))
                try:
                    if step.get('operations'):
                        done = execute_board_edits_step(
                            work, staging, working, group, step['operations'], current_counts,
                            step.get('expect_introduced', ()),
                            kicad_cli=kicad_cli, progress=progress)
                    else:
                        done = execute_step(work, staging, working, group, current_counts,
                                            step.get('expect_introduced', ()),
                                            kicad_cli=kicad_cli, progress=progress)
                    break
                except fixes.FixError as exc:
                    last_error = exc
                    ws.log_event(root, cx, 'step_attempt_failed',
                                 group=gid, attempt=attempts, error=str(exc)[:300])
            else:
                done = None
            if done is None:
                raise fixes.FixError('Step %s failed after %d attempts: %s'
                                     % (gid, MAX_STEP_ATTEMPTS, last_error))
            expected_all.update(step.get('expect_introduced', ()))
            targets.update(codes)
            working.provenance['source_hashes'] = {
                rel: fixes.digest((work / rel).read_bytes()) for rel in rels
                if (work / rel).is_file()}
            ws.log_event(root, cx, 'hashes_refreshed', group=gid)
            if done['recheck'].get('method') == 'staged-validation' and done['recheck'].get('checks'):
                staged_resolved.update(codes)
            current_counts = dict(done['after_counts'])
            _snapshot_step(root, cx, index)
            records.append({'group_id': gid, 'status': 'completed', 'attempts': attempts,
                            'kind': done['plan'].get('kind', '?'),
                            'recheck': done['recheck']})
            ws.log_event(root, cx, 'step_completed', group=gid, attempt=attempts)
        final_counts = current_counts
        final = final_validation(original_counts, final_counts, targets, expected_all,
                                 staged_resolved=staged_resolved)
        final = dict(final, method='analysis', staged_resolved=sorted(staged_resolved))
        ws.log_event(root, cx, 'plan_validated', targets=sorted(targets))
        drift = dict(ws.source_drift(source, manifest))
        if any(state != 'ok' for state in drift.values()):
            raise fixes.FixError('The source changed during the fix: %s.' % drift)
        ws.verify_snapshot(root, manifest['tx'])
        diff = ws.candidate_diff(root, cx)
        ws.log_event(root, cx, 'candidate_validated', files=sorted(r for r, d in diff.items() if d['changed']))
    except fixes.FixError:
        ws.log_event(root, cx, 'plan_rejected')
        last_good = len([r for r in records if r['status'] == 'completed']) - 1
        try:
            _rollback_to_step(root, cx, last_good)
        except fixes.FixError:
            pass
        raise
    return {'candidate': cx, 'snapshot': manifest['tx'], 'steps': records, 'final': final,
            'diff': diff, 'source_untouched': True}

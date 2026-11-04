"""Single-correction end to end plus the reusable step core (Milestones B+C).

`run_single_fix` takes one supported finding (currently VALUE_MISMATCH or
KICAD_DRC_UNCONNECTED_ITEMS, i.e. whatever file_fixes.prepare supports),
snapshots the customer's files, prepares and applies the edit inside an
isolated candidate, re-analyzes the candidate with the real project checks,
and verifies the original is byte-identical. `execute_step` is the same
prepare/apply/recheck core against an already-open candidate work directory,
used by multi-step plans (fix_plan). Nothing here writes to the customer's
files; promotion to the original requires explicit user approval (Milestone D)
through the existing approval flow.

Deterministic except for the caller's optional AI planner upstream; this
module itself makes no AI calls. KiCad runs only through the existing
file_fixes/board_edits validation paths.
"""
from pathlib import Path

from . import file_fixes as fixes
from . import fix_workspace as ws
from .analysis import analyze_project


def _relpaths(project):
    selected = project.selected_files or {}
    rels = [selected.get('pcb'), selected.get('schematic')]
    return [r for r in rels if r]


def snapshot_rels(project):
    """Every tracked file a candidate must contain.

    prepare() verifies the candidate work dir against the full provenance
    source_hashes (real linked projects include the .kicad_pro and any other
    linked file), so the snapshot must contain every hashed file — not just
    pcb+schematic — or no step on a real project can ever validate.
    """
    hashes = (getattr(project, 'provenance', None) or {}).get('source_hashes', {})
    ordered = list(dict.fromkeys([* _relpaths(project), *hashes]))
    return [r for r in ordered if isinstance(r, str) and r]


def _issue_code_counts(issues):
    counts = {}
    for issue in issues or []:
        code = getattr(issue, 'code', '?')
        counts[code] = counts.get(code, 0) + 1
    return counts


def _baseline_counts(source, project, kicad_cli=None):
    """Analyze the source for symmetric before/after comparison when possible."""
    try:
        baseline = analyze_project(source, kicad_cli=kicad_cli)
        return _issue_code_counts(baseline.issues), 'analysis'
    except Exception as exc:
        return _issue_code_counts(getattr(project, 'issues', [])), \
            'project-issues:%s' % type(exc).__name__


def execute_step(work, staging, project, group, baseline_counts, expect_introduced=(),
                 *, kicad_cli=None, progress=lambda m: None):
    """Prepare, apply and recheck ONE edit against a candidate work directory.

    baseline_counts are the issue-code counts the step starts from (original
    baseline for single fixes, previous step's counts inside a plan). Codes
    listed in expect_introduced may appear without rejecting the step;
    anything else new rejects it. Returns {plan, applied, recheck,
    after_counts} or raises fixes.FixError. Never touches the customer's
    source.
    """
    work, staging = Path(work), Path(staging)
    target_codes = {i.code for i in fixes.selected_issues(project, group)}
    if not target_codes:
        raise fixes.FixError(
            'No supported finding selected for this step: %s has no deterministic edit '
            '(supported: single-component value and unconnected-item repairs).' % (group.get('title') or group.get('id') or 'this finding'))
    plan = fixes.prepare(project, group, work, staging, kicad_cli=kicad_cli, progress=progress)
    applied = fixes.apply(plan, staging)
    progress('Rechecking the corrected candidate…')
    updated = analyze_project(work, kicad_cli=kicad_cli)
    after_counts = _issue_code_counts(updated.issues)
    removed = {c: baseline_counts.get(c, 0) - after_counts.get(c, 0) for c in target_codes}
    added = sorted(set(after_counts) - set(baseline_counts))
    allowed = set(expect_introduced or ())
    unexpected = [c for c in added if c not in allowed]
    seen_targets = [c for c in target_codes if c in baseline_counts]
    if seen_targets:
        method = 'analysis'
        if not any(removed.get(c, 0) > 0 for c in seen_targets):
            raise fixes.FixError('The targeted finding is still present after the edit. Candidate rejected.')
    else:
        # The baseline analysis cannot see this finding class (e.g. KiCad
        # DRC findings when DRC did not run in analysis). The staged KiCad
        # validation inside prepare() is then the recheck: it compared
        # real DRC on copies before and after the edit.
        method = 'staged-validation'
        if not plan.get('checks'):
            raise fixes.FixError('No staged validation evidence for this edit. Candidate rejected.')
    if unexpected:
        raise fixes.FixError('The edit introduced new findings (%s). Candidate rejected.' % ', '.join(unexpected))
    recheck = {'before': dict(baseline_counts), 'after': dict(after_counts),
               'target_removed': removed, 'added_codes': added,
               'expected_introduced': sorted(allowed),
               'method': method, 'checks': list(plan.get('checks', []))}
    return {'plan': plan, 'applied': applied, 'recheck': recheck, 'after_counts': after_counts}


def execute_board_edits_step(work, staging, project, group, operations, baseline_counts,
                             expect_introduced=(), *, kicad_cli=None, progress=lambda m: None):
    """Run one explicit board_edits operation batch against a candidate work dir.

    operations: exact op dicts (e.g. move_footprint with absolute mm); no AI
    planning involved. board_edits.prepare stages before/after copies, applies
    via KiCad, and DRC-compares them; the edited files are then synced back
    into the candidate work dir and rechecked with the real project analysis.
    Returns {plan, applied, recheck, after_counts} or raises fixes.FixError.
    Never touches the customer's source.
    """
    from . import board_edits
    work, staging = Path(work), Path(staging)
    if not operations:
        raise fixes.FixError('A board-edits step needs at least one exact operation.')
    record = board_edits.prepare(project, group, work, staging,
                                 operations=[dict(op) for op in operations],
                                 kicad_cli=kicad_cli, progress=progress)
    for entry in record.get('files', []):
        staged = staging / 'after' / entry['path']
        target = work / entry['path']
        if not staged.is_file() or fixes.digest(staged.read_bytes()) != entry['replacement_sha256']:
            raise fixes.FixError('The staged edit for %s failed verification.' % entry['path'])
        target.write_bytes(staged.read_bytes())
    progress('Rechecking the corrected candidate…')
    updated = analyze_project(work, kicad_cli=kicad_cli)
    after_counts = _issue_code_counts(updated.issues)
    added = sorted(set(after_counts) - set(baseline_counts))
    allowed = set(expect_introduced or ())
    unexpected = [c for c in added if c not in allowed]
    if unexpected:
        raise fixes.FixError('The edit introduced new findings (%s). Candidate rejected.' % ', '.join(unexpected))
    recheck = {'before': dict(baseline_counts), 'after': dict(after_counts),
               'target_removed': {}, 'added_codes': added,
               'expected_introduced': sorted(allowed),
               'method': 'staged-validation',
               'checks': list(record.get('checks', [])) + [
                   'Board-edit DRC compare passed with no new errors or unrouted connections.']}
    return {'plan': record, 'applied': {'status': 'ready'}, 'recheck': recheck,
            'after_counts': after_counts}


def run_single_fix(source_dir, project, group, root, *, kicad_cli=None, progress=lambda m: None):
    """Execute one supported correction end to end on an isolated candidate.

    Returns a record {candidate, snapshot, plan, diff, recheck, source_untouched}.
    Raises fixes.FixError (candidate rejected, reason preserved, original
    untouched) when the edit is unsupported, validation fails, or the recheck
    shows a regression.
    """
    source = Path(source_dir)
    rels = snapshot_rels(project)
    if not rels:
        raise fixes.FixError('No tracked KiCad files to correct.')
    manifest = ws.create_snapshot(source, rels, root)
    candidate = ws.create_candidate(root, manifest['tx'], label='single-fix:%s' % group.get('id', '?'))
    cx = candidate['candidate']
    work = Path(candidate['work'])
    staging = Path(root) / 'candidates' / cx / 'staging'
    before_counts, baseline_origin = _baseline_counts(source, project, kicad_cli)
    ws.log_event(root, cx, 'fix_preparing', group=group.get('id'))
    try:
        step = execute_step(work, staging, project, group, before_counts,
                            kicad_cli=kicad_cli, progress=progress)
        ws.log_event(root, cx, 'fix_prepared', kind=step['plan'].get('kind'),
                     status=step['plan'].get('status'))
        ws.log_event(root, cx, 'fix_applied_to_candidate', status=step['applied'].get('status'))
        recheck = dict(step['recheck'], baseline=baseline_origin)
        ws.log_event(root, cx, 'recheck_completed',
                     recheck={k: recheck[k] for k in ('target_removed', 'added_codes')})
        drift = dict(ws.source_drift(source, manifest))
        if any(state != 'ok' for state in drift.values()):
            raise fixes.FixError('The source changed during the fix: %s.' % drift)
        ws.verify_snapshot(root, manifest['tx'])
        diff = ws.candidate_diff(root, cx)
        ws.log_event(root, cx, 'candidate_validated', files=sorted(r for r, d in diff.items() if d['changed']))
    except fixes.FixError:
        ws.log_event(root, cx, 'candidate_rejected')
        try:
            ws.rollback_candidate(root, cx)
        except fixes.FixError:
            pass
        raise
    return {'candidate': cx, 'snapshot': manifest['tx'], 'plan': step['plan'], 'diff': diff,
            'recheck': recheck, 'source_untouched': True}

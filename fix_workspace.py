"""Immutable snapshots, isolated candidates, and transaction log for Fix Design.

Milestone A foundation: every correction starts from a hash-verified snapshot
of the customer's existing files. The original is never edited; candidates are
edited only inside their own isolated directory; every lifecycle step is
appended to a per-candidate log so any candidate can be rolled back, compared,
or audited. Deterministic: no AI calls, no KiCad calls.

Layout under a caller-provided workspace root (production passes the reports
directory; tests pass a temporary directory):

    <root>/snapshots/<tx>/original/<relpath...>   read-only verified copies
    <root>/snapshots/<tx>/manifest.json           {tx, created, source, files}
    <root>/candidates/<cx>/work/<relpath...>      the only writable copy
    <root>/candidates/<cx>/transactions.jsonl    append-only event log
"""
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
import shutil
import stat

from . import file_fixes as fixes


def _utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _txid():
    return secrets.token_hex(16)


def _safe_relpath(value):
    text = str(value or '').replace('\\', '/').strip()
    if not text or text.startswith('/') or '..' in text.split('/'):
        raise fixes.FixError('Refusing unsafe file path %r.' % (value,))
    return text


def create_snapshot(source_dir, relpaths, root):
    """Copy tracked files into a read-only snapshot with a hash manifest.

    Raises FixError without writing a manifest when anything is missing or a
    hash cannot be reproduced after the copy.
    """
    source = Path(source_dir)
    if not source.is_dir():
        raise fixes.FixError('The source folder is not available for snapshotting.')
    rels = [_safe_relpath(r) for r in (relpaths or [])]
    if not rels:
        raise fixes.FixError('Nothing to snapshot: no tracked files were given.')
    tx = _txid()
    dest = Path(root) / 'snapshots' / tx / 'original'
    dest.mkdir(parents=True, exist_ok=False)
    files = {}
    try:
        for rel in rels:
            origin = source / rel
            if not origin.is_file() or origin.is_symlink():
                raise fixes.FixError('Cannot snapshot %s: not a regular file.' % rel)
            content = origin.read_bytes()
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            files[rel] = fixes.digest(content)
        manifest = {'tx': tx, 'created': _utcnow(), 'source': str(source),
                    'files': files}
        (dest.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        for path in dest.rglob('*'):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        check = verify_snapshot(root, tx)
    except fixes.FixError:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise
    return check


def _read_manifest(root, tx):
    manifest_path = Path(root) / 'snapshots' / tx / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise fixes.FixError('Snapshot %s is missing or unreadable.' % tx)
    if not isinstance(manifest, dict) or manifest.get('tx') != tx:
        raise fixes.FixError('Snapshot %s manifest is invalid.' % tx)
    return manifest


def verify_snapshot(root, tx):
    """Re-hash every snapshot copy; raise on any mismatch, loss, or extra file."""
    manifest = _read_manifest(root, tx)
    original = Path(root) / 'snapshots' / tx / 'original'
    expected = manifest.get('files') or {}
    if not isinstance(expected, dict) or not expected:
        raise fixes.FixError('Snapshot %s manifest lists no files.' % tx)
    actual = {}
    for path in sorted(original.rglob('*')):
        if path.is_file():
            actual[path.relative_to(original).as_posix()] = fixes.digest(path.read_bytes())
    if set(actual) != set(expected):
        raise fixes.FixError('Snapshot %s file set changed (expected %d files, found %d).'
                             % (tx, len(expected), len(actual)))
    for rel, digest in expected.items():
        if actual[rel] != digest:
            raise fixes.FixError('Snapshot %s file %s no longer matches its manifest.' % (tx, rel))
    manifest['verified_at'] = _utcnow()
    return manifest


def source_drift(source_dir, manifest):
    """Compare live source files against the snapshot manifest (read-only).

    Returns [(rel, state)] with state 'ok', 'changed', 'missing', or
    'untracked'. Drift never mutates the snapshot; callers decide whether a
    drifted source may still be used.
    """
    source = Path(source_dir)
    drift = []
    for rel, digest in (manifest.get('files') or {}).items():
        origin = source / rel
        if not origin.is_file() or origin.is_symlink():
            drift.append((rel, 'missing'))
        elif fixes.digest(origin.read_bytes()) != digest:
            drift.append((rel, 'changed'))
        else:
            drift.append((rel, 'ok'))
    return drift


def _candidate_dir(root, cx):
    return Path(root) / 'candidates' / cx


def log_event(root, cx, event, **details):
    record = {'event': event, 'at': _utcnow(), **details}
    with open(_candidate_dir(root, cx) / 'transactions.jsonl', 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(record) + '\n')
    return record


def create_candidate(root, tx, label=''):
    """Create an isolated writable candidate from a verified snapshot."""
    manifest = verify_snapshot(root, tx)
    cx = _txid()[:16]
    cand = _candidate_dir(root, cx)
    if cand.exists():
        raise fixes.FixError('Candidate id collision; retry creation.')
    work = cand / 'work'
    shutil.copytree(Path(root) / 'snapshots' / tx / 'original', work)
    for path in work.rglob('*'):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    (cand / 'transactions.jsonl').write_text('', encoding='utf-8')
    log_event(root, cx, 'candidate_created', snapshot=tx, label=str(label or ''),
              files=sorted(manifest.get('files') or {}))
    return {'candidate': cx, 'snapshot': tx, 'work': str(work)}


def candidate_diff(root, cx):
    """Compare candidate work files against their snapshot originals.

    Returns {rel: {'changed': bool, 'diff': str|None}}; binary or large
    changes report changed=True with diff=None instead of guessing.
    """
    import difflib
    cand = _candidate_dir(root, cx)
    events = (cand / 'transactions.jsonl').read_text(encoding='utf-8').splitlines()
    tx = json.loads(events[0]).get('snapshot') if events else None
    if not tx:
        raise fixes.FixError('Candidate %s has no recorded snapshot.' % cx)
    verify_snapshot(root, tx)
    original = Path(root) / 'snapshots' / tx / 'original'
    work = cand / 'work'
    report = {}
    for path in sorted(original.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(original).as_posix()
        before = path.read_bytes()
        target = work / rel
        after = target.read_bytes() if target.is_file() else None
        if after is None or after == before:
            report[rel] = {'changed': after is not None and after != before, 'diff': None}
            continue
        try:
            diff = ''.join(difflib.unified_diff(
                before.decode('utf-8-sig').splitlines(True),
                after.decode('utf-8-sig').splitlines(True),
                fromfile=rel + ' (snapshot)', tofile=rel + ' (candidate)'))
            report[rel] = {'changed': True, 'diff': diff[:40000] or None}
        except (UnicodeError, ValueError):
            report[rel] = {'changed': True, 'diff': None}
    return report


def rollback_candidate(root, cx):
    """Restore candidate work files from the verified snapshot, atomically.

    The work directory is swapped via a backup rename so a failed copy can
    never leave a half-restored candidate: on failure the previous work tree
    is put back and the candidate is marked corrupt in its own log.
    """
    cand = _candidate_dir(root, cx)
    events = (cand / 'transactions.jsonl').read_text(encoding='utf-8').splitlines()
    tx = json.loads(events[0]).get('snapshot') if events else None
    if not tx:
        raise fixes.FixError('Candidate %s has no recorded snapshot.' % cx)
    verify_snapshot(root, tx)
    work = cand / 'work'
    backup = cand / 'work.rollback-bak'
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    try:
        os_replace_dir(work, backup)
        shutil.copytree(Path(root) / 'snapshots' / tx / 'original', work)
    except Exception as exc:
        if backup.exists():
            if work.exists():
                shutil.rmtree(work, ignore_errors=True)
            backup.rename(work)
        log_event(root, cx, 'rollback_failed', error=str(exc)[:300])
        raise fixes.FixError('Rollback failed and the previous work tree was restored: %s' % exc)
    shutil.rmtree(backup, ignore_errors=True)
    for path in work.rglob('*'):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    return log_event(root, cx, 'rollback_completed', snapshot=tx)


def os_replace_dir(src, dst):
    """Rename directory src onto dst (dst must not exist)."""
    if not src.exists():
        raise fixes.FixError('Nothing to roll back: the work tree is missing.')
    src.rename(dst)

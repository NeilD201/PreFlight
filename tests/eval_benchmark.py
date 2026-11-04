"""Versioned reliability benchmark for the deterministic pipeline. No provider calls.

Runs every labeled case through discovery, Gate A, ORIGIN and DOUBT (the
parts that need no model), then reports recall, forbidden hits, citation
accuracy, fix validity and determinism. Usage: python3 tests/eval_benchmark.py
[--json]. Exit status is 1 when any case fails its contract.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pcb_bootstrap  # noqa: F401,E402

from eval_cases import CASES, EVAL_VERSION
from pcb_checker.ai import atlas, design_scan, doubt, ledger, origin, scenario_scan, verdict


def run_pipeline(packet, setup=None):
    """Full deterministic flow: discovery, Gate A, ORIGIN, DOUBT, re-verify."""
    book = ledger.new_ledger({})
    if setup is not None:
        setup(book)
    atlas.discover(packet, book)
    design_scan.discover(packet, book)
    scenario_scan.discover(packet, book)
    verdict.verify(packet, book, {})
    model = origin.build_model(packet)
    origin.review_claims(book, model)
    doubt.assess(packet, book)
    verdict.verify(packet, book, {})
    origin.review_claims(book, model)
    ledger.finalize(book)
    return book


def _mode_of(claim):
    match = re.match(r'\[([A-Z]+-\d+)\]', claim.get('statement', ''))
    return match[1] if match else 'unclassified'


def modes_of(book):
    found = {}
    for claim in book['claims']:
        found.setdefault(_mode_of(claim), []).append(claim['status'])
    return found


def run_case(case):
    """Execute one case; return its measured record."""
    packet = case['packet']
    if 'fix_expected' in case:
        book = ledger.new_ledger({})
        case['setup'](book)
        target = next(c['id'] for c in book['claims'] if c.get('proposed_fix'))
        outcome = verdict.verify_fix(packet, book, target)
        passed = outcome == case['fix_expected']
        return {'id': case['id'], 'category': case['category'], 'kind': 'fix',
                'passed': passed, 'detail': f'fix {outcome}, expected {case["fix_expected"]}'}
    book = run_pipeline(packet, case.get('setup'))
    return evaluate(book, case)


def evaluate(book, case):
    """Score a finished ledger against the case contract."""
    actual = modes_of(book)
    missing = sorted(set(case.get('present', ())) - set(actual))
    forbidden = sorted(set(case.get('forbidden', ())) & set(actual))
    failures = [f'missing {m}' for m in missing] + [f'forbidden {m}' for m in forbidden]
    if 'status_of' in case:
        mode, want = case['status_of']
        got = [c['status'] for c in book['claims']
               if c.get('origin') == 'seed' and _mode_of(c) == mode]
        if not got or any(s != want for s in got):
            failures.append(f'seeded {mode} statuses {got}, want all {want}')
    if 'claims_total' in case and len(book['claims']) != case['claims_total']:
        failures.append(f"{len(book['claims'])} claims, want {case['claims_total']}")
    data = verdict.gate_facts(case['packet'])
    bad_links, bad_supported = 0, 0
    for claim in book['claims']:
        ok, _ = verdict.check_links(data, claim)
        if not ok:
            bad_links += 1
        if claim['status'] == 'supported':
            scopes_ok, _ = verdict.check_scope(data, claim)
            if not scopes_ok:
                bad_supported += 1
    coverage = book.get('coverage', {})
    return {'id': case['id'], 'category': case['category'], 'kind': 'pipeline',
            'passed': not failures, 'detail': '; '.join(failures) if failures else 'ok',
            'claims': len(book['claims']), 'bad_links': bad_links, 'bad_supported': bad_supported,
            'examined': len(coverage.get('examined_components', []))}


def summarize(records):
    """Aggregate recall, precision signals and validity across records."""
    pipe = [r for r in records if r['kind'] == 'pipeline']
    fixes = [r for r in records if r['kind'] == 'fix']
    recall_cases = [c for c in CASES if c.get('present')]
    detected = sum(1 for c in recall_cases
                   if not any(f.startswith('missing') for f in
                              next(r['detail'] for r in records if r['id'] == c['id']).split('; ')))
    return {'eval_version': EVAL_VERSION, 'cases': len(records),
            'passed': sum(r['passed'] for r in records),
            'recall': [detected, len(recall_cases)],
            'forbidden_hits': sum('forbidden' in r['detail'] for r in pipe),
            'unsupported_supported': sum(r.get('bad_supported', 0) for r in pipe),
            'unresolved_links': sum(r.get('bad_links', 0) for r in pipe),
            'fix_validity': [sum(r['passed'] for r in fixes), len(fixes)],
            'records': records}


def main(argv):
    records = [run_case(case) for case in CASES]
    report = summarize(records)
    if '--json' in argv:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        print(f"PreFlight eval v{report['eval_version']}: "
              f"{report['passed']}/{report['cases']} cases passed, "
              f"recall {report['recall'][0]}/{report['recall'][1]}, "
              f"forbidden hits {report['forbidden_hits']}, "
              f"unsupported-supported {report['unsupported_supported']}, "
              f"unresolved links {report['unresolved_links']}, "
              f"fix validity {report['fix_validity'][0]}/{report['fix_validity'][1]}")
        for record in records:
            mark = 'ok' if record['passed'] else 'FAIL'
            print(f"  [{mark}] {record['id']} ({record['category']}): {record['detail']}")
    return 0 if report['passed'] == report['cases'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))

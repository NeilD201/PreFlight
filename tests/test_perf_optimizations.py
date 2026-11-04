"""Regression tests for the performance optimizations (isolated perf copy).

These tests pin the safety properties of the concurrent paths:
- LCSC prefetch issues one fetch per distinct key and preserves order.
- Ledger IDs stay unique when specialists run concurrently.
- The shared request budget enforces its limit across threads.
- consult and gauge both execute (no context-loss skip) when run via the
  threaded orchestration path.
"""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.datasheets import LCSCClient, collect_datasheets, DatasheetError
from pcb_checker.ai import investigation as workflow, reviewer
from pcb_checker.ai.ledger import new_ledger, add_claim, record_event
from pcb_checker.ai.usage import RequestBudget, request_budget, current_budget
from pcb_checker.models import Project, Component, ComponentRecord
from test_circuit_audit import fixture as audit_fixture


def _project(refs_values):
    p = Project('perf-test', selected_files={'pcb': 'board.kicad_pcb'},
                provenance={'source_hashes': {'board.kicad_pcb': 'abc'}})
    for ref, value in refs_values:
        p.components[ref] = Component(
            ref, pcb=[ComponentRecord(ref, value, 'Resistor_SMD:R_0402_1005Metric',
                                      'board.kicad_pcb', 1)])
    return p


class ParallelFetchTests(unittest.TestCase):
    def test_one_fetch_per_distinct_key_and_order_preserved(self):
        refs = [(f'R{i}', '10k') for i in range(1, 9)] + [('C1', '100n')]
        p = _project(refs)
        calls = []
        lock = threading.Lock()

        def fake_passive(ref, value, footprint):
            with lock:
                calls.append(('passive', ref, value))
            return [{'code': 'C25744', 'mpn': 'X', 'package': '0402',
                     'parameters': {'Resistance': '10k'}}]

        def fake_search(value):
            with lock:
                calls.append(('search', value))
            return [{'code': 'C1111', 'mpn': 'Y', 'package': '0402'}]

        doc = {'code': 'C25744', 'mpn': 'X', 'package': '0402', 'manufacturer': 'M',
               'product_url': 'u', 'pdf_url': 'p', 'pages': []}
        with tempfile.TemporaryDirectory() as td, \
                patch.object(LCSCClient, 'passive_search', side_effect=fake_passive), \
                patch.object(LCSCClient, 'search', side_effect=fake_search), \
                patch.object(LCSCClient, 'document', return_value=doc):
            result = collect_datasheets(p, td)
        passive_calls = [c for c in calls if c[0] == 'passive']
        # Eight R refs share one passive key and C1 has its own: one fetch each.
        self.assertEqual(sorted(passive_calls),
                         [('passive', 'C1', '100n'), ('passive', 'R1', '10k')])
        self.assertEqual([c for c in calls if c[0] == 'search'], [])
        self.assertEqual([part['reference'] for part in result['parts']],
                         ['C1'] + [ref for ref, _ in refs if ref != 'C1'])
        self.assertTrue(all(part['code'] for part in result['parts']))

    def test_search_error_still_fails_only_that_reference(self):
        p = _project([('R1', '10k'), ('U1', 'LM358')])
        with tempfile.TemporaryDirectory() as td, \
                patch.object(LCSCClient, 'passive_search', return_value=[]), \
                patch.object(LCSCClient, 'search', side_effect=DatasheetError('down')), \
                patch.object(LCSCClient, 'document', return_value={}):
            result = collect_datasheets(p, td)
        by_ref = {part['reference']: part for part in result['parts']}
        self.assertEqual(by_ref['U1']['status'], 'failed')
        self.assertIn('R1', by_ref)


class LedgerConcurrencyTests(unittest.TestCase):
    def test_claim_and_event_ids_stay_unique_under_threads(self):
        ledger = new_ledger({'a': 'b'})
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(
                lambda i: add_claim(ledger, origin=f'agent-{i % 3}',
                                    statement=f'claim number {i}'),
                range(60)))
            seqs = list(pool.map(
                lambda i: record_event(ledger, 'note', f'detail {i}', actor='t'),
                range(60)))
        self.assertEqual(len(set(ids)), 60)
        self.assertEqual(len(set(seqs)), 60)


class BudgetConcurrencyTests(unittest.TestCase):
    def test_consume_enforces_limit_across_threads(self):
        budget = RequestBudget(limit=10, mode='complete')
        errors = []

        def work():
            try:
                budget.consume()
            except Exception as exc:  # noqa: BLE001 - counting rejections
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: work(), range(30)))
        self.assertEqual(budget.requests, 10)
        self.assertEqual(len(errors), 20)

    def test_copied_context_shares_one_budget(self):
        with request_budget('complete', 'review') as budget:
            context = copy_context()

            def work():
                current_budget().consume()

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: context.run(work), range(4)))
            self.assertEqual(budget.requests, 4)
            self.assertIs(current_budget(), budget)


class SpecialistThreadingTests(unittest.TestCase):
    def test_consult_and_gauge_both_run_on_worker_threads(self):
        project = audit_fixture()
        packet = reviewer.evidence_packet(project)
        seen = {}
        # Force the two specialists to overlap inside their contexts: sharing
        # one Context object raises RuntimeError on the second entry.
        barrier = threading.Barrier(2, timeout=30)

        def fake_fault(packet, ledger, *, key, model, provider, progress=None, **kwargs):
            seen['fault'] = threading.current_thread().name
            return {'requests': 1}

        def fake_consult(packet, ledger, *, key, model, provider, progress=None, **kwargs):
            seen['consult'] = threading.current_thread().name
            seen['consult_budget'] = current_budget() is not None
            barrier.wait()
            return {'requests': 1}

        def fake_gauge(packet, ledger, *, key, model, provider, progress=None, **kwargs):
            seen['gauge'] = threading.current_thread().name
            seen['gauge_budget'] = current_budget() is not None
            barrier.wait()
            return {'requests': 1}

        with request_budget(), \
                patch.object(workflow, 'ask_plan',
                             return_value=({'product_hypothesis': '', 'tasks': []}, {})), \
                patch('pcb_checker.ai.reverify.verify_all', return_value={}), \
                patch('pcb_checker.ai.fault.challenge', side_effect=fake_fault), \
                patch('pcb_checker.ai.consult.consult', side_effect=fake_consult), \
                patch('pcb_checker.ai.gauge.audit', side_effect=fake_gauge):
            _, trace, usage = workflow.investigate(
                project, packet, key='fake', model='test', provider='meta')
        # Both specialists executed (a shared Context entered twice raises
        # RuntimeError; a missing context makes them skip as reserved).
        self.assertTrue(seen.get('consult', '').startswith('vector-specialist'))
        self.assertTrue(seen.get('gauge', '').startswith('vector-specialist'))
        self.assertTrue(seen.get('consult_budget'))
        self.assertTrue(seen.get('gauge_budget'))
        kinds = [e['kind'] for e in trace['ledger']['events']]
        self.assertNotIn('consult_unavailable', kinds)
        self.assertNotIn('gauge_unavailable', kinds)
        self.assertEqual(usage.get('requests'), 3)


if __name__ == '__main__':
    unittest.main()

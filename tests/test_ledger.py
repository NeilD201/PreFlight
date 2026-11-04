"""Evidence Ledger: deterministic claims, transitions, coverage and revision binding."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import investigation as workflow, ledger, reviewer
from pcb_checker.ai.usage import request_budget
from test_circuit_audit import fixture
from test_investigation import plan


class LedgerTests(unittest.TestCase):
    def test_duplicate_claims_merge_without_losing_origin(self):
        book = ledger.new_ledger({'board.kicad_pcb': 'abc'})
        first = ledger.add_claim(book, origin='scope', statement='  U1 may brown out. ',
                                 scope={'components': ['U1']})
        second = ledger.add_claim(book, origin='circuit', statement='u1 MAY brown out?',
                                  scope={'components': ['U2']})
        self.assertEqual((first, second), ('H001', 'H002'))
        merged = ledger.add_claim(book, origin='doubt', statement='U1 may brown out.',
                                  scope={'components': ['U1']})
        self.assertEqual(merged, 'H001')
        self.assertEqual(len(book['claims']), 2)
        self.assertTrue(any(e['kind'] == 'merged' and 'doubt' in e['detail'] for e in book['events']))

    def test_empty_statements_and_unknown_claims_rejected(self):
        book = ledger.new_ledger({})
        with self.assertRaises(ValueError):
            ledger.add_claim(book, origin='scope', statement='   ')
        with self.assertRaises(KeyError):
            ledger.set_status(book, 'H999', ledger.UNDER_INVESTIGATION, reason='x', actor='vector')
        with self.assertRaises(KeyError):
            ledger.attach_evidence(book, 'H999', 'C:U1')
        with self.assertRaises(KeyError):
            ledger.record_event(book, 'note', 'x', actor='vector', claim_id='H999')

    def test_status_moves_need_reasons_and_cannot_skip_investigation(self):
        book = ledger.new_ledger({})
        cid = ledger.add_claim(book, origin='scope', statement='U1 may brown out.')
        with self.assertRaises(ValueError):
            ledger.set_status(book, cid, ledger.SUPPORTED, reason='skipped ahead', actor='verdict')
        with self.assertRaises(ValueError):
            ledger.set_status(book, cid, ledger.UNDER_INVESTIGATION, reason='  ', actor='vector')
        with self.assertRaises(ValueError):
            ledger.set_status(book, cid, 'proven', reason='not a status', actor='vector')
        ledger.set_status(book, cid, ledger.UNDER_INVESTIGATION, reason='assigned', actor='vector')
        ledger.set_status(book, cid, ledger.SUPPORTED, reason='measured drop', actor='verdict')
        with self.assertRaises(ValueError):
            ledger.set_status(book, cid, ledger.CONTRADICTED, reason='no direct move', actor='fault')
        ledger.set_status(book, cid, ledger.UNDER_INVESTIGATION, reason='new evidence', actor='vector')
        self.assertEqual(book['claims'][0]['status'], ledger.UNDER_INVESTIGATION)

    def test_evidence_links_record_support_and_contradiction(self):
        book = ledger.new_ledger({})
        cid = ledger.add_claim(book, origin='source', statement='R1 sets 500 mA.',
                               scope={'components': ['R1']}, evidence_ids=['D:code:p1:0'])
        ledger.attach_evidence(book, cid, 'C:R1')
        ledger.attach_evidence(book, cid, 'N:GND', supports=False)
        ledger.attach_evidence(book, cid, 'C:R1')
        self.assertEqual(book['claims'][0]['evidence'],
                         [{'id': 'D:code:p1:0', 'supports': True}, {'id': 'C:R1', 'supports': True},
                          {'id': 'N:GND', 'supports': False}])

    def test_revision_change_invalidates_without_deleting_history(self):
        book = ledger.new_ledger({'board.kicad_pcb': 'abc'})
        self.assertTrue(ledger.check_revision(book, {'board.kicad_pcb': 'abc'}))
        self.assertFalse(ledger.check_revision(book, {'board.kicad_pcb': 'changed'}))
        self.assertEqual(book['events'][-1]['kind'], 'revision_invalidated')

    def test_finalize_coverage_counts_only_recorded_entries(self):
        book = ledger.new_ledger({})
        one = ledger.add_claim(book, origin='a', statement='First.', scope={'components': ['U1'], 'nets': ['3V3']})
        two = ledger.add_claim(book, origin='b', statement='Second.', scope={'components': ['U2']})
        ledger.set_status(book, one, ledger.UNDER_INVESTIGATION, reason='go', actor='vector')
        ledger.set_status(book, one, ledger.UNRESOLVED, reason='no datasheet', actor='verdict')
        ledger.set_status(book, two, ledger.NOT_APPLICABLE, reason='dnp', actor='scope')
        ledger.record_event(book, 'tool_executed', 'inspect', actor='vector', data={'references': ['U3']})
        coverage = ledger.finalize(book)['coverage']
        self.assertEqual(coverage['claims_total'], 2)
        self.assertEqual(coverage['by_status'][ledger.UNRESOLVED], 1)
        self.assertEqual(coverage['by_status'][ledger.NOT_APPLICABLE], 1)
        self.assertEqual(coverage['examined_components'], ['U1', 'U2', 'U3'])
        self.assertEqual(coverage['examined_nets'], ['3V3'])

    def test_finalized_ledger_survives_json_round_trip(self):
        book = ledger.new_ledger({'a': 'b'}, model='m')
        ledger.add_claim(book, origin='a', statement='S.', agent_sureness=80)
        restored = json.loads(json.dumps(ledger.finalize(book)))
        self.assertEqual(restored['coverage']['claims_total'], 1)
        self.assertEqual(len(restored['revision']), 64)


class LedgerWiringTests(unittest.TestCase):
    def setUp(self):
        self.project = fixture()
        self.packet = reviewer.evidence_packet(self.project)

    def test_investigate_records_hypothesis_tool_events_and_coverage(self):
        with patch.object(workflow, 'ask_plan', return_value=(plan(), {})):
            _, trace, _ = workflow.investigate(self.project, self.packet, key='private-key',
                                               model='test', provider='gemini')
        book = trace['ledger']
        origins = {c['origin'] for c in book['claims']}
        self.assertEqual(origins, {'vector_plan', 'scope', 'circuit', 'scenario'})
        mapping = {}
        for c in book['claims']:
            mapping.setdefault(c['status'], []).append(c['statement'][:9])
        self.assertEqual(sorted(mapping['supported']),
                         ['[DSG-001]', '[MFG-001]', '[PWR-001]', '[PWR-001]', '[PWR-003]'])
        self.assertEqual(sorted(mapping['unresolved']), ['A sensor ', '[IF-002] ', '[SCN-001]'])
        self.assertTrue(all(c['verification'] == 'deterministic' for c in book['claims']))
        gate = next(e for e in book['events'] if e['kind'] == 'gate_a_completed')
        self.assertEqual(gate['data']['verified'], len(book['claims']))
        self.assertEqual(sum(e['kind'] == 'gate_a_completed' for e in book['events']), 2)
        assessed = next(e for e in book['events'] if e['kind'] == 'doubt_assessed')
        self.assertEqual(assessed['data']['gaps']['unexamined_other'], ['R1'])
        self.assertTrue(any('[SCN-001]' in c['statement'] for c in book['claims'] if c['origin'] == 'scenario'))
        self.assertTrue(any(e['kind'] == 'scenario_assessed' for e in book['events']))
        circuit_claims = [c for c in book['claims'] if c['origin'] == 'circuit']
        self.assertTrue(any('[DSG-001]' in c['statement'] and c['scope']['nets'] == ['GND']
                            for c in circuit_claims))
        self.assertTrue(any(e['kind'] == 'design_assessed' for e in book['events']))
        atlas_claims = [c for c in book['claims'] if c['origin'] == 'scope']
        self.assertGreaterEqual(len(atlas_claims), 4)
        self.assertTrue(any('[MFG-001]' in c['statement'] and c['scope']['components'] == ['U2']
                            for c in atlas_claims))
        assessed = next(e for e in book['events'] if e['kind'] == 'atlas_assessed')
        self.assertIn('IF-001', assessed['data']['excluded'])
        self.assertTrue(any('R1' in note for note in assessed['data']['notes']['IF-001']))
        executed = [e for e in book['events'] if e['kind'] == 'tool_executed']
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0]['data']['tool'], 'read_datasheet')
        self.assertEqual(executed[0]['data']['references'], ['U1'])
        self.assertIn('U1', book['coverage']['examined_components'])
        self.assertEqual(book['coverage']['claims_total'], len(book['claims']))
        self.assertEqual(book['revision'], ledger.fingerprint(self.project.provenance.get('source_hashes', {})))
        self.assertNotIn('private-key', json.dumps(book))

    def test_skipped_and_failed_runs_keep_a_ledger_with_limitations(self):
        # A fresh complete-tier budget is not scarce; spend it down to the
        # <3-request reserve so the skip path is genuinely exercised.
        with request_budget() as budget:
            while budget.limit - budget.requests >= 3:
                budget.consume()
            _, skipped, _ = workflow.investigate(self.project, self.packet, key='fake',
                                                 model='test', provider='gemini')
        self.assertEqual(skipped['status'], 'skipped')
        self.assertTrue(any(e['kind'] == 'investigation_skipped' for e in skipped['ledger']['events']))
        self.assertEqual(skipped['ledger']['coverage']['claims_total'], 0)
        with patch.object(workflow, 'ask_plan', side_effect=reviewer.AIError('no plan')):
            _, limited, _ = workflow.investigate(self.project, self.packet, key='fake',
                                                 model='test', provider='gemini')
        self.assertEqual(limited['status'], 'limited')
        self.assertTrue(any(e['kind'] == 'plan_unavailable' for e in limited['ledger']['events']))
        self.assertTrue(limited['ledger']['claims'])


if __name__ == '__main__':
    unittest.main()

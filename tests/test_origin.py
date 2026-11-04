"""ORIGIN product model: observed architecture, intent questions, safe review."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import investigation as workflow, ledger, origin, reviewer
from test_circuit_audit import fixture
from test_investigation import plan


def packet_for(components, context=None):
    packet = {'components': components}
    if context is not None:
        packet['design_context'] = context
    return packet


def part(ref, value='', pins=(), mpn='', dnp=False):
    return {'reference': ref, 'value': value, 'footprint': '', 'mpn': mpn, 'dnp': dnp,
            'pins': [{'number': n, 'name': name, 'net': net} for n, name, net in pins]}


class ModelTests(unittest.TestCase):
    def test_observed_architecture_from_facts(self):
        packet = reviewer.evidence_packet(fixture())
        model = origin.build_model(packet)
        self.assertEqual(model['observed']['subsystems'], [['R1', 'U1', 'U2']])
        self.assertEqual(model['observed']['supply_domains'], {'VCC': ['R1', 'U1', 'U2']})
        i2c = next(i for i in model['observed']['interfaces'] if i['protocol'] == 'I2C')
        self.assertEqual(i2c['nets'], ['BUS'])
        self.assertEqual(model['observed']['part_roles'], {'R1': 'passive', 'U1': 'active', 'U2': 'active'})
        self.assertEqual(model['observed']['fitted_refs'], ['R1', 'U1', 'U2'])

    def test_stated_description_confirms_purpose(self):
        packet = packet_for([part('U1', 'X')],
                            context={'description': 'A garden sensor.', 'operating_context': {}})
        model = origin.build_model(packet)
        self.assertEqual(model['confirmed']['purpose'], 'A garden sensor.')
        self.assertEqual(model['inferences'], [])
        self.assertFalse(any(q['kind'] == 'purpose' for q in model['intent_questions']))
        self.assertEqual(origin.summarize(model), 'A garden sensor.')

    def test_unstated_purpose_yields_low_confidence_guesses_and_question(self):
        packet = packet_for([part('U1', 'SENSOR'), part('J1', 'CONN')])
        model = origin.build_model(packet)
        self.assertTrue(all(c['confidence'] == 'low' for c in model['inferences']))
        sensing = next(c for c in model['inferences'] if c['purpose'] == 'sensing')
        self.assertEqual(sensing['basis_refs'], ['U1'])
        questions = [q for q in model['intent_questions'] if q['kind'] == 'purpose']
        self.assertEqual(len(questions), 1)
        self.assertEqual(len(questions[0]['id']), 20)
        self.assertIn('U1', questions[0]['context'])
        self.assertIn('unconfirmed', origin.summarize(model))

    def test_environment_question_needs_heaters_and_missing_temp(self):
        hot = packet_for([part('U1', 'AMS1117')], context={'operating_context': {}})
        self.assertTrue(any(q['kind'] == 'environment' for q in origin.build_model(hot)['intent_questions']))
        stated = packet_for([part('U1', 'AMS1117')],
                            context={'operating_context': {'temperature': '-20 to 60 C'}})
        self.assertFalse(any(q['kind'] == 'environment'
                             for q in origin.build_model(stated)['intent_questions']))
        cold = packet_for([part('R1', '10k')])
        self.assertFalse(any(q['kind'] == 'environment'
                             for q in origin.build_model(cold)['intent_questions']))

    def test_minimal_packet_builds_empty_model(self):
        model = origin.build_model({})
        self.assertEqual(model['observed']['subsystems'], [])
        self.assertEqual(origin.summarize(model), 'Product purpose unknown.')
        json.dumps(model)

    def test_model_survives_json_round_trip(self):
        model = origin.build_model(reviewer.evidence_packet(fixture()))
        restored = json.loads(json.dumps(model))
        self.assertEqual(restored['observed']['fitted_refs'], ['R1', 'U1', 'U2'])


class ReviewTests(unittest.TestCase):
    def test_intention_mentions_flag_without_changing_status(self):
        packet = reviewer.evidence_packet(fixture())
        packet['design_context']['intentions'] = [
            {'group_id': 'R001', 'note': 'U2 placement is intentional for the enclosure.'}]
        book = ledger.new_ledger({})
        multi = ledger.add_claim(book, origin='scope', statement='[PWR-003] concern',
                                 scope={'components': ['U1', 'U2'], 'nets': ['VCC'], 'pins': []})
        solo = ledger.add_claim(book, origin='scope', statement='[PWR-001] concern',
                                scope={'components': ['U1'], 'nets': ['VCC'], 'pins': ['U1.1']})
        for cid in (multi, solo):
            ledger.set_status(book, cid, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, multi, 'supported', reason='r', actor='t')
        ledger.set_status(book, solo, 'unresolved', reason='r', actor='t')
        model = origin.build_model(packet)
        result = origin.review_claims(book, model)
        self.assertEqual(result, {'reviewed': 2, 'flagged': 1})
        flagged = next(c for c in book['claims'] if c['id'] == multi)
        self.assertTrue(any('R001' in q for q in flagged['open_questions']))
        self.assertTrue(any(e['kind'] == 'origin_noted' and e['claim_id'] == multi for e in book['events']))
        self.assertEqual(flagged['status'], 'supported')
        self.assertEqual(next(c for c in book['claims'] if c['id'] == solo)['status'], 'unresolved')
        self.assertTrue(any(e['kind'] == 'origin_reviewed' for e in book['events']))

    def test_repeat_review_never_double_flags(self):
        packet = reviewer.evidence_packet(fixture())
        packet['design_context']['intentions'] = [{'group_id': 'R1', 'note': 'Keep U1 as placed.'}]
        book = ledger.new_ledger({})
        cid = ledger.add_claim(book, origin='scope', statement='[PWR-001] concern',
                               scope={'components': ['U1'], 'nets': [], 'pins': []})
        ledger.set_status(book, cid, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, cid, 'supported', reason='r', actor='t')
        model = origin.build_model(packet)
        first = origin.review_claims(book, model)
        second = origin.review_claims(book, model)
        self.assertEqual((first['flagged'], second['flagged']), (1, 0))
        self.assertEqual(sum(e['kind'] == 'origin_noted' for e in book['events']), 1)
        claim = next(c for c in book['claims'] if c['id'] == cid)
        self.assertEqual(len([q for q in claim['open_questions'] if 'R1' in q]), 1)

    def test_empty_ledger_reviews_quietly(self):
        book = ledger.new_ledger({})
        result = origin.review_claims(book, origin.build_model({}))
        self.assertEqual(result, {'reviewed': 0, 'flagged': 0})


class WiringTests(unittest.TestCase):
    def test_investigate_attaches_product_model_and_reviews(self):
        project = fixture()
        packet = reviewer.evidence_packet(project)
        with patch.object(workflow, 'ask_plan', return_value=(plan(), {})):
            _, trace, _ = workflow.investigate(project, packet, key='k', model='test', provider='gemini')
        model = trace['product_model']
        self.assertEqual(model['observed']['subsystems'], [['R1', 'U1', 'U2']])
        self.assertEqual(len(model['intent_questions']), 1)
        self.assertEqual(model['intent_questions'][0]['kind'], 'purpose')
        reviews = [e for e in trace['ledger']['events'] if e['kind'] == 'origin_reviewed']
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0]['data']['flagged'], 0)
        self.assertGreater(reviews[0]['data']['reviewed'], 0)
        self.assertEqual(reviews[1]['data']['reviewed'], reviews[0]['data']['reviewed'])
        self.assertEqual(reviews[1]['data']['flagged'], 0)
        self.assertNotIn('k-private', json.dumps(model))


class RealBoardRegressionTests(unittest.TestCase):
    def test_dotted_rails_form_supply_domains(self):
        packet = packet_for([part('U1', 'MCU', [('1', 'VDD', '+3.3V')])])
        domains = origin.supply_domains(origin._components(packet))
        self.assertEqual(domains, {'+3.3V': ['U1']})

    def test_regulator_stem_sets_power_role_and_heating(self):
        packet = packet_for([part('U4', 'AP2112K-3.3', [('1', 'VIN', '+5V')])])
        parts = origin._components(packet)
        self.assertEqual(origin.part_roles(parts), {'U4': 'power'})
        model = origin.build_model(packet)
        self.assertTrue(any(q['kind'] == 'environment' for q in model['intent_questions']))


if __name__ == '__main__':
    unittest.main()

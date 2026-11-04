"""Focused comparisons still require complete targets and grounded per-part quotes."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.pin_review import repair_pins, repair_pin_batch, quotation_options, run_connection_review
from pcb_checker.ai.reviewer import evidence_packet, validated_design_items
from test_ai_review import meta_response
from test_pin_analysis import example, answer


class FocusedPinTests(unittest.TestCase):
    def test_connection_refresh_replaces_old_pin_questions_without_extra_requests(self):
        project = example()
        packet = evidence_packet(project)
        fresh = answer(packet)
        old = copy.deepcopy(fresh)
        question = 'What supply voltage will you use?'
        old['actions'][0]['pin_checks'][0].update(decision='needs_context', missing_context=[question])
        old['questions'] = [question]
        project.ai_review = {'status':'completed', 'result':old}
        with patch('pcb_checker.ai.pin_review.repair_pin_batch', return_value=(fresh, [{'input_tokens':100,'output_tokens':20}], [])) as request:
            result = run_connection_review(project,key='dummy',model='gpt-6-luna')
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['rejected_items'],[])
        self.assertNotIn(question,result['result']['questions'])
        self.assertEqual(result['usage']['input_tokens'],100)

    def packet(self):
        p = example()
        p.datasheets['parts'][0]['status'] = 'provisional'
        packet = evidence_packet(p)
        return packet, answer(packet)['actions'][0]['pin_checks']

    def run_repair(self, packet, checks):
        options = quotation_options(packet['datasheet_excerpts'])
        copied = [copy.deepcopy(check) for check in checks]
        for check in copied:
            check['quotation_ids'] = [next((q['id'] for q in options if q['quote'] == c['quote']), 'INVENTED') for c in check.pop('citations')]
        with patch('pcb_checker.ai.reviewer.post_ai', return_value=meta_response({'pin_checks': copied, 'context_response': ''})) as post:
            plan, usage, failures = repair_pins(packet, model='test', key='dummy', request_limits=lambda: {})
        return plan, post, failures

    def test_duplicate_groups_use_one_component_request_and_keep_proof(self):
        packet, checks = self.packet()
        second = copy.deepcopy(packet['review_groups'][0]); second['id'] = 'R099'
        packet['review_groups'].append(second); packet['action_group_ids'].append('R099')
        plan, post, failures = self.run_repair(packet, checks)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(failures, [])
        actions, rejected = validated_design_items(plan, packet, allow_empty=True)
        self.assertEqual(len(actions['actions']), 2)
        self.assertEqual(rejected, [])
        self.assertTrue(all(a['steps'] == [] and a['disposition'] == 'investigate' for a in actions['actions']))
        sent = json.loads(post.call_args.args[0]['input'])
        self.assertEqual(len(sent['targets']), 1)
        self.assertTrue(sent['component']['pins'])

    def test_missing_or_invented_targets_do_not_become_advice(self):
        packet, checks = self.packet()
        for invalid in ([], checks + checks, [{**checks[0], 'pin': '999'}]):
            with self.subTest(invalid=invalid):
                plan, _, failures = self.run_repair(packet, invalid)
                self.assertEqual(plan['actions'], [])
                self.assertEqual(len(failures), 1)

    def test_fabricated_quote_is_rejected_by_normal_validator(self):
        packet, checks = self.packet()
        checks[0]['citations'][0]['quote'] = 'This is an invented requirement with no source.'
        plan, _, failures = self.run_repair(packet, checks)
        self.assertEqual(plan['actions'], [])
        self.assertTrue(failures)

    def test_each_quotation_is_exact_and_bound_to_its_source(self):
        docs = [{'evidence_id': 'D:test:p1', 'text': 'Pin description\n5 VDDIO Power supply for I/O pins\n6 GND 0 V supply\nA sentence that is wrapped\nonto another line.'}]
        choices = quotation_options(docs)
        self.assertTrue(any('wrapped\nonto' in c['quote'] for c in choices))
        for choice in choices:
            self.assertIn(choice['quote'], docs[0]['text'])
            self.assertEqual(choice['evidence_id'], 'D:test:p1')
            self.assertTrue(10 <= len(choice['quote']) <= 350)

    def test_missing_operating_fact_becomes_one_question(self):
        packet, checks = self.packet()
        checks[0].update(decision='needs_context', missing_context=['Will firmware use this pin as an external trigger?'])
        plan, _, _ = self.run_repair(packet, checks)
        self.assertEqual(plan['actions'][0]['steps'], ['Will firmware use this pin as an external trigger?'])
        accepted, rejected = validated_design_items(plan, packet, allow_empty=True)
        self.assertEqual(len(accepted['actions']), 1)
        self.assertEqual(rejected, [])

    def test_configurable_gpio_gets_a_separate_causal_claim_check(self):
        packet, checks = self.packet()
        packet['review_groups'][0]['pin_targets'][0]['name']='GPIO1'
        plan, post, failures=self.run_repair(packet, checks)
        self.assertEqual(post.call_count,2)
        self.assertIn('proposed_checks',json.loads(post.call_args.args[0]['input']))
        self.assertEqual(failures,[])

    def test_required_slots_attach_exact_targets_and_keep_evidence_checks(self):
        packet, checks = self.packet()
        options = quotation_options(packet['datasheet_excerpts'])
        choice = next(q['id'] for q in options if q['quote']==checks[0]['citations'][0]['quote'])
        raw = {'checks': {'P1': {k:v for k,v in checks[0].items() if k not in ('reference','pin','net','citations')} | {'quotation_ids':['U42:'+choice]}}, 'context_responses': {packet['review_groups'][0]['id']:''}}
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=meta_response(raw)) as post:
            plan, usage, failures = repair_pin_batch(packet,model='gpt-6-luna',key='dummy',request_limits=lambda:{})
        self.assertEqual(post.call_count,1)
        self.assertEqual(failures,[])
        accepted,rejected = validated_design_items(plan,packet,allow_empty=True)
        self.assertEqual(rejected,[])
        self.assertEqual(accepted['actions'][0]['pin_checks'][0]['net'],'GND')
        schema=post.call_args.args[0]['response_format']['schema']
        self.assertEqual(schema['properties']['checks']['required'],['P1'])
        self.assertNotIn('reference',schema['properties']['checks']['properties']['P1']['properties'])

    def test_batch_does_not_accept_missing_slot_or_wrong_component_quote(self):
        packet, checks = self.packet()
        for raw in ({'checks':{},'context_responses':{}},
                    {'checks':{'P1':{'decision':'keep','explanation':'Correct address strap.','missing_context':[],'quotation_ids':['U99:Q1']}},'context_responses':{packet['review_groups'][0]['id']:''}}):
            with patch('pcb_checker.ai.reviewer.post_ai',return_value=meta_response(raw)):
                with self.assertRaises(ValueError):
                    repair_pin_batch(packet,model='test',key='dummy',request_limits=lambda:{})

if __name__ == '__main__':
    unittest.main()

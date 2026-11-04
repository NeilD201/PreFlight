"""Project answers survive incomplete interviews, restarts and failed AI calls."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.webapp import AppError
from pcb_checker.storage import WorkspaceStore
from pcb_checker.design_review import context_key, edit_context
from pcb_checker.product_context import project_questions, product_evidence, unanswered_followups
from pcb_checker.ai.reviewer import evidence_packet
from pcb_checker.repair_design import capture_repair_context
from pcb_checker.check_scope import project_scope, YES
from test_design_review import project
import test_webapp as web_tests


class MemoryTests(unittest.TestCase):
    setUp=web_tests.StateTests.setUp
    tearDown=web_tests.StateTests.tearDown

    def prepare(self):
        self.state.project=project();self.state.report_id='saved-review'
        return self.state.project

    def test_continue_persists_before_interview_finish_and_ai(self):
        p=self.prepare();q=project_questions(p)['questions'][0]
        with patch('pcb_checker.webapp.run_ai_review') as provider:
            result=self.state.product_answers({'report_id':'saved-review','context_revision':0,'answers':{q['id']:'Not sure yet'}},save=True)
            provider.assert_not_called()
        restarted=WorkspaceStore(self.state.reports)
        saved=restarted.design_context(context_key(p))
        self.assertEqual(saved['product_context'][q['id']],'Not sure yet')
        self.assertEqual(saved['product_questions'][q['id']],q['question'])
        self.assertNotIn(q['id'],result['unanswered'])
        self.assertNotEqual(result['questions'][0]['id'],q['id'])
        with restarted.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM context_events').fetchone()[0],1)
        with self.assertRaises(AppError):
            self.state.product_answers({'report_id':'saved-review','context_revision':0,'answers':{q['id']:'Stale overwrite'}},save=True)
        with self.assertRaises(AppError):
            self.state.product_answers({'report_id':'another-project','context_revision':1,'answers':{q['id']:'Wrong project'}},save=True)

    def test_partial_updates_preserve_answers_and_blanks_clear_explicitly(self):
        p=self.prepare()
        current={'revision':0,'product_context':{'purpose':'Logger','power':'Battery'}}
        updated=edit_context(p,current,{'product_context':{'environment':'Indoors'}})
        self.assertEqual(updated['product_context'],{'purpose':'Logger','power':'Battery','environment':'Indoors'})
        cleared=edit_context(p,updated,{'product_context':{'power':''}})
        self.assertNotIn('power',cleared['product_context'])
        self.assertEqual(cleared['product_context']['purpose'],'Logger')

    def test_answer_meaning_reaches_all_review_paths_and_exact_repeat_is_hidden(self):
        p=self.prepare();question='What case surrounds the board?'
        self.state.save_question_answer({'report_id':'saved-review','context_revision':0,'question':question,'answer':'Plastic, battery beside the radio'})
        p.design_context=self.state.store.design_context(context_key(p))
        evidence=evidence_packet(p)['design_context']['product_context']
        self.assertEqual(evidence['saved_followup_answers'][0]['question'],question)
        packet=capture_repair_context({},p,p.review['groups'][0],{})
        self.assertIn('Plastic',packet['repair_context']['product_context']['saved_followup_answers'][0]['answer'])
        self.assertEqual(unanswered_followups([question,'What range is needed?'],p.design_context),['What range is needed?'])
        other=copy.deepcopy(p);other.provenance['project_name']='Different product'
        self.assertIsNone(self.state.store.design_context(context_key(other)))

    def test_unit_encoding_repeat_is_hidden_but_distinct_requirement_survives(self):
        context = {'question_answers': {'q1': {'question': 'Is the 100µF capacitor rated 10V?', 'answer': 'Yes, 10V'}}}
        self.assertEqual(unanswered_followups(['Is the 100uF capacitor rated 10V?'], context), [])
        self.assertEqual(unanswered_followups(['Is the 100µF capacitor still effective at 5V bias?'], context),
                         ['Is the 100µF capacitor still effective at 5V bias?'])

    def test_all_answered_questions_have_text_even_outside_first_page(self):
        p=self.prepare()
        answers={q['id']:'Not sure yet' for q in project_questions(p)['all_questions']}
        evidence=product_evidence({'product_context':answers},p)
        self.assertTrue(evidence['answered_questions'])
        self.assertTrue(all(q['question'] and q['answer'] for q in evidence['answered_questions']))
        self.assertFalse(evidence['unanswered'])

    def test_non_electrical_metadata_and_pin_order_do_not_reask(self):
        p=self.prepare();q=project_questions(p)['questions'][0]
        p.components['U42'].schematic[0].properties['Datasheet']='new-local-copy.pdf'
        p.components['U42'].schematic_pins.reverse()
        updated=project_questions(p,{q['id']:'One cell, 100 mAh'})
        self.assertNotIn(q['id'],updated['unanswered'])
        self.assertIn(q['id'],updated['active_question_ids'])

    def test_legacy_scope_answers_survive_revision_and_keep_followups(self):
        p=self.prepare();scope=project_scope(p)
        q=next(q for q in scope['questions'] if q['feature']=='external')
        old='environment:scope_external:0123456789'
        result=project_questions(p,{old:YES})
        self.assertEqual(result['answers'][q['id']],YES)
        self.assertNotIn(old,result['answers'])
        self.assertTrue(any(q.get('feature')=='hotplug' for q in result['all_questions']))

    def test_issue_chat_uses_its_own_memory_and_shared_product_answers(self):
        from pcb_checker.workflow import keyed_groups
        from dataclasses import asdict
        p=self.prepare();key=context_key(p)
        self.state.store.save_context(key,{'product_context':{'purpose':'Battery logger'}},0)
        group=keyed_groups(asdict(p))[0];channel='issue-key:'+group['key']
        for kind,question in [(channel,'Use the low address'),('issue-key:other','Move the antenna'),('general','Write a birthday card')]:
            self.state.store.save_conversation(key,'saved-review',kind,kind,question)
        with patch('pcb_checker.webapp.answer_step',return_value={'status':'ai','answer':'Selected issue answer'}) as send:
            self.state.step_help({'report_id':'saved-review','group_id':group['id'],'step_index':0,'question':'What about this pin?','history':[]})
        context=send.call_args.args[0].design_context
        self.assertEqual([m['question'] for m in context['conversation_memory']],['Use the low address'])
        self.assertEqual(context['product_context']['purpose'],'Battery logger')
        rows=self.state.store.conversations(key,channel)
        self.assertEqual(rows[-1]['answer'],'Selected issue answer')

    def test_chat_is_saved_before_failure_and_restored_for_other_ai_paths(self):
        p=self.prepare()
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'failed','message':'Provider offline'}):
            self.state.project_chat({'report_id':'saved-review','question':'The enclosure is plastic.',
                'history':[{'role':'assistant','text':'What is the enclosure made from?'}]})
        store=WorkspaceStore(self.state.reports)
        rows=store.conversations(context_key(p),'general')
        self.assertEqual(rows[0]['question'],'The enclosure is plastic.')
        self.assertEqual(rows[0]['status'],'failed')
        p.design_context=store.design_context(context_key(p))
        evidence=product_evidence(p.design_context,p)
        self.assertEqual(evidence['saved_conversations'][0]['prompt'],'What is the enclosure made from?')
        self.assertNotIn('Provider offline',json.dumps(evidence['saved_conversations']))


if __name__=='__main__':unittest.main()

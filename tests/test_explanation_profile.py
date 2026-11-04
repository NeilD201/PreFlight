import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.explanation_profile import learn, explanation_profile, experience_level
from pcb_checker.storage import WorkspaceStore
from pcb_checker.product_context import product_evidence, REVIEW_INSTRUCTIONS
from pcb_checker.chat_context import compact_chat
from pcb_checker.design_review import context_key
from pcb_checker.user_profile import guided_questions
from test_design_review import project
import test_webapp


class ExplanationTests(unittest.TestCase):
    def test_free_text_introduction_and_conservative_experience(self):
        first=guided_questions(project())['questions'][0]
        self.assertEqual(first['choices'], [])
        self.assertIn('yourself', first['question'])
        self.assertEqual(experience_level('I design electronics professionally.'), 'technical')
        self.assertEqual(experience_level("I am not an electrical engineer."), 'plain')
        self.assertEqual(experience_level('I can solder but this is my first PCB.'), 'plain')
        self.assertEqual(experience_level('I am an electronics engineer learning RF layout.'), 'technical')
        self.assertEqual(experience_level('I have been designing PCBs for 12 years.'), 'technical')

    def test_feedback_changes_style_without_inventing_circuit_facts(self):
        original={'product_context':{'experience':'I am an electronics engineer'}}
        self.assertEqual(explanation_profile(original)['mode'],'technical')
        learned=learn({}, "I don't understand that. Use plain English.")
        self.assertEqual(explanation_profile({**original,'explanation_preferences':learned})['mode'],'plain')
        self.assertEqual(learn(learned,'Is U3 wired correctly?'),learned)
        self.assertEqual(learn({},"I don't get it")['mode'],'plain')
        self.assertEqual(learn({},'U3','Which part is connected to the battery?'),{})
        follow=learn(learned,'Where to click in KiCad.', 'Is the unclear part its purpose or where to change it in KiCad?')
        self.assertEqual(follow['help_focus'],'where_to_click')
        self.assertEqual(learn(follow,'Use technical language and show equations')['mode'],'technical')
        self.assertNotIn('purpose',learned)

    def test_length_is_independent_of_technical_level(self):
        context={'product_context':{'experience':'I am an electronics engineer'}}
        learned=learn({},'Too long. Keep it short.')
        profile=explanation_profile({**context,'explanation_preferences':learned})
        self.assertEqual(profile['mode'],'technical')
        self.assertEqual(profile['detail'],'brief')
        learned=learn(learned,'Explain more simply, step by step.')
        profile=explanation_profile({**context,'explanation_preferences':learned})
        self.assertEqual(profile['mode'],'plain')
        self.assertEqual(profile['detail'],'detailed')
        self.assertEqual(profile['experience_level'],'technical')

    def test_focused_pin_reviewer_uses_same_style_contract(self):
        from pcb_checker.ai.pin_review import INSTRUCTIONS
        from pcb_checker.explanation_profile import EXPLANATION_INSTRUCTIONS
        self.assertIn(EXPLANATION_INSTRUCTIONS,INSTRUCTIONS)

    def test_persistence_isolation_idempotency_and_no_stale_engineering_review(self):
        with tempfile.TemporaryDirectory() as folder:
            s=WorkspaceStore(folder)
            s.save_context('p',{'product_context':{'experience':'Experienced EE — show electrical limits'}},0)
            s.save_conversation('p','r1','issue-key:a','q1',"I don't understand exposed pad.")
            s.save_conversation('p','r1','issue-key:a','q1',"Use technical language")
            s=WorkspaceStore(folder)
            saved=s.design_context('p')
            self.assertEqual(saved['revision'],1)
            self.assertEqual(explanation_profile(saved)['mode'],'plain')
            self.assertEqual(len(saved['explanation_preferences']['examples']),1)
            self.assertIsNone(s.preference('explanation:other-project'))
            self.assertEqual(product_evidence(saved)['explanation_profile']['mode'],'plain')
            # Rewriting design context cannot overwrite the latest chat preference.
            s.save_context('p',{**saved,'explanation_preferences':{'mode':'technical'}},1)
            self.assertEqual(explanation_profile(s.design_context('p'))['mode'],'plain')

    def test_compaction_preserves_person_and_feedback(self):
        context={'product_context':{'experience':'First PCB'},'explanation_preferences':learn({},'What does pull-up mean?')}
        packet={'question':'Help with U2','project':{'product_context':product_evidence(context)},
                'components':[{'reference':'U'+str(i),'value':'x'*900} for i in range(100)]}
        compacted,_=compact_chat(packet,12000)
        profile=compacted['project']['product_context']['explanation_profile']
        self.assertEqual(profile['terms_to_explain'],['pull-up'])
        self.assertLess(len(json.dumps(compacted)),14000)
        self.assertIn('never remove checks',REVIEW_INSTRUCTIONS)


class ChatLearningTests(unittest.TestCase):
    setUp=test_webapp.StateTests.setUp
    tearDown=test_webapp.StateTests.tearDown

    def test_same_request_and_next_issue_receive_saved_feedback_without_extra_ai_call(self):
        p=project();self.state.project=p;self.state.report_id='report'
        def reply(project,*args,**kwargs):
            self.assertEqual(explanation_profile(project.design_context)['mode'],'plain')
            self.assertIn("I don't understand",str(project.design_context['explanation_preferences']))
            return {'status':'ai','answer':'Here is a simpler explanation.'}
        with patch('pcb_checker.webapp.answer_project',side_effect=reply) as ai:
            result=self.state.project_chat({'report_id':'report','question':"I don't understand; please explain more simply.",'history':[]})
            ai.assert_called_once()
        self.assertEqual(result['explanation_profile']['mode'],'plain')
        saved=self.state.store.design_context(context_key(p))
        self.assertEqual(saved['revision'],1)
        p.design_context=saved
        from pcb_checker.step_help import _packet
        group=p.review['groups'][0]
        packet=_packet(p,group,{'steps':['Explain this connection.']},0,'What next?',[])
        self.assertEqual(packet['project_context']['product_context']['explanation_profile']['mode'],'plain')

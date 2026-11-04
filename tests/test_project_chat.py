import sys,json,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.project_chat import chat_packet,answer_project
from pcb_checker.models import Project
from test_design_review import project

class ChatTests(unittest.TestCase):
    def test_general_chat_needs_no_project_or_issue(self):
        p=chat_packet(None,'Help me write a birthday message',[])
        self.assertIsNone(p['project']);self.assertNotIn('selected_step',p)
        with patch('pcb_checker.project_chat.answer_packet',return_value={'status':'ai','answer':'Hello'}) as send:
            self.assertEqual(answer_project(None,'Hello',[],key='fake',model='test')['answer'],'Hello')
        self.assertEqual(send.call_args.args[0]['question'],'Hello')

    def test_context_and_references_follow_conversation(self):
        p=project();p.design_context['product_context']={'purpose':'Outdoor logger'}
        packet=chat_packet(p,'Why is that needed?',[{'role':'user','text':'Tell me about U42'}])
        self.assertEqual(packet['project']['product_context']['answers']['purpose'],'Outdoor logger')
        self.assertIn('related_evidence',packet)
        self.assertTrue(next(c for c in packet['components'] if c['reference']=='U42')['pins'])
        self.assertLess(len(json.dumps(packet).encode()),48000)

    def test_no_issue_is_selected_for_unrelated_message(self):
        self.assertNotIn('related_evidence',chat_packet(project(),'Give me a dinner idea',[]))
        self.assertEqual(chat_packet(Project('empty'),'How do resistors work?',[])['issues'],[])

    def test_missing_key_uses_no_provider(self):
        with patch('pcb_checker.project_chat.answer_packet') as send:
            self.assertEqual(answer_project(None,'Hi',[])['status'],'failed')
            send.assert_not_called()

class LongChatTests(unittest.TestCase):
    def test_long_followup_keeps_valid_bounded_evidence(self):
        history=[{'role':'user' if i%2==0 else 'assistant','text':'U42 '+('x'*3900)} for i in range(6)]
        packet=chat_packet(project(),'Explain that further',history)
        self.assertLess(len(json.dumps(packet).encode()),48000)
        self.assertEqual(len(packet['recent_chat']),6)

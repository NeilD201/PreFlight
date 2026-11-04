import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.checking_policy import delegated_check, separate_analysis, INSTRUCTIONS
from pcb_checker.product_context import REVIEW_INSTRUCTIONS
from pcb_checker.answer_style import CHAT_STYLE
from pcb_checker.ai.pin_review import INSTRUCTIONS as PIN_INSTRUCTIONS

class CheckingPolicyTests(unittest.TestCase):
    def test_analysis_is_not_user_homework(self):
        for step in ('Read the datasheet and identify the connected pins.',
                     'Check the linked part information and decide whether this is a problem.',
                     'Compare the parts list with the PCB.',
                     'Follow the two highlighted tracks to U42.',
                     'Check the signal on a real board.'):
            self.assertTrue(delegated_check(step),step)
        for step in ('Connect U42 pin 1 to GND.',
                     'Choose filled and capped vias when ordering.',
                     'Save the project, then update the review.',
                     'Should another device start each sensor reading?',
                     'Review the proposed edit before approving it.'):
            self.assertFalse(delegated_check(step),step)

    def test_pending_analysis_never_becomes_a_pass_or_a_guessed_edit(self):
        action={'disposition':'change','reason':'Gap exceeds the configured limit.',
                'steps':['Check the gap against the manufacturer limit.','If it fails, move the track.']}
        separate_analysis(action)
        self.assertEqual(action['steps'],[])
        self.assertEqual(action['disposition'],'investigate')
        self.assertEqual(len(action['pending_analysis']),2)
        self.assertIn('not completed',action['analysis_limit'])
        separate_analysis(action)
        self.assertEqual(len(action['pending_analysis']),2)
        self.assertEqual(action['reason'],'Gap exceeds the configured limit.')

    def test_shared_review_and_chat_instructions(self):
        for prompt in (REVIEW_INSTRUCTIONS,CHAT_STYLE,PIN_INSTRUCTIONS):self.assertIn(INSTRUCTIONS,prompt)

if __name__=='__main__':unittest.main()

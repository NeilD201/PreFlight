"""Do not convert internal default-rule gaps into unsupported footprint edits."""
import copy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.reviewer import evidence_packet
from pcb_checker.design_review import current_plan, local_plan
from pcb_checker.footprint_context import footprint_rule_context
from pcb_checker.models import Component, ComponentRecord, Issue, Pad, Project
from pcb_checker.review import build_review
from pcb_checker.review_sections import organize_review
from pcb_checker.step_help import answer_step, _packet
from pcb_checker.workflow import keyed_groups, compare_runs


def example(code='CLEARANCE'):
    p = Project('test', components={'U1': Component('U1', pcb=[ComponentRecord(
        'U1', 'IC', 'Test:QFN', 'test.kicad_pcb', 1,
        pads=[Pad('1', 'GND', 1), Pad('2', 'VDD', 2)])])})
    p.issues = [Issue('error', 'KICAD_DRC_'+code,
        "Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.1500 mm)"
        if code == 'CLEARANCE' else 'Front solder mask aperture bridges items with different nets',
        source='kicad_drc', items=[{'description':'Pad 1 [GND] of U1 on F.Cu'},
                                 {'description':'Pad 2 [VDD] of U1 on F.Cu'}])]
    p.review = build_review(p)
    return p


class FootprintContextTests(unittest.TestCase):
    def test_default_gap_and_mask_are_notes_not_edit_tasks(self):
        for code in ('CLEARANCE', 'SOLDER_MASK_BRIDGE'):
            p = example(code); raw=asdict(p)['issues']; plan=local_plan(p); action=plan['actions'][0]
            self.assertTrue(action['reference_only'])
            self.assertEqual(action['steps'], [])
            self.assertEqual(action['consequence']['certainty'], 'unknown')
            self.assertIn('not been verified', action['reason'])
            data=asdict(p);data['action_plan']=plan
            self.assertEqual(organize_review(data)['assignments']['R001']['section'], 'notes')
            self.assertEqual(p.review['groups'][0]['priority'], 'note')
            self.assertEqual(asdict(p)['issues'], raw)

    def test_tracks_other_footprints_unknown_pads_and_mixed_groups_stay_visible(self):
        for description in ('Track [VDD] on F.Cu', 'Pad 2 [VDD] of U2 on F.Cu',
                            'Pad 3 [VDD] of U1 on F.Cu', 'Pad 2 [OTHER] of U1 on F.Cu'):
            p=example();p.issues[0].items[1]['description']=description
            self.assertIsNone(footprint_rule_context(p.review['groups'][0], asdict(p)))
        p=example();p.components['U1'].pcb.append(copy.deepcopy(p.components['U1'].pcb[0]))
        self.assertIsNone(footprint_rule_context(p.review['groups'][0], asdict(p)))
        p=example();p.issues.append(Issue('error','KICAD_DRC_SHORTING_ITEMS','Short'))
        p.review['groups'][0]['issue_ids'].append('F0002')
        self.assertIsNone(footprint_rule_context(p.review['groups'][0], asdict(p)))

    def test_zero_gaps_custom_and_board_minimum_rules_are_not_demoted(self):
        for message in ("Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.0000 mm)",
                        "Clearance violation (netclass 'Default' clearance 0.2000 mm; actual -0.1000 mm)",
                        "Clearance violation (rule 'Isolation' clearance 4.0000 mm; actual 0.1500 mm)",
                        "Clearance violation (board setup constraints clearance 0.2000 mm; actual 0.1500 mm)",
                        "Clearance violation (netclass 'HV' clearance 4.0000 mm; actual 0.1500 mm)"):
            p=example();p.issues[0].message=message
            self.assertIsNone(footprint_rule_context(p.review['groups'][0], asdict(p)))

    def test_shorts_stay_actionable_even_with_same_footprint_pads(self):
        p=example('SHORTING_ITEMS');action=local_plan(p)['actions'][0]
        self.assertFalse(action.get('reference_only'))
        self.assertEqual(action['disposition'], 'change')
        self.assertEqual(organize_review(asdict(p))['sections'][2]['attention_count'], 1)

    def test_saved_ai_pad_moving_advice_is_replaced_without_rewriting_history(self):
        p=example();p.review['groups'][0]['priority']='review'
        p.action_plan=local_plan(p);a=p.action_plan['actions'][0]
        a.update(basis='AI guidance from supplied evidence', steps=['Move the pads apart.'], disposition='change')
        before=asdict(p);current=current_plan(p)
        self.assertEqual(current['actions'][0]['steps'], [])
        self.assertEqual(asdict(p), before)
        self.assertEqual(keyed_groups(before)[0]['signature'],keyed_groups(asdict(p))[0]['signature'])
        self.assertEqual(compare_runs(before,asdict(p))['groups']['no_longer_reported'], [])

    def test_ai_gets_geometry_context_without_a_forced_fix_task(self):
        p=example();p.review['groups'][0]['priority']='review';packet=evidence_packet(p)
        self.assertEqual(packet['action_group_ids'], [])
        context=packet['review_groups'][0]['footprint_context']
        self.assertEqual(context['findings'][0]['gap_mm'], .15)
        self.assertEqual(context['findings'][0]['pads'][1]['reference'], 'U1')

    def test_chat_explains_the_same_interpretation_and_sends_it_to_ai(self):
        p=example();action=local_plan(p)['actions'][0];group=p.review['groups'][0]
        reply=answer_step(p,group,action,0,'Should I move these pads?',[],key='')
        self.assertIn('does not justify moving',reply['answer'])
        packet=_packet(p,group,{**action,'steps':['Explain this note']},0,'Why?',[])
        self.assertEqual(packet['footprint_context']['findings'][0]['gap_mm'], .15)


if __name__=='__main__':
    unittest.main()

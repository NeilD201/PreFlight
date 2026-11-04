"""Keep impact explanations scoped to the evidence, including historical runs."""
from dataclasses import asdict
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.consequences import consequence_for
from pcb_checker.design_review import local_plan, current_plan, compose_plan
from pcb_checker.models import Project, Issue
from pcb_checker.review import build_review
from pcb_checker.report import _review_html


def design(code, severity='warning', **kwargs):
    p = Project('impact', issues=[Issue(severity, code, 'Saved finding', **kwargs)])
    p.review = build_review(p)
    return p


class ConsequenceTests(unittest.TestCase):
    def test_rule_error_is_not_a_guaranteed_failed_board(self):
        for code, word in [('KICAD_DRC_CLEARANCE', 'manufacturer'),
                           ('KICAD_DRC_VIA_DIAMETER', 'manufacturer'),
                           ('KICAD_DRC_SOLDER_MASK_BRIDGE', 'Solder')]:
            p = design(code, 'error')
            impact = local_plan(p)['actions'][0]['consequence']
            self.assertEqual(impact['certainty'], 'possible')
            self.assertIn(word, impact['text'])
            self.assertNotIn('will fail', impact['text'])

    def test_aliases_and_cosmetic_marks_have_different_impacts(self):
        p = design('KICAD_ERC_MULTIPLE_NET_NAMES')
        alias = local_plan(p)['actions'][0]['consequence']
        self.assertEqual(alias['certainty'], 'no_fault_shown')
        self.assertIn('does not itself change the wiring', alias['text'])
        cosmetic = local_plan(design('KICAD_DRC_SILK_OVERLAP'))['actions'][0]['consequence']
        self.assertIn('polarity or pin-1', cosmetic['text'])
        self.assertNotIn('short', cosmetic['text'])

    def test_antenna_warning_is_conditional_and_ecg_geometry_is_inconclusive(self):
        p = design('RF_FEED_NO_TERMINAL')
        rf = local_plan(p)['actions'][0]['consequence']
        self.assertIn('If the radio path really has no antenna', rf['text'])
        ecg = local_plan(design('SENSITIVE_INPUT_ROUTE_REVIEW'))['actions'][0]['consequence']
        self.assertEqual(ecg['certainty'], 'unknown')
        self.assertIn('do not show', ecg['text'])

    def test_intention_and_exclusion_do_not_erase_short_risk(self):
        p = design('KICAD_DRC_SHORTING_ITEMS', 'error')
        impact = consequence_for(p.review['groups'][0], asdict(p), {'disposition': 'intentional'})
        self.assertIn('excess current', impact['text'])
        self.assertIn('does not confirm', impact['context_note'])
        # Even an old action incorrectly marked no_change must retain the saved short concern.
        impact = consequence_for(p.review['groups'][0], asdict(p), {'disposition': 'no_change'})
        self.assertEqual(impact['certainty'], 'possible')
        p = design('KICAD_DRC_SHORTING_ITEMS', 'error', excluded=True)
        excluded = local_plan(p)['actions'][0]['consequence']
        self.assertEqual(excluded['certainty'], 'unknown')
        self.assertIn('Excluding it does not change the circuit', excluded['text'])

    def test_pin_mode_and_missing_checks_remain_unknown(self):
        for code in ['KICAD_ERC_PIN_TO_PIN', 'KICAD_ERC_POWER_PIN_NOT_DRIVEN',
                     'CHECKS_NOT_IMPLEMENTED', 'NEW_UNKNOWN_RULE']:
            impact = local_plan(design(code))['actions'][0]['consequence']
            self.assertEqual(impact['certainty'], 'unknown')
        p = design('KICAD_ERC_PIN_TO_PIN')
        impact = consequence_for(p.review['groups'][0], asdict(p),
                                 {'pin_checks': [{'decision': 'keep'}]})
        self.assertEqual(impact['certainty'], 'no_fault_shown')

    def test_saved_reports_refresh_impact_without_running_ai(self):
        p = design('KICAD_DRC_CLEARANCE')
        p.action_plan = local_plan(p)
        p.action_plan['actions'][0].pop('consequence')
        refreshed = current_plan(p)
        self.assertIn('consequence', refreshed['actions'][0])
        self.assertNotIn('consequence', p.action_plan['actions'][0])

    def test_supported_circuit_rules_explain_specific_functions(self):
        for rule, expected in [('schematic:supply_ground', 'cannot receive'),
                               ('schematic:charge_resistor', 'charging stays disabled'),
                               ('schematic:sda_pullup', 'communication'),
                               ('schematic:vdd_capacitor', 'power demand changes')]:
            impact = consequence_for({'rule': rule}, {})
            self.assertEqual(impact['certainty'], 'possible')
            self.assertIn(expected, impact['text'])

    def test_ai_overlay_keeps_rule_impact_and_export_includes_it(self):
        p = design('KICAD_DRC_SHORTING_ITEMS', 'error')
        p.ai_review = {'status': 'completed', 'result': {
            'actions': [{'group_id': 'R001', 'reason': 'AI explanation', 'disposition': 'investigate'}],
            'device_description': 'Test', 'device_evidence_ids': [], 'assumptions': []}}
        p.action_plan = compose_plan(p)
        self.assertEqual(p.action_plan['actions'][0]['reason'], 'AI explanation')
        self.assertIn('excess current', p.action_plan['actions'][0]['consequence']['text'])
        html = _review_html(p)
        self.assertIn('If you leave it as it is', html)
        self.assertIn('excess current', html)


if __name__ == '__main__':
    unittest.main()

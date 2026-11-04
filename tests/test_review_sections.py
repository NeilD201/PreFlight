import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.review_sections import organize_review


class ReviewSectionsTests(unittest.TestCase):
    def review(self, code='', *, category='electrical', checks=None, rule='', priority='review', disposition='investigate'):
        return organize_review({'issues': [{'code': code}], 'review': {'groups': [
            {'id': 'R001', 'category': category, 'priority': priority, 'rule': rule, 'issue_ids': ['F0001']}]},
            'action_plan': {'actions': [{'group_id': 'R001', 'disposition': disposition, 'pin_checks': checks or []}]}})

    def section(self, **kwargs):
        return self.review(**kwargs)['assignments']['R001']['section']

    def test_raw_erc_does_not_become_functional_fault(self):
        for code in ('KICAD_ERC_MULTIPLE_NET_NAMES', 'KICAD_ERC_POWER_PIN_NOT_DRIVEN'):
            self.assertEqual(self.section(code=code), 'notes')
            # An AI edit disposition with no actual pin analysis is insufficient.
            self.assertEqual(self.section(code=code, disposition='change'), 'notes')

    def test_uninterpreted_connections_are_visible_without_claiming_a_fault(self):
        for code in ('KICAD_ERC_PIN_TO_PIN', 'KICAD_ERC_PIN_NOT_CONNECTED'):
            for disposition in ('investigate', 'change'):
                review = self.review(code=code, disposition=disposition)
                item = review['assignments']['R001']
                self.assertEqual(item['section'], 'connections')
                self.assertTrue(item['attention'])
                self.assertEqual(item['status'], 'context')
                self.assertEqual(review['sections'][0]['attention_count'], 1)
                self.assertEqual(review['pending_erc_ids'], ['R001'])

    def test_accepted_pin_analysis_and_library_notes_do_not_claim_review_required(self):
        item = self.review(code='KICAD_ERC_PIN_TO_PIN', checks=[{'decision':'keep'}])['assignments']['R001']
        self.assertFalse(item['attention'])
        self.assertEqual(item['status'], 'accepted')
        item = self.review(code='KICAD_ERC_LIB_SYMBOL_MISMATCH', category='documentation')['assignments']['R001']
        self.assertFalse(item['attention'])
        self.assertEqual(item['status'], 'reference')

    def test_pin_analysis_can_raise_functional_error_or_missing_mode(self):
        for decision in ('change', 'needs_context'):
            self.assertEqual(self.section(code='KICAD_ERC_PIN_TO_PIN', checks=[{'decision': decision}]), 'connections')
        self.assertEqual(self.section(code='KICAD_ERC_PIN_TO_PIN', checks=[{'decision': 'keep'}]), 'notes')

    def test_continuity_belongs_with_traces_and_physical_rules_with_board(self):
        for code in ('KICAD_DRC_SHORTING_ITEMS', 'KICAD_DRC_UNCONNECTED_ITEMS', 'SCHEMATIC_NET_SPLIT_ON_PCB'):
            self.assertEqual(self.section(code=code), 'traces')
        for code in ('KICAD_DRC_CLEARANCE', 'KICAD_DRC_COURTYARDS_OVERLAP'):
            self.assertEqual(self.section(code=code), 'placement')
        self.assertEqual(self.section(category='signal'), 'traces')

    def test_sourced_schematic_and_topology_checks_are_connections(self):
        for rule in ('schematic:sda_pullup', 'topology:input_output_joined', 'ai_schematic:example'):
            self.assertEqual(self.section(rule=rule), 'connections')

    def test_no_change_and_notes_stay_reachable_but_not_counted_as_attention(self):
        data = self.review(category='signal', priority='note', disposition='no_change')
        self.assertEqual(data['sections'][2]['group_ids'], ['R001'])
        self.assertEqual(data['sections'][2]['attention_count'], 0)
        self.assertEqual(self.section(code='KICAD_DRC_SHORTING_ITEMS', category='expected'), 'notes')


if __name__ == '__main__':
    unittest.main()

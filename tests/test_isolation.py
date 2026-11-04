"""Project isolation: two different boards never share context, questions, or evidence.

Uses the real Smartwatch KiCad project and a synthetic fixture board. No AI calls.
"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.analysis import analyze_project
from pcb_checker.ai.reviewer import (AIError, DESIGN_SCHEMA, allowed_evidence,
                                     evidence_packet, validate_review)
from pcb_checker.design_review import context_key, empty_context
from pcb_checker.product_context import project_questions
from pcb_checker.review import build_review
from pcb_checker.storage import WorkspaceStore
from test_design_review import design_result, with_doc
from test_review import fixture
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def smartwatch():
    return analyze_project(ROOT / 'Smartwatch', native=False)


def synthetic(name='synthetic'):
    p = fixture()
    p.archive = f'/tmp/{name}.kicad_pro'
    p.provenance = {'project_name': 'SharedName', 'source_hashes': {'a': 'b'}}
    p.selected_files = {'schematic': f'{name}.kicad_sch', 'pcb': f'{name}.kicad_pcb'}
    p.review = build_review(p)
    p.design_context = empty_context()
    return p


def own_refs(project):
    return {c if isinstance(c, str) else c.reference for c in project.components}


class ContextIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.watch = smartwatch()

    def test_different_projects_never_share_a_context_key(self):
        a = synthetic('board-a')
        b = synthetic('board-b')
        self.assertEqual(a.provenance['project_name'], b.provenance['project_name'])
        self.assertNotEqual(context_key(a), context_key(b))
        self.assertNotEqual(context_key(a), context_key(self.watch))

    def test_same_project_keeps_a_stable_key(self):
        self.assertEqual(context_key(self.watch), context_key(smartwatch()))

    def test_stored_context_does_not_leak_across_projects(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = WorkspaceStore(Path(tmp.name))
        a, b = synthetic('board-a'), synthetic('board-b')
        store.save_context(context_key(a), {'description': 'board A notes', 'revision': 1}, 0)
        self.assertIsNone(store.design_context(context_key(b)))
        self.assertEqual(store.design_context(context_key(a))['description'], 'board A notes')


class QuestionRelevanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.watch = smartwatch()

    def test_questions_reference_only_their_own_project(self):
        for project in (self.watch, synthetic('board-q')):
            refs = own_refs(project)
            # Alphanumeric pin designators (e.g. nRF52840 ball H23) match
            # the ref pattern but belong to their own component, not to a
            # foreign project: accept pin numbers of own components too.
            pin_numbers = {pin.number for comp in project.components.values()
                           for pin in (comp.schematic_pins or [])}
            questions = project_questions(project)['all_questions']
            self.assertTrue(questions, f'expected questions for {project.archive}')
            import re as _re
            for q in questions:
                tokens = [q.get('reference', ''), q.get('known', '')]
                for fact in q.get('evidence', []):
                    # Pin facts look like "U42 pin 8 (PROG) → net"; part
                    # blurbs look like "U10 · MAX17048G_T10". Only the text
                    # before · names parts; MPN suffixes must not count.
                    tokens.append(str(fact).split('·')[0])
                for token in tokens:
                    for word in _re.split(r'[\s.(),→]+', token or ''):
                        if _re.fullmatch(r'[A-Z]{1,2}\d{1,3}', word or ''):
                            self.assertIn(word, refs | pin_numbers, f"{word} is not on {project.archive}")

    def test_fixture_board_gets_no_smartwatch_questions(self):
        watch_refs = own_refs(self.watch)
        synth = synthetic('board-noleak')
        synth_refs = own_refs(synth)
        foreign = watch_refs - synth_refs
        self.assertTrue(foreign, 'test needs refs unique to the smartwatch')
        blob = str(project_questions(synth)['all_questions'])
        for ref in sorted(foreign):
            self.assertNotIn(f"'{ref}'", blob, f'smartwatch ref {ref} leaked into synthetic questions')


class EvidenceIsolationTests(unittest.TestCase):
    def test_foreign_evidence_ids_are_rejected(self):
        packet = evidence_packet(with_doc(synthetic('board-ev')))
        own = allowed_evidence(packet)
        self.assertIn('C:U42', own)
        self.assertNotIn('C:U8', own)
        value = design_result(packet)
        validate_review(value, own, schema=DESIGN_SCHEMA, packet=packet)
        bad = copy.deepcopy(value)
        bad['actions'][0]['evidence_ids'] = ['C:U8']
        with self.assertRaises(AIError):
            validate_review(bad, own, schema=DESIGN_SCHEMA, packet=packet)


if __name__ == '__main__':
    unittest.main()

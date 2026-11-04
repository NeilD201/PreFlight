"""Unit tests for the central Question Creator gate (ai/question_creator).

Deterministic only: no Meta calls, no fixtures. These pin the uncertainty
fallback contract — TYPE 1 delegates, TYPE 2 asks, UNKNOWN is preserved.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from ai import question_creator as qc


def index(**over):
    base = {'answers': {}, 'documented': [], 'documents': [],
            'components': {}, 'pins': {}, 'nets': set(), 'calc_missing': set()}
    base.update(over)
    return base


class GateTests(unittest.TestCase):
    def test_technical_delegates_and_is_never_asked(self):
        d = qc.decide('What is the regulator headroom at full load?',
                      origin_agent='circuit', index=index(), tracker=qc.DedupTracker())
        self.assertEqual(d['decision'], qc.DELEGATE)
        self.assertFalse(d['ask_user'])
        self.assertTrue(d['target_agent'])
        self.assertTrue(d['required_evidence'])

    def test_unclassifiable_garbage_is_unknown_not_asked(self):
        # A non-designer-directed source with unclassifiable text stays
        # UNKNOWN: preserved, never posed, never invented.
        d = qc.decide('Hmm ??? maybe stuff',
                      origin_agent='calculator', index=index(), tracker=qc.DedupTracker())
        self.assertEqual(d['decision'], qc.UNKNOWN)
        self.assertFalse(d['ask_user'])
        self.assertIn('Hmm', d['unresolved_information'])

    def test_duplicate_need_routes_once(self):
        tracker = qc.DedupTracker()
        first = qc.decide('What enclosure material is required around J1?',
                          origin_agent='origin', index=index(), tracker=tracker)
        second = qc.decide('What enclosure material is required around J1?',
                           origin_agent='origin', index=index(), tracker=tracker)
        self.assertEqual(first['decision'], qc.ASK_USER)
        self.assertEqual(second['target_agent'], 'deduplicated')
        self.assertEqual(second['id'], first['id'])

    def test_generic_product_question_without_anchor_is_unknown_not_asked(self):
        # The reported bug: "Who will use this product?" / "What will the
        # finished product's case be like?" must never become user questions.
        for need in ('Who will use this product?',
                     'What will the finished product\u2019s case be like?',
                     'What enclosure material is required?'):
            d = qc.decide(need, origin_agent='origin',
                          index=index(), tracker=qc.DedupTracker())
            self.assertEqual(d['decision'], qc.UNKNOWN, need)
            self.assertFalse(d['ask_user'])
            self.assertIn(need[:20], d['unresolved_information'])
        # ...but the same need anchored to hardware is still asked.
        d = qc.decide('What enclosure material is required around J1?',
                      origin_agent='origin', index=index(), tracker=qc.DedupTracker())
        self.assertEqual(d['decision'], qc.ASK_USER)
        self.assertTrue(d['ask_user'])

    def test_circular_delegation_terminates_unresolved(self):
        d = qc.decide('What is the regulator headroom at full load?',
                      origin_agent='circuit', index=index(),
                      tracker=qc.DedupTracker(), chain=('investigation',))
        self.assertEqual(d['decision'], qc.UNKNOWN)
        self.assertFalse(d['ask_user'])

    def test_depth_cap_terminates_unresolved(self):
        d = qc.decide('What is the regulator headroom at full load?',
                      origin_agent='circuit', index=index(),
                      tracker=qc.DedupTracker(), depth=qc.MAX_DEPTH + 1)
        self.assertEqual(d['decision'], qc.UNKNOWN)
        self.assertFalse(d['ask_user'])

    def test_mixed_asks_user_and_delegates_technical_half(self):
        d = qc.decide('What enclosure rating is needed given the U1 regulator thermal dissipation?',
                      origin_agent='origin', index=index(), tracker=qc.DedupTracker())
        self.assertEqual(d['classification'], 'mixed')
        self.assertEqual(d['decision'], qc.ASK_USER)
        self.assertTrue(d['ask_user'])
        self.assertEqual(d['technical_followup']['target_agent'], 'investigation')
        self.assertTrue(d['technical_followup']['required_evidence'])

    def test_beginner_implementation_question_is_preserved_not_asked(self):
        quoted = ('When you press a key or turn a knob, should this board pull '
                  'one shared wire to tap the main board?')
        anchored = ('When you press a key, should U1 pull the shared row wire low '
                    'to signal the main board at J4?')
        for need in (quoted, anchored):
            d = qc.decide(need, origin_agent='pin_check',
                          index=index(), tracker=qc.DedupTracker(),
                          context_hint='beginner_interview')
            self.assertEqual(d['decision'], qc.UNKNOWN, need)
            self.assertFalse(d['ask_user'])
            self.assertIn(need[:20], d['unresolved_information'])
        # The same anchored need IS asked to experts: selection changes.
        d = qc.decide(anchored, origin_agent='pin_check',
                      index=index(), tracker=qc.DedupTracker())
        self.assertEqual(d['decision'], qc.ASK_USER)

    def test_beginner_article_pose_hides_disguised_implementation(self):
        # The interview gate already blocks these, but finding articles
        # render pin_check missing_context verbatim. The pose annotation is
        # the selection record the article display must respect: beginners
        # never see implementation-framed questions, experts keep anchored
        # ones; a vague question attached to a pin check is anchored by that
        # check for experts, but stays hidden from beginners.
        from pcb_checker.design_review import annotate_question_pose
        quoted = ('When you press a key or turn a knob, should this board pull '
                  'one shared wire to tap the main board?')
        anchored = ('When you press a key, should U1 pull the shared row wire low '
                    'to signal the main board at J4?')
        behavior = 'What should happen when you turn the knob connected at J4?'
        plan = {'actions': [{'group_id': 'R007', 'components': ['SW7', 'U1'],
                 'pin_checks': [{'reference': 'SW7', 'pin': 'A', 'decision': 'needs_context',
                                 'missing_context': [quoted, anchored, behavior]}]}]}
        data = {'design_context': {'product_context': {'experience': 'first PCB, know nothing'}},
                'components': {}}
        out = annotate_question_pose(plan, data)
        pose = out['actions'][0]['pin_checks'][0]['missing_context_pose']
        self.assertFalse(pose[quoted]['ask_beginner'])
        self.assertTrue(pose[quoted]['ask_expert'])
        self.assertFalse(pose[anchored]['ask_beginner'])
        self.assertTrue(pose[anchored]['ask_expert'])
        self.assertTrue(pose[behavior]['ask_beginner'])
        self.assertTrue(pose[behavior]['ask_expert'])

    def test_group_pose_annotated_and_tracks_saved_answers(self):
        # Group-level missing_context gets the same selection record, and
        # pose recomputes on live context: a later saved answer that
        # resolves the need flips a posed question to hidden without any
        # code change. This is what the per-serve hook relies on.
        from pcb_checker.design_review import annotate_question_pose
        behavior = 'What should happen when you turn the knob connected at J4?'
        plan = {'actions': []}
        data = {'design_context': {'product_context': {'experience': 'first PCB, know nothing'}},
                'components': {},
                'review': {'groups': [{'id': 'R009', 'components': ['U1'],
                                       'missing_context': [behavior]}]}}
        annotate_question_pose(plan, data)
        pose = data['review']['groups'][0]['missing_context_pose']
        self.assertTrue(pose[behavior]['ask_beginner'])
        data['design_context']['question_answers'] = {
            'a': {'question': 'What should happen when you turn the knob connected at J4?',
                  'answer': 'It changes volume'}}
        annotate_question_pose(plan, data)
        pose = data['review']['groups'][0]['missing_context_pose']
        self.assertFalse(pose[behavior]['ask_beginner'])
        self.assertFalse(pose[behavior]['ask_expert'])

    def test_beginner_behavior_question_is_still_asked(self):
        d = qc.decide('What should happen when you turn the knob connected at J4?',
                      origin_agent='origin',
                      index=index(), tracker=qc.DedupTracker(),
                      context_hint='beginner_interview')
        self.assertEqual(d['decision'], qc.ASK_USER)
        self.assertTrue(d['ask_user'])

    def test_beginner_technical_need_still_delegates(self):
        d = qc.decide('What is the regulator headroom at full load?',
                      origin_agent='circuit',
                      index=index(), tracker=qc.DedupTracker(),
                      context_hint='beginner_interview')
        self.assertEqual(d['decision'], qc.DELEGATE)
        self.assertFalse(d['ask_user'])

    def test_technical_verification_is_delegated_never_asked(self):
        quoted = ('Confirm SW9 mounting tabs are mechanical only - '
                  'no wire change yet.')
        variants = ['Verify the U1 footprint pad numbers match the datasheet land pattern.',
                    'Check whether the J3 shield terminals connect to ground.',
                    'Confirm the U5 part identity before wiring its supply pins.']
        for need in [quoted] + variants:
            for origin in ('pin_check', 'ai_review', 'origin'):
                for hint in (None, 'beginner_interview'):
                    d = qc.decide(need, origin_agent=origin,
                                  index=index(), tracker=qc.DedupTracker(),
                                  context_hint=hint)
                    self.assertEqual(d['decision'], qc.DELEGATE, (need, origin, hint))
                    self.assertFalse(d['ask_user'])
                    self.assertEqual(d['target_agent'], 'investigation')
        # Genuine product intent with similar words is unaffected.
        d = qc.decide('What enclosure material is required around J1?',
                      origin_agent='origin',
                      index=index(), tracker=qc.DedupTracker())
        self.assertEqual(d['decision'], qc.ASK_USER)

    def test_unanchored_mixed_delegates_technical_half_only(self):
        d = qc.decide('What enclosure rating is needed given the regulator thermal dissipation?',
                      origin_agent='origin', index=index(), tracker=qc.DedupTracker())
        self.assertEqual(d['classification'], 'mixed')
        self.assertEqual(d['decision'], qc.DELEGATE)
        self.assertFalse(d['ask_user'])
        self.assertEqual(d['target_agent'], 'investigation')

    def test_origin_directed_unknown_is_asked_not_dropped(self):
        d = qc.decide('Does this finding conflict with your stated intent? (R001: Keep U1 as placed.)',
                      origin_agent='origin_reconcile', index=index(), tracker=qc.DedupTracker())
        self.assertEqual(d['decision'], qc.ASK_USER)
        self.assertIn('origin-directed', d['classification'])

    def test_delegation_task_maps_evidence_to_allowlisted_tool(self):
        def decision(evidence):
            return {'decision': qc.DELEGATE, 'id': 'qc-x', 'target_agent': 'investigation',
                    'required_evidence': evidence, 'origin_agent': 'circuit',
                    'need': 'What is the U1 regulator headroom?',
                    'scope': {'components': ['U1'], 'nets': []},
                    'reason': 'Technical uncertainty.'}
        task = qc.delegation_task(decision('read_datasheet on U1 pinout'))
        self.assertEqual((task['tool'], task['references']), ('read_datasheet', ['U1']))
        task = qc.delegation_task(decision('calculate_limits for the supply'))
        self.assertEqual(task['tool'], 'calculate_limits')
        task = qc.delegation_task(decision('trace_connections on the supply rails'))
        self.assertEqual(task['tool'], 'trace_connections')
        # No fitted references: not executable, never invented.
        no_ref = decision('read_datasheet')
        no_ref['need'] = 'What is the regulator headroom?'
        no_ref['scope'] = {'components': [], 'nets': []}
        self.assertIsNone(qc.delegation_task(no_ref))

    def test_delegation_task_refuses_non_delegations(self):
        base = {'id': 'qc-x', 'target_agent': 'investigation',
                'required_evidence': 'read_datasheet', 'need': 'What is U1?',
                'scope': {'components': ['U1'], 'nets': []}, 'reason': 'r'}
        self.assertIsNone(qc.delegation_task({**base, 'decision': qc.ASK_USER}))
        self.assertIsNone(qc.delegation_task({**base, 'decision': qc.UNKNOWN}))
        self.assertIsNone(qc.delegation_task({**base, 'decision': qc.DELEGATE, 'target_agent': 'packet'}))
        self.assertIsNone(qc.delegation_task({**base, 'decision': qc.DELEGATE, 'target_agent': 'deduplicated'}))
        self.assertIsNone(qc.delegation_task('not a decision'))
        # Delegation records without a decision field are executable.
        record = {k: v for k, v in base.items() if k != 'decision'}
        self.assertIsNotNone(qc.delegation_task(record))

    def test_part_mention_does_not_resolve_judgment(self):
        idx = index(components={'U1': {'pins': [{'net': 'GND'}]}})
        self.assertIsNone(qc.try_resolve(
            'Does this finding conflict with your stated intent? (R001: Keep U1 as placed.)', idx))
        # ...but a genuine connectivity question still resolves from the packet.
        fact, provenance = qc.try_resolve('Which net connects to U1 pin 1?', idx)
        self.assertIn('U1', fact)
        self.assertTrue(provenance)

    def test_unknown_answers_never_resolve(self):
        # Unknown phrasing is filtered when the evidence index is built, so a
        # "not sure yet" can never resolve its question; a real answer does.
        ctx = {'question_answers': {'a': {'question': 'What battery will you use?',
                                          'answer': 'Not sure yet'}}}
        self.assertIsNone(qc.try_resolve(
            'What battery will you use?',
            qc.build_evidence_index({}, ctx)))
        ctx2 = {'question_answers': {'a': {'question': 'What battery will you use?',
                                            'answer': 'A 3.7V LiPo'}}}
        self.assertIsNotNone(qc.try_resolve(
            'What battery will you use?',
            qc.build_evidence_index({}, ctx2)))


if __name__ == '__main__':
    unittest.main()

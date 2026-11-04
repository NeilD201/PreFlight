"""Exercise API payloads, grounded citations, malformed responses and secret handling."""

import copy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.ai.reviewer import (AIError, DEFAULT_MODEL, SCHEMA, _post, post_ai,
                                    allowed_evidence, delegation_fix, evidence_packet,
                                    group_rejection_messages, normalize_design_response,
                                    qualify_uncited_pin_claims,
                                    run_ai_review, validate_review, validated_design_items,
                                    unsourced_part_claim, vague_question, DESIGN_INSTRUCTIONS)
from pcb_checker.ai import ledger as ledger_mod
from pcb_checker.ai import reviewer as reviewer_mod
from pcb_checker.report import html_report
from pcb_checker.review import build_review
from pcb_checker.__main__ import main
from test_review import fixture
from test_components import schematic, symbol, board, footprint


RESULT = {"summary": "The charger layout needs review.", "answer": "Inspect EP ground copper.",
    "findings": [{"title": "EP connection", "impact": "electrical", "confidence": "medium",
        "interpretation": "Missing EP net assignment requires review; this does not prove malfunction.",
        "next_step": "Check the PCB thermal path.", "evidence_ids": ["R001", "C:U42"]}],
    "questions": ["What fabrication process will be used?"], "limitations": ["No thermal measurements."]}


class AITests(unittest.TestCase):
    def test_identical_findings_require_per_part_conclusions(self):
        self.assertIn("one part's conclusion does not cover the others", DESIGN_INSTRUCTIONS)
        self.assertIn('identical footprints', DESIGN_INSTRUCTIONS)

    def test_actions_propose_concrete_change_not_rederivation(self):
        self.assertIn('change X to Y', DESIGN_INSTRUCTIONS)
        self.assertIn('without further analysis', DESIGN_INSTRUCTIONS)
        self.assertIn('genuinely missing from the evidence', DESIGN_INSTRUCTIONS)

    def test_identical_pending_group_gets_resolved_sibling_template(self):
        from pcb_checker.ai.reviewer import identical_resolved_note
        packet = {'components': [
            {'reference': 'SW7', 'footprint': 'EC11', 'value': 'RotaryEncoder_Switch',
             'pads': [{'number': 'MP', 'net': ''}]},
            {'reference': 'SW8', 'footprint': 'EC11', 'value': 'RotaryEncoder_Switch',
             'pads': [{'number': 'MP', 'net': ''}]},
            {'reference': 'U1', 'footprint': 'QFN', 'value': 'MCP23017',
             'pads': [{'number': '1', 'net': 'GND'}]}]}
        groups = {
            'R001': {'id': 'R001', 'title': 'Footprint pads missing from the symbol', 'components': ['SW7']},
            'R002': {'id': 'R002', 'title': 'Footprint pads missing from the symbol: SW8', 'components': ['SW8']},
            'R003': {'id': 'R003', 'title': 'Other rule', 'components': ['U1']}}
        accepted = [{'group_id': 'R001', 'reason': 'SW7 legs are mechanical frame legs on footprint evidence.'}]
        note = identical_resolved_note(packet, groups, accepted, ['R002'])
        self.assertIn('R002', note)
        self.assertIn('R001', note)
        self.assertIn('SW7', note)
        self.assertIn('ITS OWN per-part pin evidence', note)
        self.assertNotIn('U1', note)
        # Non-identical pending groups and empty inputs yield nothing.
        self.assertEqual(identical_resolved_note(packet, groups, accepted, ['R003']), '')
        self.assertEqual(identical_resolved_note(packet, groups, [], ['R002']), '')
        self.assertEqual(identical_resolved_note(packet, groups, accepted, []), '')
    def setUp(self):
        self.project = fixture()
        self.project.review = build_review(self.project)

    @patch.dict('os.environ', {}, clear=True)
    def test_missing_key_never_calls_network_or_fakes_a_review(self):
        with patch('pcb_checker.ai.reviewer.post_ai') as post:
            result = run_ai_review(self.project)
        self.assertEqual(result['status'], 'not_configured')
        post.assert_not_called()

    def test_request_uses_structured_facts_and_response_schema(self):
        response = meta_response()
        with patch('pcb_checker.ai.reviewer.post_ai', return_value=response) as post:
            result = run_ai_review(self.project, key='secret-for-test', question='What should I fix?')
        payload, key = post.call_args.args
        self.assertEqual(key, 'secret-for-test')
        self.assertFalse(payload['store'])
        self.assertEqual(payload['response_format']['mime_type'], 'application/json')
        self.assertEqual(payload['generation_config']['thinking_level'], 'low')
        self.assertNotIn('secret-for-test', json.dumps(payload))
        packet = json.loads(payload['input'])
        self.assertEqual(packet['question'], 'What should I fix?')
        self.assertNotIn('native_reports', packet)
        self.assertNotIn('archive', packet)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['input_tokens'], 20)
        self.assertEqual(len(result['evidence_sha256']), 64)

    def test_fake_citations_and_invalid_shapes_are_rejected(self):
        allowed = allowed_evidence(evidence_packet(self.project))
        for change in ['citation', 'no_evidence', 'confidence', 'extra', 'wrong_type']:
            result = copy.deepcopy(RESULT)
            if change == 'citation': result['findings'][0]['evidence_ids'] = ['C:U_DOES_NOT_EXIST']
            if change == 'no_evidence': result['findings'][0]['evidence_ids'] = []
            if change == 'confidence': result['findings'][0]['confidence'] = 'certain'
            if change == 'extra': result['fabrication_approved'] = True
            if change == 'wrong_type': result['questions'] = 'none'
            with self.subTest(change=change), self.assertRaises(AIError):
                validate_review(result, allowed)

    def test_incomplete_refused_and_broken_responses_do_not_pass(self):
        refusal = {'status':'completed','steps':[{'type':'model_output','content':[{'type':'refusal'}]}]}
        bad_json = {'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'not json'}]}]}
        cases = [{'status':'incomplete'}, refusal, bad_json,
                 {'status':'completed','steps':None}, {'status':'completed','steps':[]}]
        for response in cases:
            with self.subTest(response=response), patch('pcb_checker.ai.reviewer.post_ai', return_value=response):
                result = run_ai_review(self.project, key='secret-for-test')
                self.assertEqual(result['status'], 'failed')
                self.assertNotIn('result', result)

    def cached_review(self, key='cache-test-key'):
        done = {'status': 'completed',
                'result': {'actions': [], 'summary': 'ok', 'answer': '',
                           'findings': [], 'questions': [], 'limitations': []},
                'rejected_items': [], 'pipeline': {'status': 'completed'},
                'circuit_audit': {'status': 'completed'}}
        return patch.object(reviewer_mod, '_run_ai_review', return_value=copy.deepcopy(done))

    def test_identical_evidence_reuses_validated_review(self):
        reviewer_mod._REVIEW_CACHE.clear()
        self.project.design_context['description'] = 'Cache probe board.'
        with self.cached_review() as run:
            first = run_ai_review(self.project, key='cache-test-key', design=True, deep=True)
            second = run_ai_review(self.project, key='cache-test-key', design=True, deep=True)
        self.assertEqual(run.call_count, 1, 'Identical evidence must not spend a second request')
        self.assertTrue(second.get('reused'))
        self.assertNotIn('cache-test-key', ' '.join(reviewer_mod._REVIEW_CACHE.keys()))
        reviewer_mod._REVIEW_CACHE.clear()

    def test_changed_description_or_key_invalidates_cache(self):
        reviewer_mod._REVIEW_CACHE.clear()
        self.project.design_context['description'] = 'Cache probe board v1.'
        with self.cached_review() as run:
            run_ai_review(self.project, key='cache-test-key', design=True, deep=True)
            self.project.design_context['description'] = 'Cache probe board v2.'
            run_ai_review(self.project, key='cache-test-key', design=True, deep=True)
            run_ai_review(self.project, key='different-cache-key', design=True, deep=True)
        self.assertEqual(run.call_count, 3, 'Changed context or key must change the effective request')
        reviewer_mod._REVIEW_CACHE.clear()

    def test_transport_errors_do_not_disclose_server_body_or_key(self):
        from pcb_checker.ai.meta import ENDPOINT
        for error in [HTTPError(ENDPOINT,401,'secret-for-test',{},None), URLError('secret-for-test')]:
            with patch('pcb_checker.ai.reviewer.build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(AIError) as caught:
                    post_ai({'model': DEFAULT_MODEL}, 'secret-for-test')
                self.assertNotIn('secret-for-test', str(caught.exception))

    def test_large_evidence_is_sent_without_truncation(self):
        context = 'Product operating context. ' * 15000 + 'END_OF_CONTEXT'
        self.project.design_context['description'] = context
        response = meta_response()
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=response) as post:
            result = run_ai_review(self.project, key='secret-for-test')
        post.assert_called_once()
        encoded = post.call_args.args[0]['input']
        self.assertGreater(len(encoded.encode()),300_000)
        self.assertEqual(json.loads(encoded)['design_context']['description'], context)
        self.assertEqual(result['status'], 'completed')

    def test_truncated_evidence_retries_once_with_scoped_packet(self):
        truncated = AIError('Meta stopped early.', diagnostics={'error_kind': 'truncated'})
        with patch('pcb_checker.ai.reviewer.post_ai', side_effect=[truncated, meta_response()]) as post:
            result = run_ai_review(self.project, key='secret-for-test')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(post.call_count, 2)
        first = json.loads(post.call_args_list[0].args[0]['input'])
        second = json.loads(post.call_args_list[1].args[0]['input'])
        self.assertNotIn('memory_review', second)
        for group in second.get('review_groups', []):
            self.assertLessEqual(len(group.get('evidence', [])), 1)
        caps = [(c.args[0].get('generation_config') or {}).get('max_output_tokens') for c in post.call_args_list]
        self.assertEqual(caps, [80000, 80000])

    def test_http400_ceiling_rejection_retries_once_at_half_cap(self):
        rejected = AIError('The AI service rejected the request (HTTP 400). Meta reported: maximum max_output_tokens 32000.',
                           diagnostics={'http_status': 400})
        with patch('pcb_checker.ai.reviewer.post_ai', side_effect=[rejected, meta_response()]) as post:
            result = run_ai_review(self.project, key='secret-for-test')
        self.assertEqual(result['status'], 'completed')
        caps = [(c.args[0].get('generation_config') or {}).get('max_output_tokens') for c in post.call_args_list]
        self.assertEqual(caps, [80000, 40000])

    def test_ai_html_is_escaped_and_links_to_evidence(self):
        result = copy.deepcopy(RESULT)
        result['findings'][0]['title'] = '<img src=x onerror=alert(1)>'
        self.project.ai_review = {'status': 'completed', 'model': 'test-model', 'result': result}
        html = html_report(self.project)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;img', html)
        self.assertIn('href="#R001"', html)
        self.assertIn('href="#component-U42"', html)
        self.assertIn('AI interpretation', html)

    def test_cli_missing_key_returns_local_results_and_explicit_failure(self):
        with patch('pcb_checker.local_env.load_local_env', lambda: None), \
                tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p/'test.kicad_sch').write_text(schematic(symbol()))
            (p/'test.kicad_pcb').write_text(board(footprint()))
            stdout = io.StringIO()
            with patch.dict('os.environ', {}, clear=True), patch('sys.stdin.isatty', return_value=False), \
                 patch('sys.argv', ['main.py', td, '--skip-kicad', '--no-sourcing', '--ai', '--json', '--no-save']), \
                 redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = main()
            data = json.loads(stdout.getvalue())
            self.assertEqual(code, 3)
            self.assertIn('C1', data['components'])
            self.assertEqual(data['ai_review']['status'], 'not_configured')

    def test_cli_no_ai_overrides_configured_key(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p/'test.kicad_sch').write_text(schematic(symbol()))
            (p/'test.kicad_pcb').write_text(board(footprint()))
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'secret-for-test', 'LLAMA_API_KEY': 'secret-for-test'}, clear=True), \
                 patch('sys.argv', ['main.py', td, '--skip-kicad', '--no-sourcing', '--no-ai', '--json', '--no-save']), \
                 patch('pcb_checker.ai.reviewer.post_ai') as post, \
                 redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 0)
            post.assert_not_called()
            self.assertNotIn('secret-for-test', stdout.getvalue())


def meta_response(result=None):
    return {'status': 'completed', 'steps': [
        {'type': 'thought', 'summary': [{'type': 'text', 'text': 'Excluded thinking text'}]},
        {'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps(RESULT if result is None else result)}]}],
        'usage': {'total_input_tokens': 20, 'total_output_tokens': 10, 'total_thought_tokens': 5, 'total_tokens': 35}}


def meta_api_response(result=None):
    return {'choices': [{'message': {'content': json.dumps(RESULT if result is None else result)},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}}


def meta_design_or_plan(payload, *args, **kwargs):
    packet=json.loads(payload['input'])
    if 'tools' in packet:
        refs=[c['reference'] for c in packet['components'] if not c.get('dnp')][:1]
        return meta_response({'product_hypothesis':'Test circuit', 'tasks':[
            {'tool':'trace_connections','references':refs,'question':'Where does this part connect?',
             'reason':'Establish saved connections.'}] if refs else []})
    return meta_response(__import__('test_design_review').design_result(packet))


@patch.dict('os.environ', {}, clear=True)
class VagueQuestionTests(unittest.TestCase):
    def test_bad_example_names_no_component(self):
        reason = vague_question(
            'Check whether the capacitor connected to VBG has the correct value.',
            ['C6'], False)
        self.assertIn('names no component', reason)

    def test_requirement_claim_needs_citation_or_admission(self):
        reason = vague_question(
            'C6 may have the wrong value for the VBG rail; confirm the correct value.',
            ['C6'], False)
        self.assertIn('without citing', reason)

    def test_good_question_with_citation_passes(self):
        self.assertEqual(vague_question(
            'The datasheet (D3, page 8) specifies a 1.0 uF X7R capacitor between VBG and GND. '
            'The schematic uses C6 (currently 100 nF). Confirm C6 dielectric and fit the specified part.',
            ['C6'], True), '')

    def test_admitted_gap_without_citation_passes(self):
        self.assertEqual(vague_question(
            'C6 pin 2 connects to VBG. The excerpt does not state the required dielectric, '
            'so the correct value cannot be verified from the evidence.',
            ['C6'], False), '')

    def test_intent_question_is_out_of_scope(self):
        self.assertEqual(vague_question(
            'What should turning the encoder do when the user rotates it?',
            [], False), '')

    def test_firmware_use_question_names_no_ref(self):
        # 'This pin' refers to the analyzed pin shown alongside the question;
        # firmware behavior is user knowledge, not a datasheet lookup.
        self.assertEqual(vague_question(
            'Will firmware use this pin as an input?',
            ['U1'], False), '')

    def test_unsourced_candidate_prescription_is_rejected(self):
        docs = {'D:C1:p1:0': {'mpn': 'SRV05-4', 'candidate': True, 'text': 'SRV05-4 protects USB.'},
                'D:C2:p1:0': {'mpn': 'LD1117V', 'text': 'TAB is connected to VOUT.'}}
        action = {'citations': []}
        reason = unsourced_part_claim(['Fit SRV05-4 across D+ and D-.'], action, docs)
        self.assertIn('SRV05-4', reason)
        self.assertIn('D:C1:p1:0', reason)
        # Citing the candidate excerpt passes.
        action = {'citations': [{'evidence_id': 'D:C1:p1:0', 'quote': 'SRV05-4 protects USB.'}]}
        self.assertEqual(unsourced_part_claim(['Fit SRV05-4 across D+ and D-.'], action, docs), '')
        # Fitted-part datasheets, packages and board references never trigger it.
        action = {'citations': []}
        self.assertEqual(unsourced_part_claim(
            ['Confirm from the LD1117 SOT-223 package drawing what U3 pad 4 connects to.',
             'Change R2 from 10k to 5.1k.'], action, docs), '')

    def test_one_candidate_citation_grounds_shared_mpn(self):
        # Observed failure (live probe 2026-11-02): search_parts returns
        # several excerpts for the same candidate MPN; the model cited one
        # with a verbatim quote, but validation demanded every excerpt, so
        # the repair loop chased citations across calls until the review
        # failed. Citing one candidate excerpt grounds the prescription; the
        # per-citation verbatim check still validates the quote itself.
        docs = {'D:C2836319:p3:0': {'mpn': 'SRV05-4', 'candidate': True,
                                    'text': 'Absolute Maximum rating Ppk 60 W.'},
                'D:C2836319:p4:0': {'mpn': 'SRV05-4', 'candidate': True,
                                    'text': 'VRWM Peak Reverse Working Voltage 5 V.'}}
        action = {'citations': [{'evidence_id': 'D:C2836319:p4:0',
                                 'quote': 'VRWM Peak Reverse Working Voltage 5 V.'}]}
        self.assertEqual(unsourced_part_claim(['Fit SRV05-4 across D+ and D-.'], action, docs), '')
        # Citing none of them still fails.
        self.assertIn('SRV05-4', unsourced_part_claim(
            ['Fit SRV05-4 across D+ and D-.'], {'citations': []}, docs))

    def test_anchored_package_confirmation_passes(self):
        # A confirmation of a directly observable fact (package variant) is
        # legitimate when anchored to an evidenced scope; the finding already
        # carries the reference, requirement, observations and citations.
        self.assertEqual(vague_question('Confirm the package.', ['U42'], True), '')
        # Unanchored confirmations and technical determinations still fail.
        self.assertIn('names no component', vague_question('Confirm the package.', [], True))
        self.assertIn('names no component', vague_question(
            'Check whether the capacitor connected to VBG has the correct value.',
            ['C6'], False))


class CorrectionTests(unittest.TestCase):
    def packet(self):
        return {'action_group_ids': ['R001'],
                'review_groups': [{'id': 'R001'}],
                'routing_chains': {}, 'components': [], 'nets': [],
                'question': '',
                'datasheet_excerpts': [{'evidence_id': 'D:U1', 'references': ['U1'],
                                        'text': 'The exposed pad must be connected to VSS.'}]}

    def test_question_answer_survives_finding_rejections(self):
        # Observed failure: a follow-up question ran the review path, one
        # finding was rejected by evidence checks, and the wipe meant for
        # discarded claims also erased the direct answer to the question.
        from test_design_review import design_result
        base_packet = self.packet()
        base_packet['components'] = [{'evidence_id': 'C:U42', 'reference': 'U42', 'dnp': False}]
        base_packet['design_context'] = {'intentions': []}
        value = design_result(base_packet)
        value['answer'] = 'Verify the keepout; tuning cannot be approved from copper alone.'
        extra = copy.deepcopy(value['actions'][0])
        extra['group_id'] = 'NOPE'
        value['actions'].append(extra)
        accepted, rejected = validated_design_items(value, base_packet, allow_empty=True)
        self.assertTrue(rejected, 'The unknown issue ID must be rejected.')
        self.assertEqual(accepted['answer'], '',
                         'Without a user question the answer stays wiped.')
        asked = {**base_packet, 'question': 'Is the antenna finding fixable?'}
        kept, _ = validated_design_items(value, asked, allow_empty=True)
        self.assertEqual(kept['answer'], value['answer'],
                         'A direct answer to the user question must survive finding rejections.')

    def test_cited_evidence_id_is_linked_not_rejected(self):
        # The observed failure: the model cites D:U1 with a valid quote but
        # omits it from evidence_ids. Intent is unambiguous, so normalize
        # links it instead of rejecting the whole finding.
        value = {'actions': [],
                 'circuit_findings': [{'reference': 'U1', 'evidence_ids': [],
                                       'citations': [{'evidence_id': 'D:U1',
                                                      'quote': 'The exposed pad must be connected to VSS.'}]}]}
        out = normalize_design_response(value, self.packet())
        self.assertIn('D:U1', out['circuit_findings'][0]['evidence_ids'])

    def test_action_citation_is_linked_the_same_way(self):
        value = {'actions': [{'group_id': 'R001', 'evidence_ids': ['R001'],
                              'citations': [{'evidence_id': 'D:U1',
                                             'quote': 'The exposed pad must be connected to VSS.'}]}],
                 'circuit_findings': []}
        out = normalize_design_response(value, self.packet())
        self.assertIn('D:U1', out['actions'][0]['evidence_ids'])

    def test_hallucinated_evidence_id_is_never_linked(self):
        value = {'actions': [],
                 'circuit_findings': [{'reference': 'U1', 'evidence_ids': [],
                                       'citations': [{'evidence_id': 'D:NOPE',
                                                      'quote': 'Something.'}]}]}
        out = normalize_design_response(value, self.packet())
        self.assertNotIn('D:NOPE', out['circuit_findings'][0]['evidence_ids'])

    def test_identical_correction_failures_report_once(self):
        # One timed-out batch stamped all four items; joining repeated the
        # same timeout four times. Grouped output states the cause once.
        timeout = ('AI schematic citation is missing from its evidence list. Correction: '
                   'Meta did not respond before the request timed out. Request failed after '
                   '1 attempt(s). Local checks were preserved.')
        rejected = [{'group_id': gid, 'message': timeout}
                    for gid in ('R001', 'R002', 'R003')]
        rejected.append({'finding_index': 0, 'message': timeout})
        text = group_rejection_messages(rejected)
        self.assertEqual(text.count('timed out'), 1)
        self.assertIn('R001', text)
        self.assertIn('Local checks were preserved', text)
        self.assertIn('retry the review', text)

    def test_distinct_failures_keep_attribution(self):
        rejected = [{'group_id': 'R001', 'message': 'R001: bad steps.'},
                    {'group_id': 'R002', 'message': 'R002: bad quote.'}]
        text = group_rejection_messages(rejected)
        self.assertIn('R001', text)
        self.assertIn('R002', text)
        self.assertIn('bad steps', text)

    def test_timed_out_correction_fails_cleanly_without_invented_results(self):
        # Faithful replay of the observed Macropad failure: the first pass
        # returns verify-yourself steps, the single batched correction times
        # out, and the review must fail with one clear message — never a
        # partially validated success, never four copies of one timeout.
        from test_design_review import design_result
        from pcb_checker.ai import reviewer as reviewer_mod
        project = fixture()
        project.review = build_review(project)
        calls = []

        def fake_post(payload, key, **kwargs):
            calls.append(json.loads(payload['input']))
            if len(calls) == 1:
                packet = calls[0]
                plan = design_result(packet)
                for action in plan['actions'][:2]:
                    action['steps'] = ['Confirm the U1 footprint matches the datasheet land pattern.']
                return meta_response(plan)
            raise AIError('Meta did not respond before the request timed out. '
                          'Request failed after 1 attempt(s). Local checks were preserved.')

        with patch.object(reviewer_mod, 'post_ai', side_effect=fake_post):
            result = run_ai_review(project, key='meta-test-secret', design=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['message'].count('timed out'), 1)
        self.assertNotIn('result', result)
        self.assertIn('retry the review', result['message'])

    def test_delegation_fix_only_fires_on_verify_yourself_steps(self):
        self.assertEqual(delegation_fix([{'message': 'R001: unknown evidence.'}]), '')
        self.assertEqual(delegation_fix([]), '')
        fix = delegation_fix([{'message': 'R001: the steps ask the user to do supplied technical analysis (datasheet, pinout, footprint, wiring).'}])
        self.assertIn('Rewrite those steps', fix)
        self.assertIn('Never emit steps beginning with', fix)
        self.assertNotIn('Confirm the U1', fix)


@patch.dict('os.environ', {}, clear=True)
class MetaTests(unittest.TestCase):
    def setUp(self):
        self.project = fixture()
        self.project.review = build_review(self.project)

    def test_detailed_synthesis_gets_bounded_300s_window(self):
        record = {'status': 'limited', 'steps': [], 'tasks': [],
                  'ledger': ledger_mod.finalize(ledger_mod.new_ledger({})),
                  'elapsed_seconds': 1.0, 'limitations': [], 'delegation_results': {}}
        packet = {'investigation': []}
        audit = {'status': 'completed', 'parts': [], 'unreviewed': [], 'target_count': 0,
                 'reviewed_count': 0, 'unresolved_count': 0, 'findings': [], 'usage_samples': [],
                 'evidence_retrieval': [], 'elapsed_seconds': 0.0}
        seen = {}
        def fake_review(*args, **kwargs):
            seen.update(kwargs)
            return {'status': 'completed', 'result': {}, 'usage': {}}
        with patch('pcb_checker.ai.investigation.investigate',
                   return_value=(packet, record, {})), \
             patch.object(reviewer_mod, '_run_issue_review', side_effect=fake_review), \
             patch('pcb_checker.ai.circuit_audit.run_circuit_audit', return_value=dict(audit)):
            reviewer_mod._run_ai_review(
                self.project, key='window-test-secret', model='meta/test-window',
                provider='meta', design=True, deep=True, question='How is the supply?')
            self.assertEqual(seen.get('timeout'), 300)
            reviewer_mod._run_ai_review(
                self.project, key='window-test-secret', model='meta/test-window',
                provider='meta', design=True, deep=False, question='How is the supply?')
            self.assertEqual(seen.get('timeout'), 180)

    def test_synthesis_timeout_preserves_investigation_pipeline(self):
        record = {'status': 'limited', 'steps': [], 'tasks': [],
                  'ledger': ledger_mod.finalize(ledger_mod.new_ledger({})),
                  'elapsed_seconds': 12.5, 'limitations': ['one slow call'],
                  'delegation_results': {}}
        packet = {'investigation': [{'question': 'Q?', 'answer': 'A.'}]}
        with patch('pcb_checker.ai.investigation.investigate',
                   return_value=(packet, record, {'input_tokens': 9})), \
             patch.object(reviewer_mod, '_run_issue_review',
                          side_effect=AIError('Meta did not respond before the request timed out.')):
            result = reviewer_mod._run_ai_review(
                self.project, key='synthesis-test-secret', model='meta/test-synthesis',
                provider='meta', design=True, deep=True, question='How is the supply?')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['investigation_preserved'])
        self.assertIn('retry to complete the synthesis', result['message'])
        self.assertIn('Meta did not respond', result['message'])
        self.assertNotIn('synthesis-test-secret', json.dumps(result))
        self.assertIs(result['pipeline'], record)
        self.assertEqual(result['usage'], {'input_tokens': 9})
        self.assertTrue(any('Interpret evidence' in s.get('title', '') for s in record['steps']))

    def test_default_request_and_validated_response(self):
        with patch('pcb_checker.ai.reviewer.post_ai', return_value=meta_response()) as post, \
             patch('pcb_checker.ai.reviewer._post') as legacy_transport:
            result = run_ai_review(self.project, key='meta-test-secret', question='Explain the charger.')
        payload, key = post.call_args.args
        self.assertEqual(key, 'meta-test-secret')
        self.assertEqual(payload['model'], DEFAULT_MODEL)
        self.assertFalse(payload['store'])
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['response_format'], {'type': 'text', 'mime_type': 'application/json', 'schema': SCHEMA})
        self.assertEqual(payload['generation_config']['thinking_level'], 'low')
        packet = json.loads(payload['input'])
        self.assertEqual(packet['question'], 'Explain the charger.')
        self.assertNotIn('archive', packet)
        self.assertNotIn('meta-test-secret', json.dumps(payload))
        self.assertNotIn('meta-test-secret', json.dumps(result))
        self.assertEqual(result['provider'], 'Meta')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result'], RESULT)
        self.assertEqual(result['usage'], {'input_tokens': 20, 'output_tokens': 10, 'thought_tokens': 5, 'total_tokens': 35})
        legacy_transport.assert_not_called()

    def test_transport_uses_key_header_fixed_endpoint_and_bounded_read(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps(meta_api_response()).encode()
            result = post_ai({'model': DEFAULT_MODEL}, 'meta-test-secret')
        from pcb_checker.ai.meta import ENDPOINT
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url, ENDPOINT)
        self.assertEqual(req.get_header('Authorization'), 'Bearer meta-test-secret')
        self.assertNotIn('meta-test-secret', req.full_url)
        self.assertNotIn(b'meta-test-secret', req.data)
        body = json.loads(req.data.decode())
        self.assertEqual(body['model'], 'muse-spark-1.3-contributor')
        self.assertTrue(body['messages'])
        self.assertGreater(opener.return_value.open.call_args.kwargs['timeout'], 179)
        self.assertLessEqual(opener.return_value.open.call_args.kwargs['timeout'], 180)
        self.assertEqual(response.read.call_args.args, (2_000_001,))
        self.assertEqual(result['status'], 'completed')
        handler = opener.call_args.args[0]
        self.assertIsNone(handler.redirect_request(req, None, 302, '', {}, 'https://example.com'))

    def test_meta_attempt_gets_full_deadline_despite_attempt_cap(self):
        # request_limits() passes attempt_timeout=90 for retry headroom, but
        # Meta chat-completions is non-streaming: no bytes flow until the full
        # generation completes, so a 90s socket cap kills slow generations
        # mid-flight. Every Meta attempt must keep the full deadline.
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps(meta_api_response()).encode()
            result = post_ai({'model': DEFAULT_MODEL}, 'meta-test-secret',
                             max_attempts=3, timeout=180, attempt_timeout=90)
        self.assertEqual(result['status'], 'completed')
        self.assertGreater(opener.return_value.open.call_args.kwargs['timeout'], 179)
        self.assertLessEqual(opener.return_value.open.call_args.kwargs['timeout'], 180)

    def test_meta_retries_transient_503_then_succeeds(self):
        outage = HTTPError('https://example.com', 503, 'temporary', {}, None)
        response = type('Response', (), {
            '__enter__': lambda self: self,
            '__exit__': lambda self, *args: None,
            'read': lambda self, limit: json.dumps(meta_api_response()).encode(),
        })()
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
            opener.return_value.open.side_effect = [outage, outage, response]
            result = post_ai({'model': DEFAULT_MODEL}, 'meta-test-secret')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(opener.return_value.open.call_count, 3)
        self.assertEqual(sleep.call_args_list[0].args, (5,))
        self.assertEqual(sleep.call_args_list[1].args, (15,))

    def test_meta_retries_explicit_short_rate_limit_reset(self):
        limited = HTTPError('https://example.com', 429, 'rate limited', {}, io.BytesIO(
            json.dumps({'error': {'status': 'RESOURCE_EXHAUSTED',
                                  'message': 'Rate limit exceeded. Please retry in 1s.'}}).encode()))
        response = type('Response', (), {
            '__enter__': lambda self: self,
            '__exit__': lambda self, *args: None,
            'read': lambda self, limit: json.dumps(meta_api_response()).encode(),
        })()
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
            opener.return_value.open.side_effect = [limited, response]
            result = post_ai({'model': DEFAULT_MODEL}, 'meta-test-secret')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args, (1,))

    def test_meta_reports_503_after_retry_budget(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
            opener.return_value.open.side_effect = [
                HTTPError('https://example.com', 503, 'temporary', {}, None),
                HTTPError('https://example.com', 503, 'temporary', {}, None),
                HTTPError('https://example.com', 503, 'temporary', {}, None),
            ]
            with self.assertRaises(AIError) as caught:
                post_ai({'model': DEFAULT_MODEL}, 'meta-test-secret')
        self.assertIn('after 3 attempts', str(caught.exception))
        self.assertEqual(sleep.call_count, 2)

    def test_incomplete_blocked_malformed_and_fabricated_citations_fail(self):
        wrong_citation = copy.deepcopy(RESULT)
        wrong_citation['findings'][0]['evidence_ids'] = ['R9999']
        cases = [meta_response(wrong_citation),
                 meta_response() | {'status': 'incomplete'},
                 {'status': 'failed'}, {'status': 'requires_action'},
                 {'status': 'completed', 'steps': None},
                 {'status': 'completed', 'steps': []},
                 {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'refusal'}]}]},
                 {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': 'invalid json'}]}]}]
        for response in cases:
            with self.subTest(response=response), patch('pcb_checker.ai.reviewer.post_ai', return_value=response):
                result = run_ai_review(self.project, key='meta-test-secret')
                self.assertEqual(result['status'], 'failed')
                self.assertNotIn('result', result)

    def test_quota_failure_is_sanitized_and_never_falls_back(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'other-secret'}), \
             patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer._post') as legacy_transport:
            opener.return_value.open.side_effect = HTTPError('https://example.com', 429, 'meta-test-secret', {}, None)
            result = run_ai_review(self.project, key='meta-test-secret')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('quota', result['message'])
        self.assertNotIn('meta-test-secret', json.dumps(result))
        opener.return_value.open.assert_called_once()
        legacy_transport.assert_not_called()

    def test_large_evidence_is_sent_to_meta_without_truncation(self):
        context = 'Product operating context. ' * 15000 + 'END_OF_CONTEXT'
        self.project.design_context['description'] = context
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=meta_response()) as post:
            result = run_ai_review(self.project, key='meta-test-secret')
        post.assert_called_once()
        encoded = post.call_args.args[0]['input']
        self.assertGreater(len(encoded.encode()),300_000)
        self.assertEqual(json.loads(encoded)['design_context']['description'], context)
        self.assertEqual(result['status'], 'completed')

    def test_explicit_key_beats_environment_and_unknown_provider_fails(self):
        with patch.dict('os.environ', {'LLAMA_API_KEY': 'env-secret', 'OPENAI_API_KEY': 'other-secret'}), \
             patch('pcb_checker.ai.reviewer.post_ai', return_value=meta_response()) as post:
            result = run_ai_review(self.project, key='explicit-secret')
        self.assertEqual(post.call_args.args[1], 'explicit-secret')
        self.assertEqual(post.call_args.args[0]['model'], DEFAULT_MODEL)
        self.assertEqual(result['model'], DEFAULT_MODEL)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(run_ai_review(self.project, provider='unknown')['status'], 'failed')

    def test_default_ignores_an_openai_key(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'other-secret'}), \
             patch('pcb_checker.ai.reviewer.post_ai') as post:
            result = run_ai_review(self.project)
        self.assertEqual(result['status'], 'not_configured')
        self.assertEqual(result['provider'], 'Meta')
        self.assertIn('LLAMA_API_KEY', result['message'])
        post.assert_not_called()

    def test_report_setup_matches_provider(self):
        self.assertIn('llama.com/docs', html_report(self.project))
        self.assertIn('LLAMA_API_KEY', html_report(self.project))
        self.assertNotIn('aistudio.google.com', html_report(self.project))
        self.assertNotIn('OPENAI_API_KEY', html_report(self.project))

    def test_cli_hidden_prompt_calls_meta_and_saves_no_key(self):
        with patch('pcb_checker.local_env.load_local_env', lambda: None), \
             tempfile.TemporaryDirectory() as td, \
             patch('pcb_checker.__main__.analyze_project', return_value=self.project), \
             patch('sys.stdin.isatty', return_value=True), \
             patch('pcb_checker.__main__.getpass.getpass', return_value='meta-test-secret') as prompt, \
             patch('pcb_checker.ai.reviewer.post_ai', side_effect=meta_design_or_plan), \
             patch('sys.argv', ['main.py', '/test', '--no-sourcing', '--ai', '--json', '--output-dir', td]), \
             redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(main(), 0)
            prompt.assert_called_once_with('Meta API key (hidden): ')
            self.assertIn('billed to your Meta account', stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())['ai_review']['status'], 'completed')
            for path in Path(td).rglob('*'):
                if path.is_file():
                    self.assertNotIn(b'meta-test-secret', path.read_bytes())
            self.assertNotIn('meta-test-secret', stdout.getvalue() + stderr.getvalue())

    def test_cli_meta_key_runs_automatically_but_openai_key_does_not(self):
        for key_env, should_call in [('LLAMA_API_KEY', True), ('OPENAI_API_KEY', False)]:
            with self.subTest(key_env=key_env), \
                 patch.dict('os.environ', {key_env: 'secret-for-test'}, clear=True), \
                 patch('pcb_checker.__main__.analyze_project', return_value=copy.deepcopy(self.project)), \
                 patch('pcb_checker.local_env.load_local_env', lambda: None), \
                 patch('pcb_checker.ai.reviewer.post_ai', side_effect=meta_design_or_plan) as post, \
                 patch('pcb_checker.ai.reviewer._post') as legacy_transport, \
                 patch('sys.argv', ['main.py', '/test', '--no-sourcing', '--json', '--no-save']), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 0)
                # Plan, challenge attempt, audit attempt, then interpret evidence. The mock
                # answers are not valid challenges, so consultation skips without a request.
                self.assertEqual(post.call_count, 4 * int(should_call))
                legacy_transport.assert_not_called()

    def test_cli_quota_failure_saves_local_report(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.dict('os.environ', {'LLAMA_API_KEY': 'meta-test-secret'}), \
             patch('pcb_checker.__main__.analyze_project', return_value=self.project), \
             patch('pcb_checker.ai.reviewer.post_ai', side_effect=AIError('Meta quota reached.')), \
             patch('sys.argv', ['main.py', '/test', '--no-sourcing', '--ai', '--json', '--output-dir', td]), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 3)
            report = next(Path(td).rglob('report.json'))
            saved = json.loads(report.read_text())
            self.assertIn('U42', saved['components'])
            self.assertEqual(saved['ai_review']['status'], 'failed')
            self.assertEqual(saved['ai_review']['provider'], 'Meta')
            self.assertTrue(report.with_name('report.html').exists())


class PinClaimQualifierTests(unittest.TestCase):
    def action(self, title, reason, citations=(), steps=()):
        return {'group_id': 'R001', 'title': title, 'reason': reason,
                'citations': list(citations), 'steps': list(steps)}

    def test_uncited_tab_pad_claim_gets_provenance_label(self):
        action = self.action(
            'Footprint pads missing from the symbol: LD1117V voltage regulator (U3)',
            'U3 (LD1117V in SOT-223) pad 4 has no symbol pin and no net.')
        qualify_uncited_pin_claims([action])
        self.assertIn('no manufacturer-datasheet page is cited', action['reason'])

    def test_uncited_pin_swap_claim_gets_provenance_label(self):
        action = self.action(
            'RP2040 microcontroller (U2): confirmed QSPI clock/select swap',
            'U2 pin 52 (QSPI_SCLK) is on net Q_SEL which reaches U5 pin 1.')
        qualify_uncited_pin_claims([action])
        self.assertIn('no manufacturer-datasheet page is cited', action['reason'])

    def test_datasheet_cited_finding_is_untouched(self):
        reason = 'USB D+ and D- have no ESD protection.'
        action = self.action('USB ESD', reason,
                             citations=[{'evidence_id': 'D:C2836319:p2:0', 'quote': 'Working voltage: 5V'}])
        qualify_uncited_pin_claims([action])
        self.assertEqual(action['reason'], reason)

    def test_pure_calculation_finding_is_untouched(self):
        reason = 'LED series resistance may starve the LED (R12, R13, R14).'
        action = self.action('LED dim', reason)
        qualify_uncited_pin_claims([action])
        self.assertEqual(action['reason'], reason)

    def test_label_is_applied_only_once(self):
        action = self.action('Pad claim', 'U3 pad 4 has no symbol pin.')
        qualify_uncited_pin_claims([action, action])
        self.assertEqual(action['reason'].count('no manufacturer-datasheet page is cited'), 1)


if __name__ == '__main__':
    unittest.main()

"""VECTOR consultation: question collection, validation, budget gates."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import consult, ledger, reviewer
from pcb_checker.ai.usage import request_budget
from test_circuit_audit import fixture


def challenged_ledger():
    book = ledger.new_ledger({})
    for cid in ('H001', 'H002'):
        ledger.add_claim(book, origin='scope', statement=f'Claim {cid}.',
                         scope={'components': ['U1'], 'nets': [], 'pins': []})
    ledger.record_event(book, 'challenge', 'weakened: x', actor='fault', claim_id='H002',
                        data={'missing_check': 'Second question?'})
    ledger.record_event(book, 'challenge', 'stands: y', actor='vector', claim_id='H001',
                        data={'missing_check': 'Wrong actor.'})
    ledger.record_event(book, 'challenge', 'stands: z', actor='fault', claim_id='H001',
                        data={'missing_check': 'First question?'})
    return book


def reply(cid, question, finding='answered', answer='Because.', cited=()):
    return {'claim_id': cid, 'question': question, 'finding': finding,
            'answer': answer, 'cited_ids': list(cited)}


class CollectionTests(unittest.TestCase):
    def test_pending_questions_follow_ledger_order_and_cap(self):
        book = challenged_ledger()
        self.assertEqual(consult.pending_questions(book),
                         [('H001', 'First question?'), ('H002', 'Second question?')])
        self.assertEqual(consult.pending_questions(book, limit=1), [('H001', 'First question?')])

    def test_empty_questions_and_events_ignored(self):
        book = ledger.new_ledger({})
        ledger.add_claim(book, origin='s', statement='s.', scope={'components': []})
        ledger.record_event(book, 'challenge', 'x', actor='fault', claim_id='H001', data={})
        ledger.record_event(book, 'challenge', 'x', actor='fault', claim_id='H001',
                            data={'missing_check': '  '})
        self.assertEqual(consult.pending_questions(book), [])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.packet = reviewer.evidence_packet(fixture())
        self.asked = [('H001', 'Why U1?')]

    def test_exact_question_set_required(self):
        with self.assertRaises(reviewer.AIError):
            consult.validate_replies({'consultations': [reply('H001', 'Other?')]},
                                     self.asked, self.packet)
        with self.assertRaises(reviewer.AIError):
            consult.validate_replies({'consultations': []}, self.asked, self.packet)

    def test_finding_answer_and_citations_checked(self):
        with self.assertRaises(reviewer.AIError):
            consult.validate_replies({'consultations': [reply('H001', 'Why U1?', 'maybe')]},
                                     self.asked, self.packet)
        with self.assertRaises(reviewer.AIError):
            consult.validate_replies({'consultations': [reply('H001', 'Why U1?', 'answered', '  ')]},
                                     self.asked, self.packet)
        with self.assertRaises(reviewer.AIError):
            consult.validate_replies({'consultations': [reply('H001', 'Why U1?', 'answered', 'x.', ['N:NOPE'])]},
                                     self.asked, self.packet)
        replies = consult.validate_replies(
            {'consultations': [reply('H001', 'Why U1?', 'partial', 'x.', ['C:U1'])]},
            self.asked, self.packet)
        self.assertEqual(replies[0]['finding'], 'partial')


class ConsultTests(unittest.TestCase):
    def setUp(self):
        self.packet = reviewer.evidence_packet(fixture())

    def test_success_records_answers_with_provenance(self):
        book = challenged_ledger()
        answer = {'consultations': [reply('H001', 'First question?', 'answered', 'Found it.', ['C:U1']),
                                    reply('H002', 'Second question?', 'unanswerable', 'No data.', [])]}
        with request_budget('detailed'), \
                patch.object(consult, '_ask', return_value=(answer, {'input_tokens': 3})) as ask:
            usage = consult.consult(self.packet, book, key='k', model='m', provider='m')
        ask.assert_called_once()
        self.assertEqual(usage, {'input_tokens': 3})
        by_id = {c['id']: c for c in book['claims']}
        self.assertEqual(by_id['H001']['consultations'][0]['finding'], 'answered')
        self.assertEqual(by_id['H002']['consultations'][0]['cited_ids'], [])
        self.assertEqual(by_id['H001']['status'], 'hypothesized')
        kinds = [e['kind'] for e in book['events']]
        self.assertIn('consultation', kinds)
        self.assertIn('consultation_completed', kinds)
        self.assertNotIn('secret', json.dumps(book))

    def test_no_questions_skips_without_request(self):
        book = ledger.new_ledger({})
        with patch.object(consult, '_ask') as ask:
            consult.consult(self.packet, book, key='k', model='m', provider='m')
        ask.assert_not_called()
        self.assertTrue(any(e['kind'] == 'consult_skipped' for e in book['events']))

    def test_no_budget_and_low_budget_skip(self):
        book = challenged_ledger()
        with patch.object(consult, '_ask') as ask:
            consult.consult(self.packet, book, key='k', model='m', provider='m')
        ask.assert_not_called()
        with request_budget('detailed') as budget, patch.object(consult, '_ask') as ask:
            budget.requests = 8
            consult.consult(self.packet, book, key='k', model='m', provider='m')
        ask.assert_not_called()
        self.assertTrue(any(e['kind'] == 'consult_skipped' for e in book['events']))

    def test_failure_records_redacted_unavailable(self):
        book = challenged_ledger()
        with request_budget('detailed'), \
                patch.object(consult, '_ask', side_effect=reviewer.AIError('bad secret-key')):
            consult.consult(self.packet, book, key='secret-key', model='m', provider='m')
        event = next(e for e in book['events'] if e['kind'] == 'consult_unavailable')
        self.assertNotIn('secret-key', event['detail'])

    def test_single_timeout_retries_once(self):
        book = challenged_ledger()
        native = {'choices': [{'message': {'content': '{"consultations":[]}'}, 'finish_reason': 'stop'}],
                  'usage': {'prompt_tokens': 5, 'completion_tokens': 5, 'total_tokens': 10}}
        response = type('Response', (), {
            '__enter__': lambda self: self,
            '__exit__': lambda self, *args: None,
            'read': lambda self, limit: json.dumps(native).encode(),
        })()
        with request_budget('detailed'), \
             patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [TimeoutError('slow'), response]
            value, _ = consult._ask(self.packet, book, [('H001', 'Q?')], key='k', model='meta/muse-spark-1.3-contributor',
                                    provider='meta', progress=None)
        self.assertEqual(value, {'consultations': []})
        self.assertEqual(opener.return_value.open.call_count, 2)

    def test_transports_parse(self):
        book = challenged_ledger()
        native = {'status': 'completed', 'usage': {},
                  'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': '{"consultations":[]}'}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai', return_value=native):
            value, _ = consult._ask(self.packet, book, [('H001', 'Q?')], key='k', model='m',
                                    provider='gemini', progress=None)
        self.assertEqual(value, {'consultations': []})
        native = {'status': 'incomplete', 'usage': {}}
        with patch('pcb_checker.ai.reviewer._post', return_value=native), \
                self.assertRaises(reviewer.AIError):
            consult._ask(self.packet, book, [('H001', 'Q?')], key='k', model='m',
                         provider='meta', progress=None)

    def test_context_lists_resolvable_evidence_ids(self):
        book = challenged_ledger()
        sent = consult._context(self.packet, book, [('H001', 'First question?')])[0]
        self.assertIn('C:U1', sent['evidence_ids'])
        self.assertIn('C:U1', consult.INSTRUCTIONS)
        self.assertIn('N:SDA', consult.INSTRUCTIONS)

    def test_unavailable_event_carries_failure_diagnostics(self):
        book = challenged_ledger()
        boom = reviewer.AIError('Meta did not finish.', diagnostics={'finish_reason': 'length'})
        with request_budget('detailed'), patch.object(consult, '_ask', side_effect=boom):
            consult.consult(self.packet, book, key='k', model='m', provider='meta')
        event = next(e for e in book['events'] if e['kind'] == 'consult_unavailable')
        self.assertEqual(event['data'], {'diagnostics': {'finish_reason': 'length'}})

    def test_completed_event_reports_consulted_of_eligible(self):
        book = challenged_ledger()
        answer = {'consultations': [reply('H001', 'First question?'), reply('H002', 'Second question?')]}
        with request_budget('detailed'), \
                patch.object(consult, '_ask', return_value=(answer, {})):
            consult.consult(self.packet, book, key='k', model='m', provider='meta')
        done = next(e for e in book['events'] if e['kind'] == 'consultation_completed')
        self.assertEqual(done['data']['eligible'], 2)
        self.assertEqual(consult.pending_questions(book, limit=None),
                         [('H001', 'First question?'), ('H002', 'Second question?')])


if __name__ == '__main__':
    unittest.main()

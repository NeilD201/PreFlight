"""Regressions for mixed reports, cancellation, and rejected AI corrections."""
import gzip
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.reviewer import (AIError, DESIGN_SCHEMA, allowed_evidence, evidence_packet,
    focused_packet, normalize_design_response, run_ai_review, validate_review)
from pcb_checker.models import Issue
from pcb_checker.webapp import AppState, create_server
from pcb_checker.review import build_review
from test_components import schematic, symbol, board, footprint
from test_design_review import project, with_doc, design_result


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'test.kicad_sch').write_text(schematic(symbol()))
        (self.source / 'test.kicad_pcb').write_text(board(footprint()))
        self.state = AppState(self.source, self.root / 'reports', key='test-only-key')
        self.start()
        self.finish()
        self.old_id = self.state.report_id
        self.old_project = self.state.project

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def start(self, **extra):
        self.state.start('analysis', {'path': str(self.source), 'native': False,
            'datasheets': False, **extra})

    def finish(self):
        self.state.worker.join(8)
        self.assertFalse(self.state.worker.is_alive())
        return self.state.snapshot()['job']

    def test_report_switch_waits_for_ai_and_saved_assets(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {'status': 'failed', 'message': 'Test service unavailable'}
        with patch('pcb_checker.webapp.run_ai_review', side_effect=delayed):
            self.start(ai=True, baseline_id=self.old_id)
            try:
                self.assertTrue(entered.wait(3))
                self.assertIs(self.state.project, self.old_project)
                self.assertEqual(self.state.report_id, self.old_id)
                self.assertEqual(self.state.project_data()['workspace']['report_id'], self.old_id)
            finally:
                release.set()
            self.assertEqual(self.finish()['status'], 'completed')
        self.assertNotEqual(self.state.report_id, self.old_id)
        self.assertTrue(self.state.report_path(self.state.report_id).exists())

    def test_stop_keeps_previous_report_and_next_run_can_start(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(5)
            kwargs['progress']('Checking returned evidence')
            return {'status': 'failed'}
        with patch('pcb_checker.webapp.run_ai_review', side_effect=delayed):
            self.start(ai=True)
            try:
                self.assertTrue(entered.wait(3))
                self.state.cancel({'job_id': self.state.job['id']})
                self.assertTrue(self.state.snapshot()['job']['cancel_requested'])
            finally:
                release.set()
            self.assertEqual(self.finish()['status'], 'cancelled')
        self.assertIs(self.state.project, self.old_project)
        self.assertEqual(self.state.report_id, self.old_id)
        self.start()
        self.assertEqual(self.finish()['status'], 'completed')

    def test_preview_cache_does_not_cache_credentials_or_project_api(self):
        path = self.state.reports / self.old_id / 'schematic.svg'
        svg = ('<svg>' + ' ' * 5000 + '</svg>').encode()
        path.write_bytes(svg)
        server = create_server(self.state, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = 'http://127.0.0.1:' + str(server.server_address[1])
        try:
            with urlopen(Request(base + '/reports/' + self.old_id + '/schematic.svg', headers={'Accept-Encoding': 'gzip'})) as r:
                self.assertEqual(gzip.decompress(r.read()), svg)
                self.assertIn('private', r.headers['Cache-Control'])
            for endpoint in ['/api/config', '/api/project']:
                with urlopen(base + endpoint) as r:
                    self.assertEqual(r.headers['Cache-Control'], 'no-store')
                    self.assertNotIn(b'test-only-key', r.read())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)


class CorrectionTests(unittest.TestCase):
    def test_redundant_group_link_repaired_without_accepting_unknown_sources(self):
        packet = evidence_packet(project())
        value = design_result(packet)
        value['actions'][0]['evidence_ids'] = ['C:U42']
        fixed = normalize_design_response(value, packet)
        validate_review(fixed, allowed_evidence(packet), schema=DESIGN_SCHEMA, packet=packet)
        self.assertEqual(value['actions'][0]['evidence_ids'], ['C:U42'])
        value['actions'][0]['evidence_ids'] = ['C:INVENTED']
        with self.assertRaises(AIError):
            validate_review(normalize_design_response(value, packet), allowed_evidence(packet), schema=DESIGN_SCHEMA, packet=packet)

    def test_quote_formatting_can_vary_but_requirements_cannot(self):
        p = with_doc(project())
        p.datasheets['documents']['C150772']['pages'][0]['text'] = 'The I²C pull-up must use the 3.3 V rail.'
        packet = evidence_packet(p)
        value = design_result(packet)
        value['actions'][0]['citations'][0]['quote'] = 'The I2C pull-up must use the 3.3 V rail.'
        validate_review(value, allowed_evidence(packet), schema=DESIGN_SCHEMA, packet=packet)
        value['actions'][0]['citations'][0]['quote'] = 'The I2C pull-up must use the 5 V rail.'
        with self.assertRaises(AIError):
            validate_review(value, allowed_evidence(packet), schema=DESIGN_SCHEMA, packet=packet)

    def test_focused_signal_path_keeps_matching_component_evidence(self):
        packet = evidence_packet(project())
        packet['components'].append({'reference': 'C33', 'evidence_id': 'C:C33', 'pins': []})
        packet['routing_chains'] = {'chains': [{'id': 'T:radio', 'kind': 'rf',
            'components': ['U42', 'C33'], 'routes': []}]}
        focused = focused_packet(packet, packet['action_group_ids'])
        self.assertIn('C:C33', allowed_evidence(focused))

    def test_corrections_cover_remaining_issues_instead_of_dropping_all_but_three(self):
        p = project()
        p.issues.extend(Issue('warning', 'UNKNOWN_' + str(i), 'Needs review') for i in range(5))
        p.review = build_review(p)
        calls = []
        def response(payload, *args, **kwargs):
            calls.append(payload)
            decoded = json.loads(payload['input'])
            packet = decoded.get('circuit_evidence', decoded)
            value = design_result(packet)
            if len(calls) == 1:
                for action in value['actions']:
                    action['disposition'] = 'intentional'
            return {'status': 'completed', 'steps': [{'type': 'model_output',
                'content': [{'type': 'text', 'text': json.dumps(value)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai', side_effect=response):
            result = run_ai_review(p, key='test-only-key', design=True)
        self.assertEqual(result['coverage']['validated_actions'], result['coverage']['requested_actions'])
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()

"""End-to-end Fix Design verification on the real faulty WenneBoard project.

Runs against a disposable copy (WENNEBOARD_DIR, default /tmp/wenne-check) and
asserts the original folder (WENNEBOARD_ORIGINAL) stays byte-identical.
Skips when the disposable copy is absent so other machines stay green.
"""
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.analysis import analyze_project
from pcb_checker.report import save_report
from pcb_checker.review import build_review


BOARD_DIR = os.environ.get('WENNEBOARD_DIR', '/tmp/wenne-check')
ORIGINAL_DIR = os.environ.get('WENNEBOARD_ORIGINAL',
                              '/Volumes/500GB HDD/WenneBoard/PCB_VON_MODULARE_TASTATUR/falsch')


def _hash_tree(root):
    digest = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and not path.name.startswith('~') and '.lck' not in path.name:
            digest[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


@unittest.skipUnless(os.path.isdir(BOARD_DIR), 'WenneBoard disposable copy absent')
class WenneboardFixDesignTests(unittest.TestCase):
    timings = {}

    @classmethod
    def setUpClass(cls):
        started = time.monotonic()
        cls.original_hashes = _hash_tree(ORIGINAL_DIR) if os.path.isdir(ORIGINAL_DIR) else None
        cls.project = analyze_project(BOARD_DIR)
        cls.review = build_review(cls.project)
        cls.groups = {g['id']: g for g in cls.review['groups']}
        cls.timings['analysis'] = round(time.monotonic() - started, 1)

    @classmethod
    def tearDownClass(cls):
        if cls.original_hashes is not None:
            assert _hash_tree(ORIGINAL_DIR) == cls.original_hashes, 'original board files changed'
        print('\nWenneBoard e2e timings (s):', json.dumps(cls.timings))

    def _time(self, name, started):
        self.timings[name] = round(time.monotonic() - started, 1)

    def _led_group(self):
        for group in self.groups.values():
            if group.get('rule') == 'led_series_resistance':
                return group
        self.fail('LED resistor finding missing')

    def test_1_led_resistors_grouped_with_actionable_outcome(self):
        started = time.monotonic()
        group = self._led_group()
        self.assertEqual(sorted(group['components']), ['R12', 'R13', 'R14'])
        self.assertEqual(group['outcome'], 'conditional')
        self.assertIn('R12', group['title'])
        self._time('led_group', started)

    def test_2_capacitor_findings_grouped_with_both_refs(self):
        started = time.monotonic()
        group = next(g for g in self.groups.values() if g.get('rule') == 'mlcc_package_plausibility')
        self.assertEqual(sorted(group['components']), ['C105', 'C106'])
        self.assertEqual(group['outcome'], 'conditional')
        self._time('mlcc_group', started)

    def _mismatch_board(self, temp):
        """Copy BOARD_DIR, corrupt one real component value, return (work, project, review, group)."""
        from pcb_checker.webapp import AppState  # noqa: F401
        work = Path(temp) / 'board'
        shutil.copytree(BOARD_DIR, work, ignore=shutil.ignore_patterns('*-backups', '*.bak', '*.lck', 'fp-info-cache', '*.prl'))
        probe = analyze_project(work)
        target = next(ref for ref in sorted(probe.components, key=str)
                      if len(probe.components[ref].schematic) == 1
                      and len(probe.components[ref].pcb) == 1
                      and probe.components[ref].schematic[0].value.strip()
                      and probe.components[ref].pcb[0].value.strip()
                      and not ref.startswith('#'))
        sch_file = Path(probe.components[target].schematic[0].source_file)
        if not sch_file.is_absolute():
            sch_file = work / sch_file
        text = sch_file.read_text()
        ref_marker = '(property "Reference" "%s"' % target
        at = text.find(ref_marker)
        self.assertNotEqual(at, -1, 'symbol for %s not found' % target)
        val_marker = '(property "Value" "'
        vat = text.find(val_marker, at)
        self.assertNotEqual(vat, -1, 'value for %s not found' % target)
        end = text.find('"', vat + len(val_marker))
        original = text[vat + len(val_marker):end]
        sch_file.write_text(text[:vat + len(val_marker)] + original + '_MISMATCH' + text[end:])
        project = analyze_project(work)
        review = build_review(project)
        mismatch = [i for i in project.issues if i.code == 'VALUE_MISMATCH' and i.component == target]
        self.assertTrue(mismatch, 'corrupting %s produced no VALUE_MISMATCH' % target)
        group = next((g for g in review['groups'] if g.get('outcome') == 'verified_correction'
                      and target in (g.get('components') or [])), None)
        self.assertIsNotNone(group, 'no verified value correction found for %s' % target)
        return work, project, review, group

    def _wait_file_fix(self, state, fix_id):
        for _ in range(600):
            current = state.file_fix(fix_id)
            if current['status'] != 'preparing':
                return current
            time.sleep(0.5)
        return state.file_fix(fix_id)

    def test_3_verified_value_repair_on_disposable_copy(self):
        from pcb_checker.webapp import AppState
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as temp:
            work, project, review, group = self._mismatch_board(temp)
            state = AppState(work, Path(temp) / 'reports')
            state.project = project
            state.report_id = 'mismatch'
            state.store.preference('input:mismatch', {'path': str(work)})
            record = state.prepare_file_fix({'report_id': 'mismatch', 'group_id': group['id']})
            current = self._wait_file_fix(state, record['id'])
            self.assertEqual(current['status'], 'ready', current)
            applied = state.apply_file_fix({'fix_id': record['id'], 'closed_in_kicad': True})
            self.assertEqual(applied['status'], 'applied', applied)
            state.close()
        self._time('value_repair', started)

    def test_4_no_split_same_cause_groups(self):
        from collections import Counter
        started = time.monotonic()
        keys = Counter((g.get('rule'), g.get('title', '').split('(')[0].strip()) for g in self.groups.values())
        dupes = {k: n for k, n in keys.items() if n > 1 and k[0]}
        self.assertEqual(dupes, {}, f'split same-cause groups remain: {dupes}')
        self._time('dedupe', started)

    def test_5_oversized_model_response_narrows_and_retries(self):
        from pcb_checker.ai.reviewer import AIError
        from pcb_checker import repair_design
        started = time.monotonic()
        calls = []

        def fake_post_ai(payload, key, **kwargs):
            calls.append(payload)
            if len(calls) == 1:
                raise AIError('Meta stopped early.', diagnostics={'error_kind': 'truncated'})
            return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [
                {'type': 'text', 'text': json.dumps({
                    'title': 'Fit a verified 100nF part at C105',
                    'diagnosis': '0402 cannot hold 100uF; the rail has no verified bulk capacitance.',
                    'changes': [{'op': 'set_value', 'target': 'C105', 'value': '100nF', 'why': 'Verified standard value.'}],
                    'assumptions': [], 'questions': [],
                    'validation': ['Re-run the checks.'], 'limitations': []})}]}]}

        group = next(g for g in self.groups.values() if g.get('rule') == 'mlcc_package_plausibility')
        with tempfile.TemporaryDirectory() as temp, \
                patch('pcb_checker.design_review.local_plan', return_value={'actions': []}), \
                patch.object(repair_design, 'post_ai', side_effect=fake_post_ai):
            result = repair_design.investigate(
                self.project, group, 'Fix C105', {}, Path(temp), 'key', 'meta/muse-spark-1.3-contributor')
        self.assertEqual(len(calls), 2)
        first = len(json.dumps(calls[0]['input']))
        second = len(json.dumps(calls[1]['input']))
        self.assertLess(second, first, 'retry packet is narrower')
        print('repair packet bytes: first=%d retry=%d output_schema=%s' % (first, second, 'REPAIR_SCHEMA_SMALL/1500tok'))
        self.assertEqual(result['changes'][0]['target'], 'C105')
        self._time('oversized_retry', started)

    def _small_proposal(self, title, targets):
        return {'title': title, 'diagnosis': 'Diagnosis for %s.' % title,
                'changes': [{'op': 'set_value', 'target': t, 'value': '680',
                             'why': 'Verified fix for %s.' % t} for t in targets],
                'assumptions': [], 'questions': [],
                'validation': ['Re-run the checks.'], 'limitations': []}

    def test_5b_double_truncation_splits_per_component(self):
        from pcb_checker.ai.reviewer import AIError
        from pcb_checker import repair_design
        started = time.monotonic()
        calls = []

        def fake_post_ai(payload, key, **kwargs):
            calls.append(payload)
            if len(calls) <= 2:
                raise AIError('Meta stopped early.', diagnostics={'error_kind': 'truncated'})
            refs = [['R12'], ['R13'], ['R14']][len(calls) - 3]
            return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [
                {'type': 'text', 'text': json.dumps(self._small_proposal('Fix %s' % '+'.join(refs), refs))}]}]}

        group = self._led_group()
        with tempfile.TemporaryDirectory() as temp, \
                patch('pcb_checker.design_review.local_plan', return_value={'actions': []}), \
                patch.object(repair_design, 'collect_datasheets',
                             return_value={'parts': [], 'documents': {}}), \
                patch.object(repair_design, 'post_ai', side_effect=fake_post_ai):
            result = repair_design.investigate(
                self.project, group, 'Fix the dim LEDs', {}, Path(temp), 'key', 'meta/muse-spark-1.3-contributor')
        self.assertEqual(len(calls), 5, 'expected full + stripped + 3 single-ref chunk calls')
        caps = [(c.get('generation_config') or {}).get('max_output_tokens') for c in calls]
        self.assertEqual(caps[0], 1500, 'first attempt keeps the tight cap')
        self.assertTrue(all(c == 4000 for c in caps[1:]), 'retry and split escalate the cap, validation still enforces shape')
        sizes = [len(json.dumps(c['input'])) for c in calls]
        self.assertLess(sizes[1], sizes[0], 'stripped retry must be smaller than the first packet')
        self.assertLess(sizes[2], sizes[0], 'chunk packet must be smaller than the first packet')
        targets = sorted(c['target'] for c in result['changes'])
        self.assertEqual(targets, ['R12', 'R13', 'R14'])
        self._time('split_retry', started)

    def test_5c_chunk_resample_then_exhaustion_message(self):
        from pcb_checker.ai.reviewer import AIError
        from pcb_checker import repair_design
        started = time.monotonic()

        # R12 chunk truncates once then succeeds; R13/R14 succeed at once.
        script = iter(['truncated', 'truncated', 'truncated', 'ok-R12', 'ok-R13', 'ok-R14'])
        calls = []

        def fake_post_ai(payload, key, **kwargs):
            calls.append(payload)
            outcome = next(script)
            if outcome == 'truncated':
                raise AIError('Meta stopped early.', diagnostics={'error_kind': 'truncated'})
            ref = outcome.split('-')[1]
            return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [
                {'type': 'text', 'text': json.dumps(self._small_proposal('Fix %s' % ref, [ref]))}]}]}

        group = self._led_group()
        with tempfile.TemporaryDirectory() as temp, \
                patch('pcb_checker.design_review.local_plan', return_value={'actions': []}), \
                patch.object(repair_design, 'collect_datasheets',
                             return_value={'parts': [], 'documents': {}}), \
                patch.object(repair_design, 'post_ai', side_effect=fake_post_ai):
            result = repair_design.investigate(
                self.project, group, 'Fix the dim LEDs', {}, Path(temp), 'key', 'meta/muse-spark-1.3-contributor')
        self.assertEqual(len(calls), 6, 'expected full + stripped + 3 chunks + 1 resample')
        targets = sorted(c['target'] for c in result['changes'])
        self.assertEqual(targets, ['R12', 'R13', 'R14'])

        # Every attempt truncates: the error must describe what was tried and the remedy.
        def always_truncated(payload, key, **kwargs):
            raise AIError('Meta stopped early.', diagnostics={'error_kind': 'truncated'})
        with tempfile.TemporaryDirectory() as temp, \
                patch('pcb_checker.design_review.local_plan', return_value={'actions': []}), \
                patch.object(repair_design, 'collect_datasheets',
                             return_value={'parts': [], 'documents': {}}), \
                patch.object(repair_design, 'post_ai', side_effect=always_truncated):
            with self.assertRaisesRegex(AIError, 'automatic attempts.*press Fix Design again'):
                repair_design.investigate(
                    self.project, group, 'Fix the dim LEDs', {}, Path(temp), 'key', 'meta/muse-spark-1.3-contributor')
        self._time('resample_exhaustion', started)

    def test_11_requested_value_change_applies_to_disposable_copy(self):
        from pcb_checker.webapp import AppState
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / 'board'
            shutil.copytree(BOARD_DIR, work, ignore=shutil.ignore_patterns('*-backups', '*.bak', '*.lck', 'fp-info-cache', '*.prl'))
            project = analyze_project(work)
            review = build_review(project)
            group = next(g for g in review['groups'] if g.get('rule') == 'led_series_resistance')
            before_codes = {}
            for issue in project.issues:
                before_codes[issue.code] = before_codes.get(issue.code, 0) + 1
            state = AppState(work, Path(temp) / 'reports')
            state.project = project
            state.report_id = 'review'
            state.store.preference('input:review', {'path': str(work)})
            record = state.prepare_file_fix({'report_id': 'review', 'group_id': group['id'],
                                             'edit_request': 'Change R12 from 10 kΩ to 680 Ω.'})
            current = self._wait_file_fix(state, record['id'])
            self.assertEqual(current['status'], 'ready', current)
            self.assertIn('Change R12 from', current.get('summary', ''))
            applied = state.apply_file_fix({'fix_id': record['id'], 'closed_in_kicad': True})
            self.assertEqual(applied['status'], 'applied', applied)
            state.close()
            after = analyze_project(work)
            r12 = after.components['R12']
            self.assertTrue(any('680' in (r.value or '') for r in r12.schematic), 'schematic value not updated')
            self.assertTrue(any('680' in (r.value or '') for r in r12.pcb), 'pcb value not updated')
            for issue in after.issues:
                if issue.code not in before_codes:
                    self.fail('repair introduced a new issue class: %s' % issue.code)
        self._time('requested_value_repair', started)

    def test_6_failed_meta_request_blocks_without_editing_files(self):
        from pcb_checker import repair_design
        from pcb_checker.webapp import AppState
        started = time.monotonic()
        before = _hash_tree(BOARD_DIR)
        state = AppState(BOARD_DIR, Path(tempfile.mkdtemp()) / 'reports')
        state.project = self.project
        state.report_id = 'review'
        state.meta_key = 'e2e-fixture-key'
        state.store.preference('input:review', {'path': str(BOARD_DIR)})
        group = self._led_group()
        with patch.object(repair_design, 'post_ai',
                          side_effect=RuntimeError('boom')), \
                patch.object(repair_design, 'collect_datasheets',
                          return_value={'parts': [], 'documents': {}}), \
                patch('pcb_checker.design_review.local_plan', return_value={'actions': []}):
            record = state.prepare_file_fix({'report_id': 'review', 'group_id': group['id'],
                                             'edit_request': 'Explain the LED current.',
                                             'investigate_design': True})
            for _ in range(600):
                current = state.file_fix(record['id'])
                if current['status'] != 'preparing':
                    break
                time.sleep(0.5)
        self.assertEqual(current['status'], 'blocked', current)
        self.assertIn('reference', current['message'])
        self.assertEqual(_hash_tree(BOARD_DIR), before, 'project files changed on failure')
        self._time('failed_meta', started)
        state.close()

    def test_7_reload_preserves_preparation(self):
        from pcb_checker.webapp import AppState
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as temp:
            work, project, review, group = self._mismatch_board(temp)
            reports = Path(temp) / 'reports'
            state = AppState(work, reports)
            state.project = project
            state.report_id = 'review'
            state.store.preference('input:review', {'path': str(work)})
            record = state.prepare_fix_design({'report_id': 'review', 'steps': [{'group_id': group['id']}]})
            state.fix_design_worker.join(timeout=600)
            self.assertEqual(state.fix_design(record['id'])['status'], 'validated',
                             state.fix_design(record['id']).get('message'))
            reopened = AppState(work, reports)
            same = reopened.fix_design(record['id'])
            self.assertEqual(same['status'], 'validated')
            self.assertEqual(same['group_ids'], [group['id']])
            self._time('reload', started)
            state.close()
            reopened.close()

    def test_8_repeated_prepare_returns_same_record(self):
        from pcb_checker.webapp import AppState
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as temp:
            work, project, review, group = self._mismatch_board(temp)
            state = AppState(work, Path(temp) / 'reports')
            state.project = project
            state.report_id = 'review'
            state.store.preference('input:review', {'path': str(work)})
            first = state.prepare_fix_design({'report_id': 'review', 'steps': [{'group_id': group['id']}]})
            state.fix_design_worker.join(timeout=600)
            self.assertEqual(state.fix_design(first['id'])['status'], 'validated',
                             state.fix_design(first['id']).get('message'))
            again = state.prepare_fix_design({'report_id': 'review', 'steps': [{'group_id': group['id']}]})
            self.assertEqual(again['id'], first['id'])
            self._time('idempotent_prepare', started)
            state.close()

    def test_9_saved_review_repair_preparation(self):
        from pcb_checker.webapp import restore_project, AppState
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as temp:
            work, project, review, group = self._mismatch_board(temp)
            out = save_report(project, temp)
            data = json.loads((out / 'report.json').read_text())
            restored = restore_project(data)
            state = AppState(work, Path(temp) / 'reports')
            state.project = restored
            state.report_id = 'saved-review'
            state.store.preference('input:saved-review', {'path': str(work)})
            saved_group = next(g for g in restored.review['groups'] if g['id'] == group['id'])
            record = state.prepare_fix_design({'report_id': 'saved-review',
                                               'steps': [{'group_id': saved_group['id']}]})
            state.fix_design_worker.join(timeout=600)
            self.assertEqual(state.fix_design(record['id'])['status'], 'validated',
                             state.fix_design(record['id']).get('message'))
            state.close()
        self._time('saved_review', started)

    def test_10_disposable_copy_export_validates(self):
        import zipfile
        from pcb_checker.webapp import AppState
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as temp:
            work, project, review, group = self._mismatch_board(temp)
            state = AppState(work, Path(temp) / 'reports')
            state.project = project
            state.report_id = 'review'
            state.store.preference('input:review', {'path': str(work)})
            record = state.prepare_fix_design({'report_id': 'review', 'steps': [{'group_id': group['id']}]})
            state.fix_design_worker.join(timeout=600)
            validated = state.fix_design(record['id'])
            self.assertEqual(validated['status'], 'validated', validated.get('message'))
            approved = state.approve_fix_design({'fix_id': record['id']})
            self.assertEqual(approved['status'], 'approved')
            blob, files = state.export_fix_design(record['id'])
            self.assertTrue(blob, 'empty export artifact')
            archive = Path(temp) / 'fix.zip'
            archive.write_bytes(blob)
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                pcb_names = [n for n in names if n.endswith('.kicad_pcb')]
                self.assertTrue(pcb_names, names)
                fixed_text = bundle.read(pcb_names[0]).decode('utf-8')
                self.assertIn('_MISMATCH', fixed_text, 'exported copy must carry the applied value edit')
            for pcb in Path(BOARD_DIR).glob('*.kicad_pcb'):
                self.assertNotIn('_MISMATCH', pcb.read_text(), 'disposable source changed by export')
            self._time('approve_export', started)
            state.close()


if __name__ == '__main__':
    unittest.main()

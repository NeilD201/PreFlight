"""Opt-in tests against a real project; mutate temporary copies only.

Set PCB_CHECKER_TEST_PROJECT to the extracted smartwatch project to run these.
The ordinary synthetic suite does not require KiCad or a private project.
"""

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.analysis import analyze_project
from pcb_checker.native import find_kicad


SOURCE = os.environ.get('PCB_CHECKER_TEST_PROJECT')


@unittest.skipUnless(SOURCE and find_kicad(), 'Set PCB_CHECKER_TEST_PROJECT and install KiCad for real-project tests')
class NativeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(SOURCE)
        cls.snapshot = {p.name: p.read_bytes() for p in cls.source.iterdir() if p.is_file()}
        pairs = [name for name in cls.snapshot if name.endswith('.kicad_pcb') and str(Path(name).with_suffix('.kicad_sch')) in cls.snapshot]
        if len(pairs) != 1:
            raise ValueError('The integration fixture needs one matching PCB/schematic pair at its root.')
        cls.selection = {'pcb':pairs[0], 'schematic':str(Path(pairs[0]).with_suffix('.kicad_sch'))}
        # Compare the same inventory: a user's parent folder can contain nested copies,
        # legacy CSVs or unrelated files that are intentionally absent from this ZIP.
        cls.staging = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.staging.cleanup)
        for name,content in cls.snapshot.items():
            (Path(cls.staging.name)/name).write_bytes(content)
        cls.baseline = analyze_project(cls.staging.name, native=True, **cls.selection)

    def test_real_connections_and_rule_outputs(self):
        p = self.baseline
        self.assertEqual(p.connectivity['status'], 'compared')
        self.assertEqual(p.connectivity['pins_compared'], p.connectivity['schematic_pins'])
        self.assertTrue({'erc', 'drc'} <= p.native_reports.keys())
        self.assertTrue({'kicad_erc', 'kicad_drc', 'net_split_and_merge_detection'} <= set(p.checks_performed))
        self.assertFalse(any(i.code in ('SCHEMATIC_NET_SPLIT_ON_PCB', 'PCB_NET_MERGES_SCHEMATIC_NETS') for i in p.issues))
        self.assertEqual(p.board_checks['status'],'prepared')
        self.assertTrue(p.board_checks['outline_closed'])
        self.assertEqual(p.fabrication['status'],'draft')
        self.assertEqual(hashlib.sha256(p._fabrication_assets['analyzed-board.kicad_pcb']).hexdigest(),p.board_checks['analyzed_pcb_sha256'])

    def test_real_zip_matches_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'project.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                for name, content in self.snapshot.items():
                    archive.writestr(name, content)
            zipped = analyze_project(path, native=True, **self.selection)
        self.assertEqual(zipped.connectivity, self.baseline.connectivity)
        self.assertEqual(zipped.schematic_nets, self.baseline.schematic_nets)
        self.assertEqual(zipped.pcb_nets, self.baseline.pcb_nets)
        # KiCad ERC can vary finding order and choose a different representative
        # power symbol on the same net. Compare categories and multiplicities.
        self.assertEqual(Counter((i.code, i.severity) for i in zipped.issues),
                         Counter((i.code, i.severity) for i in self.baseline.issues))

    def test_real_pad_net_mutation_is_caught(self):
        component = self.baseline.components['C24']
        pad = next(p for p in component.pcb[0].pads if p.number == '1')
        self.assertEqual(pad.net, 'GND')
        pcb_name = self.baseline.selected_files['pcb']
        pcb = self.snapshot[pcb_name].decode('utf-8')
        lines = pcb.splitlines(keepends=True)
        start = sum(len(line) for line in lines[:pad.line - 1])
        original_suffix = pcb[start:]
        # Change this one pad's assignment using the existing +1V8 net identifier.
        net = re.search(r'\(net (\d+) "\+1V8"\)', pcb)
        self.assertIsNotNone(net)
        altered, count = re.subn(r'\(net \d+ "GND"\)', f'(net {net[1]} "+1V8")', original_suffix, count=1)
        self.assertEqual(count, 1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            for name, content in self.snapshot.items():
                (path / name).write_bytes(content)
            (path / pcb_name).write_text(pcb[:start] + altered)
            changed = analyze_project(path, native=True, **self.selection)
        codes = {i.code for i in changed.issues}
        self.assertIn('SCHEMATIC_NET_SPLIT_ON_PCB', codes)
        self.assertIn('PCB_NET_MERGES_SCHEMATIC_NETS', codes)
        self.assertTrue(any(i.code == 'PIN_NET_NAME_MISMATCH' and i.component == 'C24' for i in changed.issues))

    def test_source_files_remain_identical(self):
        for name, content in self.snapshot.items():
            self.assertEqual(hashlib.sha256((self.source / name).read_bytes()).digest(), hashlib.sha256(content).digest())
            self.assertEqual((Path(self.staging.name)/name).read_bytes(), content)

    def test_source_geometry_matches_native_finding_coordinates(self):
        from pcb_checker.board_locations import board_locations
        index=board_locations(self.baseline._fabrication_assets['analyzed-board.kicad_pcb'])['item_locations']
        matched=0
        for issue in self.baseline.issues:
            if issue.source!='kicad_drc':continue
            for item in issue.items:
                shape=index.get(item.get('uuid'))
                if not shape:continue
                self.assertLess(math.dist(shape['point'],[item['pos']['x'],item['pos']['y']]),.002,shape['label'])
                matched+=1
        self.assertGreater(matched,0)


class RunFilterTests(unittest.TestCase):
    """_run failure detection without KiCad; runs in the ordinary suite."""

    def test_fontconfig_noise_does_not_fail_healthy_commands(self):
        from pcb_checker.native import _run
        out = _run('/bin/sh', ['-c', 'echo "Fontconfig error: No writable cache directories" >&2'],
                   Path(tempfile.gettempdir()))
        self.assertEqual(out, '')

    def test_genuine_stderr_errors_still_fail(self):
        from pcb_checker.native import NativeError, _run
        with self.assertRaises(NativeError):
            _run('/bin/sh', ['-c', 'echo "Error: cannot open design" >&2'], Path(tempfile.gettempdir()))

    def test_fontconfig_config_failure_noise_does_not_fail(self):
        # Some environments print "Cannot load config file" instead of going
        # silent with FONTCONFIG_FILE=/dev/null; that noise must not fail a
        # healthy command.
        from pcb_checker.native import _run
        out = _run('/bin/sh', ['-c', 'echo "Fontconfig error: Cannot load config file /dev/null" >&2'],
                   Path(tempfile.gettempdir()))
        self.assertEqual(out, '')

    def test_kicad_lock_cleanup_noise_does_not_fail(self):
        # KiCad CLI can report its own stale lock-file cleanup on stderr
        # while the command itself succeeds (returncode 0 with a saved
        # report). That housekeeping noise must not fail a healthy command.
        from pcb_checker.native import _run
        out = _run('/bin/sh', ['-c', 'echo "12:49:37 AM: Error: Failed to remove lock file \'/tmp/org.kicad.kicad/instances/kicad-cli-9.0\' (error 2: No such file or directory)" >&2'],
                   Path(tempfile.gettempdir()))
        self.assertEqual(out, '')


class TimeoutRetryTests(unittest.TestCase):
    """DRC timeout evidence and one bounded retry; no KiCad needed."""

    def test_timeout_reports_duration_and_partial_output(self):
        from pcb_checker.native import NativeTimeout, _run
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(NativeTimeout) as raised:
                _run('/bin/sh', ['-c', 'echo "lock held by sibling" >&2; sleep 5'],
                     Path(td), timeout=1)
        message = str(raised.exception)
        self.assertIn('timed out after', message)
        self.assertIn('(limit 1s)', message)
        self.assertIn('lock held by sibling', message)

    def test_timeout_without_output_says_so(self):
        from pcb_checker.native import NativeTimeout, _run
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(NativeTimeout) as raised:
                _run('/bin/sh', ['-c', 'sleep 5'], Path(td), timeout=1)
        self.assertIn('No output was captured before timing out', str(raised.exception))

    def test_drc_retries_once_then_reports_both_facts(self):
        from pcb_checker.native import NativeTimeout, _run_checker_job
        calls = []
        def flaky(executable, args, directory, timeout=90, **kw):
            calls.append(list(args))
            if len(calls) == 1:
                raise NativeTimeout('KiCad command timed out after 1.0s (limit 1s): drc.')
            Path(args[args.index('--output') + 1]).write_text('{"ok": true}')
            return ''
        with tempfile.TemporaryDirectory() as td:
            with patch('pcb_checker.native._run', side_effect=flaky):
                with patch('pcb_checker.native.time.sleep') as slept:
                    text = _run_checker_job('kicad', 'drc', ['pcb', 'drc'], 'in.pcb',
                                            Path(td) / 'checker-drc.json', Path(td),
                                            progress=calls.append)
        self.assertEqual(json.loads(text), {'ok': True})
        self.assertEqual(len([c for c in calls if isinstance(c, list)]), 2, 'exactly two attempts')
        slept.assert_called_once_with(5)
        self.assertTrue(any('retrying once' in str(c) for c in calls), 'retry announced via progress')

    def test_drc_gives_up_after_second_timeout(self):
        from pcb_checker.native import NativeTimeout, _run_checker_job
        attempts = []
        def always_slow(executable, args, directory, timeout=90, **kw):
            attempts.append(1)
            raise NativeTimeout('KiCad command timed out after 1.0s (limit 1s): drc.')
        with tempfile.TemporaryDirectory() as td:
            with patch('pcb_checker.native._run', side_effect=always_slow):
                with patch('pcb_checker.native.time.sleep'):
                    with self.assertRaises(NativeTimeout):
                        _run_checker_job('kicad', 'drc', ['pcb', 'drc'], 'in.pcb',
                                         Path(td) / 'checker-drc.json', Path(td))
        self.assertEqual(len(attempts), 2, 'one retry, then the timeout is reported')

    def _capture_env(self):
        from pcb_checker import native
        import subprocess as _subprocess
        seen = {}
        def capture(*args, **kwargs):
            seen.update(kwargs.get('env', {}))
            return _subprocess.CompletedProcess(args[0], 0, '', '')
        return native, seen, capture

    def test_check_commands_disable_fontconfig(self):
        import os as _os
        native, seen, capture = self._capture_env()
        with tempfile.TemporaryDirectory() as td:
            with patch.object(native.subprocess, 'run', side_effect=capture):
                native._run('/bin/true', [], Path(td), no_fonts=True)
        self.assertEqual(seen.get('FONTCONFIG_FILE'), _os.devnull)

    def test_explicit_fontconfig_is_respected(self):
        native, seen, capture = self._capture_env()
        with tempfile.TemporaryDirectory() as td:
            with patch.dict('os.environ', {'FONTCONFIG_FILE': '/custom/fonts.conf'}):
                with patch.object(native.subprocess, 'run', side_effect=capture):
                    native._run('/bin/true', [], Path(td), no_fonts=True)
        self.assertEqual(seen.get('FONTCONFIG_FILE'), '/custom/fonts.conf')

    def test_checker_jobs_request_no_fonts(self):
        from pcb_checker.native import _run_checker_job
        kwargs = {}
        def fake_run(executable, args, directory, timeout=90, **kw):
            kwargs.update(kw)
            Path(args[args.index('--output') + 1]).write_text('{}')
            return ''
        with tempfile.TemporaryDirectory() as td:
            with patch('pcb_checker.native._run', side_effect=fake_run):
                _run_checker_job('kicad', 'drc', ['pcb', 'drc'], 'in.pcb',
                                 Path(td) / 'checker-drc.json', Path(td))
        self.assertTrue(kwargs.get('no_fonts'), 'DRC must not scan system fonts')

    def test_non_drc_jobs_do_not_retry(self):
        from pcb_checker.native import NativeTimeout, _run_checker_job
        attempts = []
        def always_slow(executable, args, directory, timeout=90, **kw):
            attempts.append(1)
            raise NativeTimeout('KiCad command timed out after 1.0s (limit 1s): erc.')
        with tempfile.TemporaryDirectory() as td:
            with patch('pcb_checker.native._run', side_effect=always_slow):
                with patch('pcb_checker.native.time.sleep') as slept:
                    with self.assertRaises(NativeTimeout):
                        _run_checker_job('kicad', 'erc', ['sch', 'erc'], 'in.sch',
                                         Path(td) / 'checker-erc.json', Path(td))
        self.assertEqual(len(attempts), 1, 'no retry for non-DRC jobs')
        slept.assert_not_called()


if __name__ == '__main__':
    unittest.main()

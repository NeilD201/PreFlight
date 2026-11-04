"""Native Mac pickers must fail loudly, never as a silent cancel."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _pcb_bootstrap  # noqa: F401
from pcb_checker.webapp import AppError, picker_paths


def completed(returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(['osascript'], returncode, stdout, stderr)


PROBE_OK = completed(0, 'ok\n', '')


class PickerTests(unittest.TestCase):
    def test_genuine_cancel_returns_none_quietly(self):
        with patch('pcb_checker.webapp.subprocess.run',
                   side_effect=[PROBE_OK, completed(1, '', 'execution error: User canceled. (-128)')]):
            self.assertIsNone(picker_paths('choose file', what='file'))

    def test_blocked_gui_fails_fast_without_hanging(self):
        with patch('pcb_checker.webapp.subprocess.run',
                   return_value=completed(1, '', 'connection invalid')) as send:
            with self.assertRaises(AppError) as caught:
                picker_paths('choose file', what='file')
        self.assertIn('no dialog access', str(caught.exception))
        self.assertIn('Project location', str(caught.exception))
        self.assertEqual(send.call_count, 1, 'a blocked GUI must fail on the probe, never hang on the picker')

    def test_picker_failure_reports_reason_not_cancel(self):
        with patch('pcb_checker.webapp.subprocess.run',
                   side_effect=[PROBE_OK, completed(1, '', 'execution error: No GUI. (-10000)')]), \
                self.assertRaises(AppError) as caught:
            picker_paths('choose file', what='file')
        self.assertIn('could not open', str(caught.exception))
        self.assertIn('Project location', str(caught.exception))

    def test_missing_oscript_reports_manual_path(self):
        with patch('pcb_checker.webapp.subprocess.run', side_effect=OSError('denied')), \
                self.assertRaises(AppError) as caught:
            picker_paths('choose folder', what='folder')
        self.assertIn('Project location', str(caught.exception))

    def test_picker_script_opens_at_valid_directory_only(self):
        from pcb_checker.webapp import file_picker_script
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            script = file_picker_script(home)
            self.assertIn('default location', script)
            self.assertIn(home, script)
        plain = file_picker_script(None)
        self.assertNotIn('default location', plain)
        self.assertNotIn('default location', file_picker_script('/no/such/dir'))
        self.assertNotIn('default location', file_picker_script('/tmp/"quoted'))
        self.assertIn('every .kicad_sch sheet', file_picker_script())

    def test_chosen_paths_are_returned_stripped(self):
        with patch('pcb_checker.webapp.subprocess.run',
                   side_effect=[PROBE_OK, completed(0, '/a/b.pro\n/c/d.pcb\n\n', '')]):
            self.assertEqual(picker_paths('choose file'), ['/a/b.pro', '/c/d.pcb'])


if __name__ == '__main__':
    unittest.main()

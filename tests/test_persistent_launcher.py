"""The launch window must not own the server's lifetime or duplicate instances."""
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker import launch_app


class PersistentLauncherTests(unittest.TestCase):
    def test_existing_instance_is_reopened_without_a_second_process(self):
        with patch.object(launch_app, 'probe', return_value='ready'), patch.object(launch_app.subprocess, 'Popen') as spawn, patch.object(launch_app.webbrowser, 'open') as browser, redirect_stdout(io.StringIO()):
            self.assertEqual(launch_app.launch(), 0)
            spawn.assert_not_called()
            browser.assert_called_once_with('http://127.0.0.1:8765')

    def test_other_service_is_left_alone(self):
        for status in ('occupied', 'unresponsive'):
            with patch.object(launch_app, 'probe', return_value=status), patch.object(launch_app.subprocess, 'Popen') as spawn, patch.object(launch_app.webbrowser, 'open') as browser, redirect_stdout(io.StringIO()):
                self.assertEqual(launch_app.launch(), 1)
                spawn.assert_not_called();browser.assert_not_called()

    def test_new_server_has_independent_session_and_redirected_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(launch_app, 'probe', side_effect=['offline','ready']), patch.object(launch_app.subprocess, 'Popen') as spawn, redirect_stdout(io.StringIO()):
                self.assertEqual(launch_app.launch(open_browser=False,root=root), 0)
                self.assertTrue(spawn.call_args.kwargs['start_new_session'])
                self.assertEqual(spawn.call_args.kwargs['stdin'],launch_app.subprocess.DEVNULL)
                self.assertEqual(spawn.call_args.args[0][1],str(root/'main.py'))
                self.assertTrue((root/'reports'/'.webserver.log').is_file())

    def test_failed_start_does_not_open_broken_page(self):
        with tempfile.TemporaryDirectory() as directory:
            child=Mock();child.poll.return_value=2
            with patch.object(launch_app, 'probe', side_effect=['offline','offline']), patch.object(launch_app.subprocess, 'Popen', return_value=child), patch.object(launch_app.webbrowser, 'open') as browser, redirect_stdout(io.StringIO()):
                self.assertEqual(launch_app.launch(root=Path(directory)), 1)
                browser.assert_not_called()


if __name__=='__main__':unittest.main()

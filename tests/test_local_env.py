"""Development-only .env credential workflow: precedence, errors, redaction, exclusion."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.meta import post_meta
from pcb_checker.ai.reviewer import AIError, redact_error, run_ai_review
from pcb_checker.local_env import load_local_env, meta_key, parse_dotenv

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = 'unit-test-meta-key-sentinel-001'


class ParseTests(unittest.TestCase):
    def test_values_quotes_comments_and_export(self):
        parsed = parse_dotenv('export LLAMA_API_KEY="abc123"  # trailing\n'
                              "GEMINI_API_KEY='g-key'\n# comment\nBLANK=\n")
        self.assertEqual(parsed, {'LLAMA_API_KEY': 'abc123', 'GEMINI_API_KEY': 'g-key'})

    def test_unknown_names_are_ignored(self):
        parsed = parse_dotenv('LLAMA_API_KEY=k\nPATH=/evil\nLD_PRELOAD=x\n')
        self.assertEqual(parsed, {'LLAMA_API_KEY': 'k'})


class PrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_dotenv_local_beats_dotenv_and_process_env_beats_files(self):
        (self.root / '.env').write_text('LLAMA_API_KEY=from-dotenv\n')
        (self.root / '.env.local').write_text('LLAMA_API_KEY=from-local\n')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LLAMA_API_KEY', None)
            load_local_env(self.root)
            self.assertEqual(meta_key(), 'from-local')
        with patch.dict(os.environ, {'LLAMA_API_KEY': 'from-process'}):
            load_local_env(self.root)
            self.assertEqual(meta_key(), 'from-process')

    def test_loader_reports_names_never_values(self):
        (self.root / '.env').write_text(f'LLAMA_API_KEY={SENTINEL}\n')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LLAMA_API_KEY', None)
            info = load_local_env(self.root)
        blob = json.dumps(info)
        self.assertIn('LLAMA_API_KEY', blob)
        self.assertNotIn(SENTINEL, blob)

    def test_oversized_or_missing_files_are_skipped(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LLAMA_API_KEY', None)
            self.assertEqual(load_local_env(self.root), {'loaded': [], 'files': []})
            self.assertEqual(meta_key(), '')


class MissingKeyTests(unittest.TestCase):
    def test_post_meta_refuses_empty_key_without_network(self):
        with self.assertRaises(AIError) as ctx:
            post_meta({'model': 'meta/muse-spark-1.3-contributor'}, '', timeout=5)
        self.assertIn('Meta API key', str(ctx.exception))

    def test_review_without_key_reports_not_configured_not_another_provider(self):
        project = type('P', (), {'provenance': {'source_hashes': {}},
                                 'datasheets': [], 'verification': {}})()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LLAMA_API_KEY', None)
            result = run_ai_review(project, key=None, provider='meta')
        self.assertEqual(result['status'], 'not_configured')
        self.assertEqual(result['provider'], 'Meta')
        self.assertIn('LLAMA_API_KEY', result['message'])


class RedactionTests(unittest.TestCase):
    def test_error_redaction_removes_key_material(self):
        message = redact_error(f'call failed for {SENTINEL} twice {SENTINEL}', SENTINEL)
        self.assertNotIn(SENTINEL, message)
        self.assertIn('[redacted]', message)


class GitExclusionTests(unittest.TestCase):
    def test_gitignore_covers_env_files_and_example_is_placeholder_only(self):
        text = (ROOT / '.gitignore').read_text()
        self.assertIn('.env', text)
        self.assertIn('.env.*', text)
        example = (ROOT / '.env.example').read_text()
        self.assertIn('LLAMA_API_KEY=', example)
        value = next(line for line in example.splitlines()
                       if line.startswith('LLAMA_API_KEY=')).split('=', 1)[1]
        self.assertIn('paste-your', value)
        try:
            for name in ('.env', '.env.local'):
                ignored = subprocess.run(['git', 'check-ignore', '-q', name],
                                         cwd=ROOT, timeout=30, check=False)
                self.assertEqual(ignored.returncode, 0, f'{name} must be git-ignored')
        except (OSError, subprocess.SubprocessError):
            self.skipTest('git unavailable')
        tracked = subprocess.run(['git', 'ls-files', '.env', '.env.local'],
                                 cwd=ROOT, capture_output=True, text=True,
                                 timeout=30, check=False)
        self.assertEqual(tracked.stdout.strip(), '', 'secret files must not be tracked')


if __name__ == '__main__':
    unittest.main()

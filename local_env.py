"""Development-only credential loading from a local `.env` file or process environment.

Precedence for the Meta API key (highest first):
  1. Explicit `key=` argument passed to the review call.
  2. `LLAMA_API_KEY` already present in the process environment.
  3. `LLAMA_API_KEY` from `.env.local` (project root, untracked).
  4. `LLAMA_API_KEY` from `.env` (project root, untracked).

Files never override variables already set in the environment, so a real
deployment cannot be silently redirected by a stray file. Values are only
ever read into memory and sent to the configured Meta endpoint; this module
never prints, logs, or returns key material — only variable *names*.

Production credential storage (macOS Keychain via `credential_store`) is
unchanged; the web app keeps filling keys from Keychain only when the
corresponding environment variable is empty.
"""
import os
from pathlib import Path

# Only these names are read from .env files. Anything else is ignored so a
# local file cannot inject unrelated configuration into the process.
ALLOWED_NAMES = frozenset({
    'LLAMA_API_KEY', 'GEMINI_API_KEY', 'OPENAI_API_KEY', 'PCB_CHECKER_AI_MODEL',
})
ENV_FILES = ('.env.local', '.env')
MAX_ENV_FILE_BYTES = 64 * 1024


def parse_dotenv(text):
    """Parse `KEY=VALUE` lines. Returns {name: value} for allowed names only."""
    found = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].strip()
        name, sep, value = line.partition('=')
        name = name.strip()
        if not sep or name not in ALLOWED_NAMES:
            continue
        value = value.strip()
        if value[:1] in ('"', "'"):
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else value[1:]
        elif '#' in value:
            value = value.split('#', 1)[0].strip()
        if value and name not in found:
            found[name] = value
    return found


def load_local_env(root=None):
    """Load `.env.local` then `.env` from the project root into os.environ.

    Existing process variables always win. Returns {'loaded': [names],
    'files': [paths]} — variable names only, never values.
    """
    base = Path(root) if root else Path(__file__).resolve().parent
    preexisting = set(os.environ)
    loaded, files = [], []
    for filename in reversed(ENV_FILES):  # Base .env first, .env.local overrides it.
        path = base / filename
        try:
            if not path.is_file() or path.stat().st_size > MAX_ENV_FILE_BYTES:
                continue
            values = parse_dotenv(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            continue
        files.append(str(path))
        for name, value in values.items():
            if name not in preexisting:
                os.environ[name] = value
                if name not in loaded:
                    loaded.append(name)
    return {'loaded': loaded, 'files': files}


def meta_key():
    """Return the Meta API key from the process environment, or ''.

    Call `load_local_env()` first in development entry points so `.env`
    files are honored. Raises nothing; callers report a missing key as
    `not_configured` instead of switching provider or model.
    """
    value = os.environ.get('LLAMA_API_KEY', '')
    return value.strip() if isinstance(value, str) else ''

# Project structure and data flow

PCB Preflight is a local Python web application. `launch_app.py` starts the
server on loopback (`127.0.0.1`); `web/` contains the browser interface and
`webapp.py` implements its local HTTP routes and application state.

## Main areas

- `main.py`, `__main__.py`, and `launch_app.py`: command-line and app startup.
- `web/`, `templates/`, and `webapp.py`: browser UI, HTTP endpoints, and
  request handling.
- `parsers/`, `archive.py`, and `native.py`: read KiCad project inputs and
  invoke optional local KiCad tools.
- `checks/`, `analysis.py`, `engineering_checks.py`, and `review.py`: local
  circuit, connectivity, board, and engineering checks.
- `ai/`, `web_chat.py`, and `datasheets.py`: optional model-backed review and
  chat, plus LCSC part/datasheet lookup.
- `board_edits.py`, `schematic_edits.py`, `repair_design.py`, and related
  modules: draft, validate, and apply supported design edits.
- `storage.py`, `chat_decisions.py`, `product_context.py`, and
  `memory_review.py`: saved reports, project context, and review decisions.
- `tests/`: automated Python and browser-script tests.
- `docs/`: design, workflow, and reliability notes.

## Network and privacy boundary

The app is intended to run locally. Its browser server binds to loopback; it is
not a hosted multi-user service. Local API routes handle project uploads,
reports, and review actions. The app also makes these outbound requests when a
feature is used:

- The selected AI provider: Google Gemini, OpenAI, or Meta Muse Spark. The provider
  receives the specific prompt and project evidence needed for that AI action;
  this can include design details. Do not send confidential designs unless
  your provider and account terms allow it.
- LCSC: part search and product/datasheet metadata requests use component
  identifiers or search terms. Datasheet PDFs are fetched from LCSC-hosted URLs.
- Manufacturer and reference-design links in checks and reports are citations;
  opening those links contacts the named website.

These endpoints are application features, not hidden telemetry. The source
does not contain a hard-coded provider API key. Provider keys can be remembered in
macOS Keychain; reports, preferences, project context, and review history are
stored locally beneath `reports/`. Other provider credentials may also be
provided through their documented environment variables. Never commit keys,
local databases, reports, uploaded boards, datasheets, or manufacturing files.

The rules in the root `.gitignore` exclude those local artifacts. This is a
repository hygiene safeguard, not a full security audit or a guarantee that a
future file cannot contain sensitive data. Review staged changes before every
push.

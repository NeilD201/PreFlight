"""Prompt for a project folder, or accept a folder/ZIP as an argument."""

import argparse
import json
import getpass
import os
import sys
import sqlite3
from dataclasses import asdict
from contextlib import closing
from pathlib import Path

from .analysis import analyze_project
from .archive import ArchiveError
from .report import inspect_component, print_report, save_report
from .checks.circuit import inspect_net, inspect_pin
from .ai.reviewer import DEFAULT_PROVIDER, PROVIDERS, check_ai_connection, run_ai_review


def saved_project_path(reports=None) -> Path | None:
    """Use this installation's last connected project, with no built-in board path."""
    db_path = Path(reports or Path(__file__).resolve().parent/'reports')/'.preflight.sqlite3'
    if not db_path.is_file():
        return None
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            row = db.execute('SELECT value FROM preferences WHERE key=?', ('linked_kicad_files',)).fetchone()
            value = json.loads(row[0]).get('path') if row else None
            return Path(value).expanduser() if isinstance(value,str) and value else None
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return None


def prompt_project_folder() -> Path:
    base = Path(__file__).resolve().parent
    configured = os.environ.get("PCB_CHECKER_PROJECT", "").strip()
    default = Path(configured).expanduser() if configured else saved_project_path()
    if default and not (default.is_dir() or (default.is_file() and default.suffix.lower() == ".zip")):
        default = None
    print(f"Place your unzipped project folder inside: {base}", file=sys.stderr)
    print("You can also enter a full folder path or a KiCad project ZIP.", file=sys.stderr)
    if default:
        print(f"Default project: {default} (press Enter to use it)", file=sys.stderr)
    while True:
        print("Unzipped project folder name or full path: ",
              end="", file=sys.stderr, flush=True)
        name = input().strip()
        if not name:
            if default:
                return default
            print("Please enter a folder name.", file=sys.stderr)
            continue
        # Accept a full path pasted with surrounding quotes, including spaces.
        if len(name) >= 2 and name[0] == name[-1] and name[0] in ("'", '"'):
            name = name[1:-1]
        if not name.strip():
            print("Please enter a folder name.", file=sys.stderr)
            continue
        path = Path(name).expanduser()
        if not path.is_absolute():
            path = base / path
        if path.is_dir() or (path.is_file() and path.suffix.lower() == '.zip'):
            return path
        print(f"Folder not found: {path}\nCheck the name and try again.", file=sys.stderr)


def prompt_ai_key(provider: str) -> str | None:
    config = PROVIDERS[provider]
    key = os.environ.get(config['key_env'], '').strip()
    if not key and sys.stdin.isatty():
        setup = ('A Meta API key is required; usage is billed to your Meta account.\n'
                 if provider == 'meta' else
                 'Get a key: https://aistudio.google.com/apikey\n'
                 'Use a Google project on the Free tier with billing disabled for free testing. Quotas apply.\n'
                 if provider == 'gemini' else 'OpenAI API usage is billed to your key.\n')
        print(f'\nAI requests go to {config["name"]}.\n' + setup +
              'The key is used for this run and is not saved. Press Enter to skip.', file=sys.stderr)
        try:
            key = getpass.getpass(f'{config["name"]} API key (hidden): ').strip()
        except (KeyboardInterrupt, EOFError):
            key = ''
    return key


def main() -> int:
    from .local_env import load_local_env
    load_local_env()  # Development-only .env support; never overrides real environment variables.
    parser = argparse.ArgumentParser(description="Compare schematic and PCB components in a KiCad folder or ZIP.")
    parser.add_argument('--version', action='version', version='PCB Preflight 0.6.0')
    parser.add_argument('--no-sourcing', action='store_true', help='Skip online LCSC matching and datasheet downloads')
    parser.add_argument("archive", nargs="?",
                        help="KiCad project folder or ZIP; omit to be asked for a folder")
    parser.add_argument('--web', action='store_true', help='Open the local browser app')
    parser.add_argument('--port', type=int, default=8765, help='Local browser app port (default: 8765)')
    parser.add_argument('--no-browser', action='store_true', help='Start the browser app without opening a tab')
    parser.add_argument("--json", action="store_true", help="Print the report and component records as JSON")
    parser.add_argument("--pcb", help="PCB path within the project, when there is more than one")
    parser.add_argument("--schematic", help="Schematic path within the project, when there is more than one")
    parser.add_argument('--bom', help='BOM CSV path within the project, when there is more than one')
    parser.add_argument('--cpl', help='Placement CSV path within the project, when there is more than one')
    parser.add_argument('--cpl-units', choices=['auto','mm','in'], default='auto', help='Confirm units for bare placement coordinates')
    parser.add_argument("--skip-kicad", action="store_true", help="Run only the built-in component checks")
    parser.add_argument("--kicad-cli", help="Path to the KiCad CLI executable")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "reports",
                        help="Parent folder for saved HTML and JSON reports")
    parser.add_argument("--no-save", action="store_true", help="Print results without saving report files")
    parser.add_argument("--component", help="Print the pin connections of a component, such as U13")
    parser.add_argument("--net", help="List every component pin on a named schematic net")
    parser.add_argument("--pin", help="Inspect one pin and its connections, for example U8.8")
    parser.add_argument("--raw", action="store_true", help="Print the original detailed rule listing")
    ai_flags = parser.add_mutually_exclusive_group()
    ai_flags.add_argument("--ai", dest="ai", action="store_true", default=None, help="Run AI review with Meta Muse Spark by default; ask privately for a key if needed")
    ai_flags.add_argument("--no-ai", dest="ai", action="store_false", help="Run only local checks, even when an API key is configured")
    parser.add_argument("--ai-provider", choices=PROVIDERS, default=DEFAULT_PROVIDER,
                        help="AI service (default: meta, Meta Muse Spark)")
    parser.add_argument("--ai-model", help="Review model (default: PCB_CHECKER_AI_MODEL or the provider's default)")
    parser.add_argument("--test-ai", action="store_true",
                        help="Test the selected API with one small request; skips the project prompt and KiCad")
    parser.add_argument("--ask", help="Ask AI a question about this project's structured circuit evidence")
    args = parser.parse_args()
    if args.ai_provider == 'meta' and args.ai_model:
        args.ai_model = 'meta/' + args.ai_model.removeprefix('meta/')
    if args.web:
        if args.test_ai or args.json or args.no_save or args.component or args.net or args.pin or args.ask or args.pcb or args.schematic or args.bom or args.cpl:
            parser.error('With --web, use the browser controls for checks, questions and source selection.')
        if not 1 <= args.port <= 65535:
            parser.error('--port must be between 1 and 65535.')
        from .webapp import serve
        return serve(args.archive or os.environ.get('PCB_CHECKER_PROJECT') or saved_project_path(args.output_dir) or '',
                     args.output_dir, port=args.port, open_browser=not args.no_browser,
                     key=os.environ.get('GEMINI_API_KEY', '').strip(),
                     model=args.ai_model or os.environ.get('PCB_CHECKER_AI_MODEL') or ('gpt-6-luna' if args.ai_provider=='openai' else PROVIDERS['meta']['model'] if args.ai_provider=='meta' else None),
                     kicad_cli=args.kicad_cli, ai=args.ai, native=not args.skip_kicad)
    if args.ask and args.ai is False:
        parser.error('--ask requires AI; remove --no-ai.')
    if args.test_ai:
        if args.ai is False or args.archive or args.ask or args.component or args.net or args.pin:
            parser.error('--test-ai runs without a project; remove project arguments, --ask and --no-ai.')
        key = prompt_ai_key(args.ai_provider)
        print('Testing the AI API with one small structured request (up to 60 seconds). No PCB files are read.',
              file=sys.stderr, flush=True)
        try:
            result = check_ai_connection(key=key, model=args.ai_model, provider=args.ai_provider)
        except KeyboardInterrupt:
            parser.exit(130, '\nAPI test cancelled.\n')
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f'AI CONNECTION TEST: {result["status"].replace("_", " ")}')
            print(f'  Provider: {result.get("provider", args.ai_provider)}')
            print(f'  Model: {result.get("model", "")!r}')
            print(f'  {result["message"]}')
        return 0 if result['status'] == 'completed' else 3
    try:
        path = Path(args.archive).expanduser() if args.archive is not None else prompt_project_folder()
        project = analyze_project(path, pcb=args.pcb, schematic=args.schematic,
                                  bom=args.bom, cpl=args.cpl, cpl_units=args.cpl_units,
                                  native=not args.skip_kicad, kicad_cli=args.kicad_cli,
                                  progress=lambda message: print(message, file=sys.stderr, flush=True))
    except EOFError:
        parser.exit(2, "\nNo folder provided. Run again and enter a folder name.\n")
    except KeyboardInterrupt:
        parser.exit(130, "\nCancelled.\n")
    except ArchiveError as exc:
        parser.exit(2, f"Import failed: {exc}\n")
    ai_config = PROVIDERS[args.ai_provider]
    from .datasheets import collect_datasheets
    from .assembly_export import generate_assembly
    from .board_visual import render_board
    progress = lambda message: print(message, file=sys.stderr, flush=True)
    if not args.no_sourcing:
        project.datasheets = collect_datasheets(project, args.output_dir / '.datasheets', progress)
        from .checks.requirements import attach_datasheet_evidence
        attach_datasheet_evidence(project)
    from .signal_paths import analyze_signal_paths
    analyze_signal_paths(project, path)
    project.assembly_export = generate_assembly(project, path, args.kicad_cli, progress)
    project.provenance.update(checker_version='0.6.0', schema_version=5)
    ai_requested = args.ai is True or bool(args.ask) or (args.ai is None and bool(os.environ.get(ai_config['key_env'])))
    if ai_requested:
        key = prompt_ai_key(args.ai_provider)
        if key:
            print(f'Running {ai_config["name"]} AI review of circuit evidence…', file=sys.stderr, flush=True)
        try:
            project.ai_review = run_ai_review(project, key=key, model=args.ai_model,
                                              provider=args.ai_provider, question=args.ask or '', design=True, deep=True, progress=progress)
        except KeyboardInterrupt:
            project.ai_review = {'status': 'cancelled', 'message': 'AI review cancelled; local checks were preserved.'}
    elif args.ai is False:
        project.ai_review = {'status': 'disabled', 'message': 'AI was disabled with --no-ai. No evidence was sent.'}
    from .design_review import compose_plan
    project.action_plan = compose_plan(project)
    if args.json:
        print(json.dumps(asdict(project), indent=2))
    else:
        print_report(project, raw=args.raw)
    if args.component:
        try:
            print(inspect_component(project, args.component), file=sys.stderr if args.json else sys.stdout)
        except ValueError as exc:
            parser.exit(2, str(exc) + "\n")
    for value, inspect in ((args.net, inspect_net), (args.pin, inspect_pin)):
        if value:
            try:
                print(inspect(project, value), file=sys.stderr if args.json else sys.stdout)
            except ValueError as exc:
                parser.exit(2, str(exc) + '\n')
    if not args.no_save:
        try:
            board_visual = render_board(project, path, args.kicad_cli)
            folder = save_report(project, args.output_dir, board_visual)
            print(f"\nOpen this report in your browser:\n{folder / 'report.html'}", file=sys.stderr)
            if project.assembly_export:
                counts = project.assembly_export['counts']
                print(f"Assembly draft: {counts['assigned']}/{counts['fitted']} LCSC codes, {counts['placed']} placements.\n{folder / 'assembly-draft.zip'}", file=sys.stderr)
        except OSError as exc:
            parser.exit(2, f"Could not save the report: {exc}\n")
    return 3 if ai_requested and project.ai_review.get('status') != 'completed' else 0


if __name__ == "__main__":
    raise SystemExit(main())

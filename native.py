"""Optional KiCad CLI integration. Commands run sequentially on temporary copies."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from typing import Callable

from .archive import MAX_FILE_BYTES, ArchiveError, read_project_file
from .models import Issue, Project
from .parsers.sexpr import ParseError, parse, properties


class NativeError(ValueError):
    pass


class NativeTimeout(NativeError):
    """A KiCad call that exceeded its time budget. Usually transient
    (shared instance locks, cold caches), so DRC gets one extra attempt."""


# KiCad CLI uses shared instance lock files: concurrent in-process calls
# (analysis plus a file-fix or firmware worker) can stall each other.
_KICAD_LOCK = threading.Lock()


@dataclass
class NativeResult:
    netlist: str | None = None
    reports: dict[str, dict] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    version: str = ""
    board_checks: dict = field(default_factory=dict)
    fabrication: dict = field(default_factory=dict)
    fabrication_assets: dict = field(default_factory=dict)


def _conventional_kicad_cli() -> list[str]:
    """Well-known install locations beyond PATH, oldest last so newer wins."""
    found = ["/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"]
    if sys.platform == "win32":
        for version in ("7.0", "8.0", "9.0"):
            for root in (f"C:\\Program Files\\KiCad\\{version}",
                         f"C:\\Program Files (x86)\\KiCad\\{version}"):
                found.append(root + "\\bin\\kicad-cli.exe")
    else:
        for version in ("7.0", "8.0", "9.0"):
            found.append(f"/usr/share/kicad-{version}/bin/kicad-cli")
    return found


def find_kicad(explicit: str | None = None) -> str | None:
    if explicit:
        return shutil.which(explicit)
    candidates = [shutil.which("kicad-cli")]
    if sys.platform == "win32":
        candidates.append(shutil.which("kicad-cli.exe"))
    for location in _conventional_kicad_cli():
        if location not in candidates:
            candidates.append(location if os.path.isfile(location) else shutil.which(location))
    return next((c for c in candidates if c), None)


def _run(executable: str, args: list[str], directory: Path, timeout: int = 90, no_fonts: bool = False) -> str:
    env = dict(os.environ, LC_ALL="C", LANG="C")
    # Check commands never render text. Disabling fontconfig removes a whole
    # hang class: kicad-cli otherwise scans system fonts on every call and can
    # stall there (observed: 90s timeouts with nothing but Fontconfig warnings
    # on stderr). Rendering exports keep normal fonts.
    if no_fonts:
        env.setdefault("FONTCONFIG_FILE", os.devnull)
    started = time.monotonic()
    try:
        with _KICAD_LOCK:
            result = subprocess.run([executable, *args], cwd=directory, env=env,
                                    capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        def _text(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
        partial = (_text(exc.stderr) + '\n' + _text(exc.stdout)).strip()[-800:]
        detail = f" KiCad said before timing out: {partial}" if partial else " No output was captured before timing out."
        raise NativeTimeout(f"KiCad command timed out after {elapsed:.1f}s (limit {timeout}s): {' '.join([executable, *args])}.{detail}") from exc
    except OSError as exc:
        raise NativeError(f"KiCad command failed or timed out: {exc}") from exc
    # No --exit-code-violations: rule violations are data; nonzero means engine failure.
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-1800:]
        raise NativeError(f"KiCad exited with {result.returncode}: {detail}")
    # Fontconfig cache and locale complaints are environmental noise (notably
    # under sandboxes and read-only homes); they never indicate engine failure
    # for netlist, ERC, DRC or version commands. Missing or unparseable output
    # files still raise through _read_output and the JSON report parsing.
    # KiCad's own stale lock-file cleanup notice is the same class of noise:
    # the command already succeeded (returncode 0) and only the removal of an
    # already-gone instance lock is reported.
    diagnostic = "\n".join(line for line in result.stderr.splitlines()
                           if not line.startswith("Fontconfig ")
                           and "Failed to remove lock file" not in line)
    if "error:" in diagnostic.lower():
        raise NativeError(diagnostic.strip()[-1800:] or result.stderr.strip()[-1800:])
    return result.stdout.strip()


# Measured on the faulty WenneBoard: DRC needs ~85-90s on a cold cache but
# has exceeded 180s on a busy machine, which throws away minutes of work and
# reruns. DRC gets a long first attempt; the single retry is kept for genuine
# transients (instance locks). Read-only stage, so patience only costs time
# when KiCad is actually slow.
_CHECK_TIMEOUTS = {"drc": 240}


def _run_checker_job(binary, name, args, input_path, output, directory, progress=None):
    """Run one KiCad checker job and return its report text.

    DRC gets one extra attempt after a short pause: DRC timeouts are usually
    transient (shared instance locks, cold caches) and DRC is read-only, so a
    bounded retry is safe. Deterministic failures are not retried.
    """
    attempts = 2 if name == "drc" else 1
    timeout = _CHECK_TIMEOUTS.get(name, 90)
    for attempt in range(attempts):
        try:
            _run(binary, [*args, "--output", str(output), input_path], directory, no_fonts=True,
                 timeout=timeout)
            return _read_output(output)
        except NativeTimeout:
            if attempt + 1 >= attempts:
                raise
            if progress:
                progress(f"KiCad {name.upper()} timed out; retrying once in a few seconds…")
            time.sleep(5)


def _read_output(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise NativeError(f"Missing or oversized KiCad output: {path.name}")
    return path.read_text(encoding="utf-8-sig")


def _stage(source: str | Path, project: Project, directory: Path) -> None:
    inventory = {PurePosixPath(item.path).as_posix(): item.path for item in project.files}
    selected = {PurePosixPath(path).with_suffix("") for path in project.selected_files.values()}
    required = set(project.selected_files.values())
    for item in project.files:
        path = PurePosixPath(item.path)
        if path.suffix in (".kicad_pro", ".kicad_dru") and path.with_suffix("") in selected:
            required.add(item.path)
    pending = sorted(required)
    copied = set()
    while pending:
        relative = pending.pop()
        if relative in copied:
            continue
        content = read_project_file(source, relative)
        target = directory.joinpath(*PurePosixPath(relative).parts)
        if not target.resolve().is_relative_to(directory.resolve()):
            raise NativeError("Project file escapes the temporary working directory.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        copied.add(relative)
        if target.suffix != '.kicad_sch':
            continue
        # Copy only selected schematics and their child sheets, not unrelated backups.
        root = parse(content.decode("utf-8-sig"), "kicad_sch")
        for sheet in root.all("sheet"):
            fields = properties(sheet)
            filename = fields.get("Sheetfile", fields.get("Sheet file", ""))
            child = target.parent / filename
            if (not filename or "${" in filename or Path(filename).is_absolute()
                    or not child.resolve().is_relative_to(directory.resolve())):
                raise NativeError(f"Child sheet is missing or outside the imported project: {filename!r}")
            child_relative = child.resolve().relative_to(directory.resolve()).as_posix()
            if child_relative not in inventory:
                raise NativeError(f"Child sheet is missing from the imported project: {filename!r}")
            pending.append(inventory[child_relative])


def run_native(source: str | Path, project: Project, *, executable: str | None = None,
               progress: Callable[[str], None] | None = None) -> NativeResult:
    result = NativeResult()
    binary = find_kicad(executable)
    if binary is None:
        result.issues.append(Issue("info", "KICAD_UNAVAILABLE",
            "KiCad CLI was not found; netlist, ERC, and DRC checks could not run.",
            suggested_fix="Install KiCad 9 or newer, or pass --kicad-cli /path/to/kicad-cli."))
        return result
    for kind, selected in project.selected_files.items():
        if kind not in ('pcb', 'schematic'):
            continue
        expected = str(PurePosixPath(selected).with_suffix('.kicad_pro'))
        if not any(PurePosixPath(item.path).as_posix() == expected for item in project.files):
            result.issues.append(Issue("warning", "PROJECT_RULES_UNAVAILABLE",
                f"No {expected} project settings were included for {kind}; KiCad may use default rules.",
                [selected], suggested_fix="Include the matching .kicad_pro file to check the saved project rules."))
    with tempfile.TemporaryDirectory(prefix="pcb-checker-") as temporary:
        directory = Path(temporary)
        try:
            result.version = _run(binary, ["version"], directory, timeout=15)
            _stage(source, project, directory)
        except (NativeError, ArchiveError, ParseError, UnicodeError, OSError) as exc:
            result.issues.append(Issue("warning", "KICAD_SETUP_FAILED", str(exc)))
            return result
        jobs = []
        board = directory / project.selected_files['pcb'] if 'pcb' in project.selected_files else None
        if board:
            from .fabrication import prepare_board, export_fabrication
            if progress:
                progress('Refilling copper on a temporary board copy…')
            original = board.read_bytes()
            result.board_checks = prepare_board(binary, board, directory)
            if result.board_checks.get('status') != 'prepared':
                board.write_bytes(original)
                result.issues.append(Issue('warning', 'COPPER_REFILL_NOT_PERFORMED',
                    result.board_checks.get('message', 'Copper refill did not run.'), source='coverage'))
            result.board_checks['drc_basis'] = 'refilled_copy' if result.board_checks.get('status') == 'prepared' else 'saved_fills'
            settings_path = board.with_suffix('.kicad_pro')
            try:
                settings = json.loads(settings_path.read_text())
                rules = settings.get('board', {}).get('design_settings', {})
                erc = settings.get('erc', {})
                result.board_checks['rule_settings'] = {
                    'status':'read',
                    'ignored_drc':sorted(k for k,v in rules.get('rule_severities', {}).items() if v == 'ignore'),
                    'ignored_erc':sorted(k for k,v in erc.get('rule_severities', {}).items() if v == 'ignore'),
                    'drc_exclusions':len(rules.get('drc_exclusions', [])),
                    'erc_exclusions':len(erc.get('erc_exclusions', []))}
            except (ValueError, OSError, TypeError, AttributeError):
                result.board_checks['rule_settings'] = {'status':'unavailable'}
        if "schematic" in project.selected_files:
            schematic = str(directory / project.selected_files["schematic"])
            jobs.extend([("netlist", ["sch", "export", "netlist", "--format", "kicadxml"], schematic, "xml"),
                         ("erc", ["sch", "erc", "--format", "json"], schematic, "json")])
        if "pcb" in project.selected_files:
            parity = ['--schematic-parity'] if 'schematic' in project.selected_files and Path(project.selected_files['schematic']).with_suffix('') == Path(project.selected_files['pcb']).with_suffix('') else []
            result.board_checks['parity_requested'] = bool(parity)
            jobs.append(("drc", ["pcb", "drc", "--format", "json", '--units', 'mm', '--severity-all', '--all-track-errors', *parity],
                         str(directory / project.selected_files["pcb"]), "json"))
        # KiCad uses shared instance lock files. Parallel invocations can interfere.
        for name, args, input_path, extension in jobs:
            if progress:
                progress(f"Running KiCad {name.upper()}…")
            output = directory / f"checker-{name}.{extension}"
            try:
                text = _run_checker_job(binary, name, args, input_path, output, directory, progress).replace(str(directory) + "/", "")
                if name == "netlist":
                    result.netlist = text
                else:
                    data = json.loads(text)
                    if not isinstance(data, dict):
                        raise NativeError("KiCad returned an unexpected report structure.")
                    result.reports[name] = data
            except (NativeError, ValueError, OSError, UnicodeError) as exc:
                message = str(exc).replace(str(directory) + "/", "")
                result.issues.append(Issue("warning", f"KICAD_{name.upper()}_FAILED", message))
        if board:
            if progress:
                progress('Checking rules ignored by the saved project…')
            result.board_checks['expanded_rule_review'] = expanded_rules(binary, jobs, directory, result.reports)
            if progress:
                progress('Exporting Gerber and drill files from the checked board copy…')
            result.fabrication, result.fabrication_assets = export_fabrication(binary, board, directory,
                result.board_checks, project.provenance.get('source_hashes', {}))
            result.board_checks['drc_completed'] = 'drc' in result.reports
            result.fabrication['drc_completed'] = 'drc' in result.reports
    return result


def expanded_rules(binary, jobs, directory, reports):
    """Re-enable ignored rules on temporary settings; restore bytes even on failure."""
    results = []
    for kind, args, input_path, extension in jobs:
        if kind not in ('erc', 'drc'):
            continue
        path = Path(input_path).with_suffix('.kicad_pro')
        try:
            original = path.read_bytes()
            settings = json.loads(original)
            group = settings.get('erc', {}) if kind == 'erc' else settings.get('board', {}).get('design_settings', {})
            severities = group.get('rule_severities', {})
            ignored = sorted(k for k,v in severities.items() if v == 'ignore')
            if not ignored:
                results.append({'kind':kind, 'status':'completed' if kind in reports else 'failed', 'rules':[], 'findings':[]})
                continue
            for name in ignored:
                severities[name] = 'warning'
            output = directory / ('checker-expanded-'+kind+'.json')
            try:
                path.write_text(json.dumps(settings), encoding='utf-8')
                _run(binary, [*args, '--output', str(output), input_path], directory)
                report = json.loads(_read_output(output).replace(str(directory)+'/', ''))
                findings = rule_issues(report, kind, Path(input_path).name)
                counts = Counter(i.code.removeprefix('KICAD_'+kind.upper()+'_').lower()
                                 for i in findings if not i.excluded)
                reports['expanded-'+kind] = report
                results.append({'kind':kind, 'status':'completed', 'rules':ignored,
                                'findings':[{'rule':name, 'count':counts[name]} for name in ignored if counts[name]]})
            finally:
                path.write_bytes(original)
        except (NativeError, ValueError, OSError, UnicodeError, TypeError, AttributeError, KeyError) as exc:
            results.append({'kind':kind, 'status':'failed', 'message':str(exc).replace(str(directory)+'/', '')})
    return {'status':'completed' if results and all(r['status']=='completed' for r in results) else 'failed', 'results':results}


def rule_issues(report: dict, kind: str, source_file: str) -> list[Issue]:
    """Preserve engine severity and item evidence, including explicitly excluded items."""
    groups = []
    if kind == "erc":
        if not isinstance(report.get("sheets"), list):
            raise NativeError("ERC report has no sheets array.")
        for sheet in report["sheets"]:
            groups.append((sheet.get("path", "/"), sheet.get("violations", [])))
    else:
        if not isinstance(report.get("violations"), list):
            raise NativeError("DRC report has no violations array.")
        for key in ("violations", "unconnected_items", "schematic_parity"):
            groups.append((key, report.get(key, [])))
    issues = []
    for group, violations in groups:
        for index, violation in enumerate(violations):
            excluded = violation.get("excluded", False) or violation.get("severity") == "exclusion"
            severity = violation.get("severity", "warning")
            severity = severity if severity in ("error", "warning", "info") else "info"
            evidence = [f"{source_file}; KiCad {kind.upper()} {group}, item {index + 1}"]
            for item in violation.get("items", []):
                detail = item.get("description", "")
                if item.get("pos"):
                    detail += f"; position {item['pos']} ({report.get('coordinate_units', 'units unspecified')}, as reported by KiCad)"
                if item.get("uuid"):
                    detail += f"; UUID {item['uuid']}"
                evidence.append(detail)
            code = f"KICAD_{kind.upper()}_{violation.get('type', 'unknown').upper()}"
            issues.append(Issue("info" if excluded else severity, code,
                violation.get("description", "KiCad rule finding"), evidence,
                source=f"kicad_{kind}", excluded=bool(excluded),
                items=violation.get("items", [])))
    return issues

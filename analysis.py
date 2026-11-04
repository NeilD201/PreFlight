"""Select source files, parse components, merge references, and run checks."""

from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
import hashlib
from typing import Callable

from .archive import ArchiveError, import_project, read_project_file
from .checks.components import compare_components, reference_key, validate_components
from .checks.connectivity import attach_netlist, compare_connectivity, pcb_net_inventory
from .models import Component, Issue, Project, ProjectFile
from .parsers.kicad_pcb import parse_pcb
from .parsers.kicad_schematic import child_sheet_files, parse_schematic
from .parsers.schematic_netlist import resolve_schematic_nets
from .parsers.sexpr import ParseError
from .native import NativeError, rule_issues, run_native
from .review import build_review
from .parsers.assembly import CSVError, parse_assembly
from .checks.assembly import check_assembly, parts_inventory


def _backup(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (parts[-1].startswith(("_autosave-", "~"))
            or any(part.lower() in ("backup", "backups")
                   or part.lower().endswith("-backups") for part in parts[:-1]))


def _schematic_hint_stems(project: Project) -> set:
    """Stems that identify the root schematic: the board and project stems.

    Mirrors the Browse flow, which pairs the sheet whose stem matches the
    .kicad_pro stem. Hierarchical projects store every sub-sheet as its own
    .kicad_sch file, so stem equality is how the root is recognized.
    """
    stems = set()
    for item in project.files:
        if _backup(item.path):
            continue
        if item.kind in ('pcb', 'kicad_project'):
            stems.add(PurePosixPath(item.path).with_suffix('').name)
    return stems


def _select(project: Project, kind: str, requested: str | None) -> ProjectFile | None:
    files = [item for item in project.files if item.kind == kind]
    if requested is not None and kind.endswith('_candidate'):
        files = [item for item in project.files if item.kind in ('bom_candidate','cpl_candidate','csv_unknown')]
    candidates = [item for item in files if not _backup(item.path)]
    for item in files:
        if _backup(item.path):
            item.status = "skipped_backup"
    if requested is not None:
        normalized = PurePosixPath(requested).as_posix()
        selected = next((item for item in files if PurePosixPath(item.path).as_posix() == normalized), None)
        if selected is None:
            raise ArchiveError(f"Selected {kind} file {requested!r} is not in the project inventory.")
        return selected
    if len(candidates) == 1:
        return candidates[0]
    if kind == "schematic" and len(candidates) > 1:
        matched = [item for item in candidates
                   if PurePosixPath(item.path).with_suffix('').name in _schematic_hint_stems(project)]
        if len(matched) == 1:
            return matched[0]
        names = ", ".join(item.path for item in candidates)
        raise ArchiveError(
            f"Multiple root schematic candidates found ({names}). Select the project's root schematic "
            f"in check options or using --schematic with its path from the inventory. "
            f"No circuit checks have run and no AI review was started.")
    if len(candidates) > 1:
        project.issues.append(Issue(
            "warning", f"SELECT_{kind.upper()}_FILE",
            f"Multiple {kind.removesuffix('_candidate')} files found. Choose one in check options or using --{kind.removesuffix('_candidate')} with its path from the inventory.",
            [item.path for item in candidates],
        ))
    elif files:
        project.issues.append(Issue("info", f"ONLY_{kind.upper()}_BACKUPS",
                                    f"Only backup/autosave {kind} files found. Select one explicitly to parse it.",
                                    [item.path for item in files]))
    return None


def _pair_confirmed(selected: dict[str, ProjectFile | None], explicit: bool) -> bool:
    if selected["schematic"] is None or selected["pcb"] is None:
        return False
    schematic = PurePosixPath(selected["schematic"].path).with_suffix("")
    pcb = PurePosixPath(selected["pcb"].path).with_suffix("")
    return explicit or schematic == pcb


def _merge_child_sheets(project: Project, path: str | Path, root_item, root_text: str, parsed):
    """Parse hierarchical child sheets and merge their symbols into parsed.

    Only inventory files of the same project are read; originals are never
    modified. comparison_supported is restored only when every referenced
    sheet resolved without ambiguity. Bounded to 64 sheets.
    """
    from .archive import read_project_file as _read
    top_path = "/" + (parsed.document_uuid or "")
    root_stem = PurePosixPath(root_item.path).stem
    inventory = {PurePosixPath(item.path).as_posix(): item for item in project.files
                 if item.kind == "schematic" and not _backup(item.path)}
    seen = {PurePosixPath(root_item.path).as_posix()}
    queue = [(name, PurePosixPath(root_item.path).parent) for name in child_sheet_files(root_text)]
    sheets: list[str] = []
    ok = True
    while queue and len(sheets) < 64:
        name, directory = queue.pop(0)
        rel = PurePosixPath(str(directory / name)).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        item = inventory.get(rel)
        if item is None:
            ok = False
            parsed.issues.append(Issue(
                "warning", "HIERARCHY_SHEET_UNRESOLVED",
                f"Child sheet {name!r} referenced by the schematic was not found in the project; its symbols are missing.",
                [root_item.path]))
            continue
        try:
            text = _read(path, rel).decode("utf-8-sig")
            child = parse_schematic(text, rel, instance_root=top_path, project_name=root_stem)
        except (ArchiveError, ParseError, UnicodeError) as exc:
            ok = False
            item.status = "parse_failed"
            parsed.issues.append(Issue("error", "FILE_PARSE_FAILED",
                                      f"Could not parse {rel}: {exc}", [rel]))
            continue
        if any(issue.code == "AMBIGUOUS_SYMBOL_INSTANCE" for issue in child.issues):
            ok = False
        existing = {(r.reference, r.source_file) for r in parsed.components}
        for record in child.components:
            if record.reference in {r.reference for r in parsed.components} and (record.reference, rel) not in existing:
                ok = False
                parsed.issues.append(Issue(
                    "warning", "HIERARCHY_DUPLICATE_REFERENCE",
                    f"{record.reference} appears on more than one sheet with different symbols; comparison is skipped.",
                    [f"{rel}:{record.line}"]))
                continue
            parsed.components.append(record)
        item.status = "components_parsed"
        project.provenance.setdefault("source_hashes", {})[rel] = hashlib.sha256(
            _read(path, rel)).hexdigest()
        parsed.issues.extend(child.issues)
        parsed.non_physical_symbols += child.non_physical_symbols
        sheets.append(rel)
        for sub in child_sheet_files(text):
            queue.append((sub, PurePosixPath(rel).parent))
    if queue:
        ok = False
        parsed.issues.append(Issue(
            "warning", "HIERARCHY_PARTIAL",
            "More than 64 hierarchical sheets were referenced; only the first 64 were merged and comparison is skipped.",
            [root_item.path]))
    parsed.issues = [issue for issue in parsed.issues if issue.code != "HIERARCHY_NOT_SUPPORTED"]
    if sheets:
        parsed.issues.append(Issue(
            "info" if ok else "warning", "HIERARCHY_MERGED" if ok else "HIERARCHY_PARTIAL",
            f"Merged {len(sheets)} hierarchical sheet(s) ({', '.join(sheets)}); "
            + ("all sheet symbols are included in the comparison." if ok else
               "some sheets could not be resolved; comparison is skipped."),
            sheets))
    parsed.comparison_supported = ok and not any(
        issue.code in ("AMBIGUOUS_SYMBOL_INSTANCE", "HIERARCHY_SHEET_UNRESOLVED",
                       "HIERARCHY_DUPLICATE_REFERENCE", "FILE_PARSE_FAILED")
        for issue in parsed.issues)
    return parsed


def _attach_internal_schematic_nets(project: Project, path) -> None:
    """Fallback schematic connectivity when no KiCad CLI netlist is available.

    Resolves pin-to-net assignments from the schematic sheets with the
    internal geometric netlister and attaches them in netlist shape, so the
    existing connectivity comparison runs unchanged. Never overwrites pins
    a netlist export already provided; failures leave the project for the
    standard not-performed path with a recorded reason.
    """
    selected = project.selected_files.get("schematic")
    if not selected:
        return
    notes = []
    try:
        root_text = read_project_file(path, selected).decode("utf-8-sig")
    except (ArchiveError, OSError, KeyError, UnicodeDecodeError) as exc:
        project.connectivity["internal_notes"] = [f"Internal schematic netlist skipped: {exc}"]
        return
    child_texts = {}
    base = PurePosixPath(selected).parent
    for child in child_sheet_files(root_text):
        if len(child_texts) >= 64:
            notes.append("Internal schematic netlist stopped at 64 child sheets.")
            break
        try:
            child_texts[child] = read_project_file(path, str(base.joinpath(child))).decode("utf-8-sig")
        except (ArchiveError, OSError, KeyError, UnicodeDecodeError) as exc:
            notes.append(f"Child sheet {child} unreadable, skipped: {exc}")
    try:
        result = resolve_schematic_nets(root_text, child_texts)
    except (ParseError, ValueError) as exc:
        project.connectivity["internal_notes"] = notes + [f"Internal schematic netlist failed: {exc}"]
        return
    for ref, pin_list in result["pins"].items():
        component = project.components.get(ref)
        if component is not None and not component.schematic_pins:
            component.schematic_pins = list(pin_list)
    project.schematic_nets = dict(result["nets"])
    notes.extend(f"Internal netlist: {note}" for note in result["notes"][:10])
    project.connectivity["internal_notes"] = notes
    project.connectivity["internal_stats"] = result["stats"]


def analyze_project(path: str | Path, *, pcb: str | None = None,
                    schematic: str | None = None, native: bool = False,
                    bom: str | None = None, cpl: str | None = None, cpl_units: str = 'auto',
                    kicad_cli: str | None = None,
                    progress: Callable[[str], None] | None = None) -> Project:
    project = import_project(path)
    if not any(f.kind in ('pcb', 'schematic') for f in project.files):
        foreign = {'.epro', '.schdoc', '.pcbdoc', '.prjpcb', '.brd', '.sch', '.fsch', '.fbrd', '.dsn', '.opj'}
        if any(Path(f.path).suffix.lower() in foreign for f in project.files):
            raise ArchiveError('This project uses a CAD format that Preflight cannot analyze yet. Editor connections can monitor its saved files, but review currently requires KiCad .kicad_sch and .kicad_pcb files. No circuit checks have run.')
    project.provenance = {'schema_version':3, 'checker_version':'0.3.0',
                          'created':datetime.now(timezone.utc).isoformat(), 'source_hashes':{},
                          'options':{'native':native,'cpl_units':cpl_units}}
    project.issues = [issue for issue in project.issues
                      if issue.code not in ("VERIFICATION_NOT_PERFORMED", "MULTIPLE_BOARDS")]
    project.stage = "component_inventory"
    ready = {}
    selected = {"schematic": _select(project, "schematic", schematic),
                "pcb": _select(project, "pcb", pcb)}
    for kind, item in selected.items():
        if item is None:
            continue
        project.selected_files[kind] = item.path
        parser = parse_schematic if kind == "schematic" else parse_pcb
        try:
            raw = read_project_file(path, item.path)
            project.provenance['source_hashes'][item.path] = hashlib.sha256(raw).hexdigest()
            text = raw.decode("utf-8-sig")
            parsed = parser(text, item.path)
            if kind == 'schematic':
                project.provenance['schematic_uuid'] = parsed.document_uuid
                if not parsed.comparison_supported and child_sheet_files(text):
                    parsed = _merge_child_sheets(project, path, item, text, parsed)
        except (ArchiveError, ParseError, UnicodeError) as exc:
            item.status = "parse_failed"
            project.issues.append(Issue("error", "FILE_PARSE_FAILED",
                                        f"Could not parse {item.path}: {exc}", [item.path]))
            continue
        item.status = "components_parsed" if parsed.comparison_supported else "components_partially_parsed"
        project.issues.extend(parsed.issues)
        project.non_physical_symbols += parsed.non_physical_symbols
        ready[kind] = parsed.comparison_supported
        for record in parsed.components:
            component = project.components.setdefault(record.reference, Component(record.reference))
            getattr(component, kind).append(record)
    project.components = dict(sorted(project.components.items(), key=lambda item: reference_key(item[0])))
    ambiguous = validate_components(project)
    if ready:
        project.checks_performed.append("component_reference_and_field_validation")
    paired = _pair_confirmed(selected, explicit=pcb is not None and schematic is not None)
    if all(selected.values()) and not paired:
        project.issues.append(Issue(
            "warning", "SOURCE_PAIR_NOT_CONFIRMED",
            "The PCB and schematic have different names or folders. Confirm the intended pair using both --pcb and --schematic.",
            list(project.selected_files.values()),
        ))
    if ready.get("schematic") and ready.get("pcb") and paired:
        compare_components(project, ambiguous)
        project.stage = "component_comparison"
        project.checks_performed.extend(["schematic_pcb_reference_comparison", "value_comparison",
                                         "footprint_name_comparison"])
    else:
        project.issues.append(Issue(
            "info", "COMPONENT_COMPARISON_NOT_PERFORMED",
            "Component comparison requires a confirmed pair of successfully parsed PCB and supported single-sheet schematic files.",
        ))
    pcb_net_inventory(project)
    if native:
        for item in project.files:
            if item.kind in ('kicad_project','kicad_rules') and not _backup(item.path):
                project.provenance['source_hashes'][item.path] = hashlib.sha256(read_project_file(path,item.path)).hexdigest()
    if native:
        native_result = run_native(path, project, executable=kicad_cli, progress=progress)
        project.board_checks = native_result.board_checks
        project.fabrication = native_result.fabrication
        project._fabrication_assets = native_result.fabrication_assets
        if project.board_checks.get('status') == 'prepared':
            project.checks_performed.extend(['copper_zone_refill','board_outline_validation'])
        if project.fabrication.get('status') == 'draft':
            project.checks_performed.append('fabrication_export')
        project.issues.extend(native_result.issues)
        project.engine = {"name": "KiCad CLI", "version": native_result.version or "unavailable"}
        for kind, data in native_result.reports.items():
            if kind.startswith('expanded-'):
                project.native_reports[kind] = data
                continue
            source = project.selected_files["schematic" if kind == "erc" else "pcb"]
            try:
                findings = rule_issues(data, kind, source)
            except (NativeError, TypeError, KeyError, AttributeError) as exc:
                project.issues.append(Issue("warning", f"KICAD_{kind.upper()}_REPORT_INVALID", str(exc)))
                continue
            project.native_reports[kind] = data
            project.issues.extend(findings)
            project.checks_performed.append(f"kicad_{kind}")
            if kind == 'drc':
                project.board_checks['parity_findings'] = sum(not p.get('excluded') and p.get('severity') != 'exclusion' for p in data.get('schematic_parity', []))
        if native_result.netlist and project.stage == "component_comparison":
            try:
                attach_netlist(project, native_result.netlist)
                compare_connectivity(project, ambiguous)
            except ParseError as exc:
                project.issues.append(Issue("warning", "NETLIST_COMPARISON_FAILED", str(exc)))
    if project.connectivity.get("status") != "compared" and project.stage == "component_comparison":
        _attach_internal_schematic_nets(project, path)
        saved_notes = project.connectivity.get("internal_notes")
        saved_stats = project.connectivity.get("internal_stats")
        if project.connectivity.get("status") != "compared":
            compare_connectivity(project, ambiguous)
            project.connectivity["internal_resolver"] = True
        if saved_notes is not None:
            project.connectivity["internal_notes"] = saved_notes
        if saved_stats is not None:
            project.connectivity["internal_stats"] = saved_stats
    if project.connectivity.get("status") != "compared":
        project.connectivity["status"] = "not_performed"
        project.issues.append(Issue("info", "CONNECTIVITY_NOT_PERFORMED",
            "Connectivity comparison requires a resolved KiCad netlist and a supported schematic/PCB pair."))
    assembly_parsed = {}
    for kind, requested in (('bom',bom),('cpl',cpl)):
        item = _select(project, kind+'_candidate', requested)
        if item is None:
            continue
        project.selected_files[kind] = item.path
        try:
            raw = read_project_file(path,item.path)
            project.provenance['source_hashes'][item.path] = hashlib.sha256(raw).hexdigest()
            assembly_parsed[kind] = parse_assembly(raw,item.path,kind,units=cpl_units)
            item.status = 'assembly_'+assembly_parsed[kind]['status']
        except (ArchiveError,CSVError) as exc:
            item.status = 'parse_failed'
            project.issues.append(Issue('error','ASSEMBLY_PARSE_FAILED',f'Could not parse {item.path}: {exc}',[item.path],source='assembly'))
    check_assembly(project,assembly_parsed,pcb_ready=ready.get('pcb',False))
    project.assembly['parts'] = parts_inventory(project)
    for part in project.assembly['parts']:
        if part['conflicts']:
            project.issues.append(Issue('warning','ASSEMBLY_PART_IDENTITY_CONFLICT',
                f'{part["reference"]} has conflicting declared {", ".join(part["conflicts"])} fields across the project sources.',
                component=part['reference'],source='assembly',
                suggested_fix='Choose the intended manufacturer and part number, verify its package and ratings, then make the source fields consistent.'))
    for relative, digest in project.provenance['source_hashes'].items():
        if hashlib.sha256(read_project_file(path,relative)).hexdigest() != digest:
            raise ArchiveError('A source file changed during the run. Save the project and run again: '+relative)
    project.issues.append(Issue(
        "info", "CHECKS_NOT_IMPLEMENTED",
        ("Full electrical-rating/pinout verification and manufacturer-specific fabrication checks have not run. LCSC matching and PDF reading are reported separately. "
         "Copper refill and fabrication export status are reported separately; passing rule checks is not production approval.") if native else
        "Connectivity, electrical and manufacturing checks have not run. LCSC matching and PDF reading are reported separately.",
    ))
    from .memory_review import add_findings
    add_findings(project)
    project.review = build_review(project)
    return project

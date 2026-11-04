"""Read placed symbols, without counting embedded library definitions."""

from pathlib import PurePosixPath

from ..models import ComponentRecord, Issue, ParsedComponents
from .sexpr import Node, flag, parse, properties, unit_number


def _instance(symbol: Node, top_path: str, project_name: str) -> tuple[str, int] | None:
    """Select only the current project instance, avoiding other saved projects.

    top_path is the top sheet's ``"/" + uuid`` path. Root symbols match it
    exactly; symbols on child sheets match below it at any nesting depth.
    """
    instances = symbol.first("instances")
    if instances is None:
        return None
    projects = instances.all("project")
    preferred = [p for p in projects if p.values == [project_name]]
    candidates = preferred or [p for p in projects if p.values == [""]]
    matches = [path for project in candidates for path in project.all("path")
               if path.values and (path.values[0].rstrip("/") == top_path
                                   or path.values[0].rstrip("/").startswith(top_path + "/"))]
    if len(matches) != 1 or not matches[0].get("reference"):
        return None
    return matches[0].get("reference"), unit_number(matches[0], unit_number(symbol))


def child_sheet_files(text: str) -> list[str]:
    """Filenames referenced by hierarchical sheet instances in a schematic file."""
    root = parse(text, "kicad_sch")
    files = []
    for sheet in root.all("sheet"):
        try:
            props = properties(sheet)
        except ParseError:
            continue
        name = props.get("Sheetfile", props.get("Sheet file", "")).strip()
        if name and name not in files:
            files.append(name)
    return files


def parse_schematic(text: str, source_file: str, instance_root: str | None = None,
                    project_name: str | None = None) -> ParsedComponents:
    """Parse one schematic sheet.

    instance_root selects the project instance for a child sheet: pass the top
    sheet's ``"/" + uuid`` path so sub-sheet symbols resolve to this project.
    project_name overrides the instance project match (child sheets live in
    files whose stem differs from the project name). When instance_root is set,
    hierarchy is accounted for by the caller and no HIERARCHY_NOT_SUPPORTED
    issue is emitted for nested sheets.
    """
    root = parse(text, "kicad_sch")
    top_path = instance_root or ("/" + root.get("uuid"))
    name = project_name or PurePosixPath(source_file).stem
    result = ParsedComponents(document_uuid=root.get("uuid"))
    if root.all("sheet") and instance_root is None:
        result.comparison_supported = False
        result.issues.append(Issue(
            "info", "HIERARCHY_NOT_SUPPORTED",
            "This schematic has child sheets. Only root symbols were read; comparison is skipped.",
            [f"{source_file}:{sheet.line}" for sheet in root.all("sheet")],
            suggested_fix="Use a single-sheet project for this version; hierarchy support comes later.",
        ))
    # KiCad 6 kept instance overrides in a root-level symbol_instances section.
    legacy_instances = root.first("symbol_instances")
    legacy = {}
    if legacy_instances:
        for path in legacy_instances.all("path"):
            if path.values:
                legacy[path.values[0]] = path
    for symbol in root.all("symbol"):
        fields = properties(symbol)
        reference = fields.get("Reference", "").strip()
        unit = unit_number(symbol)
        instance = _instance(symbol, top_path, name)
        if instance:
            reference, unit = instance
        elif symbol.first("instances") is not None:
            result.comparison_supported = False
            result.issues.append(Issue(
                "warning", "AMBIGUOUS_SYMBOL_INSTANCE",
                f"Could not identify the current project instance for {reference or 'a symbol'}; comparison is skipped.",
                [f"{source_file}:{symbol.line}"], component=reference or None,
            ))
        old_instance = legacy.get("/" + symbol.get("uuid"))
        if old_instance is not None:
            reference = old_instance.get("reference", reference)
            unit = unit_number(old_instance, unit)
            fields = dict(fields, Value=old_instance.get("value", fields.get("Value", "")),
                          Footprint=old_instance.get("footprint", fields.get("Footprint", "")))
        if reference.startswith("#"):
            result.non_physical_symbols += 1
            continue
        result.components.append(ComponentRecord(
            reference=reference.strip(), value=fields.get("Value", ""),
            footprint=fields.get("Footprint", ""), source_file=source_file,
            line=symbol.line, uuid=symbol.get("uuid"), symbol=symbol.get("lib_id"),
            unit=unit, in_bom=flag(symbol, "in_bom", True),
            on_board=flag(symbol, "on_board", True), dnp=flag(symbol, "dnp", False),
            properties=fields,
        ))
    return result

"""Component identity and field consistency; no electrical correctness claims."""

from decimal import Decimal, DecimalException
import re

from ..models import ComponentRecord, Issue, Project


REFERENCE = re.compile(r"[A-Za-z][A-Za-z0-9_]*[0-9]+")
SCALES = {"": "1", "p": "1e-12", "n": "1e-9", "u": "1e-6",
          "m": "1e-3", "k": "1e3", "K": "1e3", "M": "1e6", "G": "1e9"}
NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def reference_key(reference: str) -> tuple:
    # Numeric strings sort naturally without converting arbitrarily long input to int.
    return tuple((1, len(part.lstrip("0")), part.lstrip("0")) if part.isdigit() else (0, part)
                 for part in re.split(r"(\d+)", reference))


def _passive_value(reference: str, value: str) -> Decimal | None:
    """Normalize simple R/C/L values, preserving the m (milli) / M (mega) distinction."""
    kind = re.sub(r"[0-9]+$", "", reference).upper()
    units = {"R": r"(?:[RrΩ]|[Oo]hm[s]?)?", "C": r"[Ff]?", "L": r"[Hh]?"}
    if kind not in units or len(value) > 256:
        return None
    value = re.sub(r"\s+", "", value).replace("µ", "u").replace("μ", "u")
    match = re.fullmatch(f"({NUMBER})([pnumkKMG]?)" + units[kind], value)
    if match:
        number, scale = match.groups()
    else:
        # Common notation where the scale replaces the decimal point: 4k7, 4R7.
        match = re.fullmatch(r"(\d+)([pnumkKMGRr])(\d+)" + units[kind], value)
        if not match or (match[2] in "Rr" and kind != "R"):
            return None
        number, scale = f"{match[1]}.{match[3]}", match[2]
        scale = "" if scale in "Rr" else scale
    try:
        return Decimal(number) * Decimal(SCALES[scale])
    except DecimalException:
        return None


def values_match(reference: str, left: str, right: str) -> bool:
    if left.strip() == right.strip():
        return True
    a, b = _passive_value(reference, left), _passive_value(reference, right)
    return a is not None and b is not None and a == b


def _issue(project: Project, severity: str, code: str, reference: str,
           message: str, records: list[ComponentRecord], fix: str | None = None) -> None:
    project.issues.append(Issue(severity, code, message,
                                [record.location for record in records],
                                component=reference or None, suggested_fix=fix))


def validate_components(project: Project) -> set[str]:
    """Return ambiguous references that must not be compared across sources."""
    ambiguous = set()
    for reference, component in project.components.items():
        all_records = component.schematic + component.pcb
        if REFERENCE.fullmatch(reference) is None:
            ambiguous.add(reference)
            _issue(project, "warning", "INVALID_REFERENCE", reference,
                   f"Reference {reference!r} is missing, unannotated, or unsupported.", all_records,
                   "Check the reference in KiCad and annotate the schematic if needed.")
            continue
        if len(component.pcb) > 1:
            ambiguous.add(reference)
            _issue(project, "error", "DUPLICATE_PCB_REFERENCE", reference,
                   f"{reference} occurs on {len(component.pcb)} PCB footprints.", component.pcb)
        symbols = component.schematic
        if len(symbols) > 1:
            units = [record.unit for record in symbols]
            signatures = {(r.symbol, r.value, r.footprint, r.on_board, r.in_bom, r.dnp) for r in symbols}
            if len(set(units)) != len(units):
                ambiguous.add(reference)
                _issue(project, "error", "DUPLICATE_SCHEMATIC_REFERENCE", reference,
                       f"{reference} repeats the same symbol unit in the schematic.", symbols)
            elif len(signatures) != 1 or not symbols[0].symbol:
                ambiguous.add(reference)
                _issue(project, "warning", "SCHEMATIC_UNIT_CONFLICT", reference,
                       f"{reference}'s symbol units have inconsistent properties.", symbols)
        for record in all_records:
            if not record.value.strip() or record.value.strip() == "~":
                _issue(project, "info", "MISSING_VALUE", reference,
                       f"{reference} has no value in {record.source_file}.", [record])
            if record in symbols and record.on_board and not record.footprint.strip():
                _issue(project, "warning", "MISSING_FOOTPRINT", reference,
                       f"{reference} has no schematic footprint assignment.", [record])
    return ambiguous


def compare_components(project: Project, ambiguous: set[str]) -> None:
    for reference in sorted(project.components, key=reference_key):
        if reference in ambiguous:
            continue
        component = project.components[reference]
        schematic = component.schematic[0] if component.schematic else None
        pcb = component.pcb[0] if component.pcb else None
        records = component.schematic + component.pcb
        if schematic is None:
            if pcb.board_only:
                _issue(project, "info", "BOARD_ONLY_COMPONENT", reference,
                       f"{reference} is marked board-only; no schematic symbol is expected.", records)
            else:
                _issue(project, "warning", "PCB_COMPONENT_MISSING_FROM_SCHEMATIC", reference,
                       f"{reference} appears in the PCB but not in the schematic. Review whether it is an intentional board-only footprint.", records)
        elif not schematic.on_board:
            if pcb:
                _issue(project, "warning", "SCHEMATIC_EXCLUDED_FROM_BOARD", reference,
                       f"{reference} is excluded from the board in the schematic, but a PCB footprint exists.", records)
        elif pcb is None:
            _issue(project, "warning", "SCHEMATIC_COMPONENT_MISSING_FROM_PCB", reference,
                   f"{reference} is assigned to the board in the schematic but has no PCB footprint.", records,
                   "Review the component and use Update PCB from Schematic in KiCad if appropriate.")
        else:
            if schematic.value.strip() and pcb.value.strip() and not values_match(reference, schematic.value, pcb.value):
                _issue(project, "warning", "VALUE_MISMATCH", reference,
                       f"{reference}: schematic value {schematic.value!r}; PCB value {pcb.value!r}.", records,
                       "Confirm the intended value and synchronize the schematic and PCB fields.")
            if schematic.footprint.strip() and pcb.footprint.strip() and schematic.footprint.strip() != pcb.footprint.strip():
                _issue(project, "warning", "FOOTPRINT_MISMATCH", reference,
                       f"{reference}: schematic footprint {schematic.footprint!r}; PCB footprint {pcb.footprint!r}.", records,
                       "Confirm the intended footprint assignment in KiCad. This check compares names only.")

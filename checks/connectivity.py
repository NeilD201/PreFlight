"""Compare pin-to-net assignments and endpoint groups, independent of net numbering."""

from collections import defaultdict
import math
import re

from ..models import Issue, Project

#: Footprint pad designators that conventionally mark mechanical mounting
#: legs rather than electrical terminals (MP = mounting pad, MH = mounting
#: hole, etc.). An unconnected pad with one of these designators is normally
#: a mechanical leg, but only the part datasheet establishes its function —
#: the designation alone is supporting evidence, never proof, and never a
#: request for the user to verify anything.
_MOUNTING_PAD = re.compile(r'^(MP|MH|MOUNT\w*|TAB|MECH\w*)\d*$', re.IGNORECASE)
from ..parsers.netlist import parse_netlist
from ..parsers.sexpr import ParseError
from .components import reference_key


def pcb_net_inventory(project: Project) -> None:
    nets = defaultdict(set)
    for reference, component in project.components.items():
        for record in component.pcb:
            for pad in record.pads:
                if pad.number and pad.net:
                    nets[pad.net].add(f"{reference}.{pad.number}")
    project.pcb_nets = {name: sorted(members) for name, members in sorted(nets.items())}


def attach_netlist(project: Project, text: str) -> None:
    pins, nets = parse_netlist(text)
    expected = {ref for ref, c in project.components.items() if c.schematic and c.schematic[0].on_board}
    if not expected <= set(pins) or any(ref not in project.components or not project.components[ref].schematic for ref in pins):
        raise ParseError("Exported netlist references disagree with the parsed schematic; connectivity comparison is skipped.")
    for reference, values in pins.items():
        project.components[reference].schematic_pins = values
    project.schematic_nets = nets


def pad_measurements(component) -> dict:
    """Measured footprint pad geometry for one component.

    Counts pads, reports the minimum center-to-center pitch and the board
    extents from parsed at/size values only. Pads without usable geometry
    (custom primitives) are counted as unmeasured; conformance to the
    manufacturer drawing stays NOT VERIFIED because PDF figures are not
    machine-extracted — this reports what was measured, never a pass.
    """
    pads = [pad for record in (component.pcb or []) for pad in (record.pads or [])]
    measured = [p for p in pads
                if p.x_mm is not None and p.y_mm is not None
                and p.width_mm is not None and p.height_mm is not None]
    pitch = None
    if len(measured) >= 2:
        best = None
        for i, a in enumerate(measured):
            for b in measured[i + 1:]:
                gap = math.hypot(a.x_mm - b.x_mm, a.y_mm - b.y_mm)
                if best is None or gap < best:
                    best = gap
        pitch = round(best, 4) if best is not None else None
    extents = {}
    if measured:
        xs = [p.x_mm for p in measured]
        ys = [p.y_mm for p in measured]
        extents = {'span_x_mm': round(max(xs) - min(xs), 4),
                   'span_y_mm': round(max(ys) - min(ys), 4)}
    return {'pad_count': len(pads), 'measured_pads': len(measured),
            'unmeasured_pads': len(pads) - len(measured),
            'min_pitch_mm': pitch, 'conformance': 'NOT VERIFIED', **extents}


def _name(name: str) -> str:
    return name.replace("{slash}", "/")


def _is_ground_net(net: str) -> bool:
    """Recognize explicit ground labels only; never infer from connectivity."""
    return bool(re.fullmatch(r"(?:[ADP]?GND|VSS|0V)", _name(net or "").rsplit("/", 1)[-1], re.I))


def classify_pad_gap(number, nets, kinds):
    """Classify a footprint pad that has no pin in the schematic symbol.

    Returns (class, basis) using only the pad designation, assigned nets and
    pad kinds parsed from the KiCad files. Classes are structural only:
    'mounting' for mechanical designations/holes without nets, 'grounded-pad'
    when every assigned net is an explicit ground label (no function claimed),
    and 'unverified' otherwise — an unmatched pad is never declared a defect
    and never declared optional by this function. The part datasheet alone
    establishes the pad function.
    """
    nets = {n for n in (nets or []) if n}
    kinds = {k for k in (kinds or []) if k}
    designation = (number or "").strip()
    if not nets and (_MOUNTING_PAD.match(designation)
                     or (kinds and all(kind == "np_thru_hole" for kind in kinds))):
        return ("mounting",
                f"Pad {designation or '?'} carries no net and its designation "
                "reads as a mechanical leg or mounting hole.")
    if nets and all(_is_ground_net(net) for net in nets):
        return ("grounded-pad",
                f"Pad {designation or '?'} connects to "
                f"{', '.join(sorted(nets))}; grounding alone does not prove "
                "a thermal or shield function.")
    if nets:
        return ("unverified",
                f"Pad {designation or '?'} connects to "
                f"{', '.join(sorted(nets))} but has no symbol pin; its "
                "function is NOT VERIFIED without the part datasheet.")
    return ("unverified",
            f"Pad {designation or '?'} has no symbol pin and no assigned net; "
            "its function is NOT VERIFIED without the part datasheet.")


def compare_connectivity(project: Project, ambiguous: set[str]) -> None:
    pcb_assignments = {}
    schematic_assignments = {}
    evidence = {}
    checked = 0
    for ref, component in project.components.items():
        if ref in ambiguous or not component.schematic or len(component.pcb) != 1 or not component.schematic[0].on_board:
            continue
        record = component.pcb[0]
        by_number = defaultdict(list)
        for pad in record.pads:
            if pad.number:
                by_number[pad.number].append(pad)
        for pin in component.schematic_pins:
            endpoint = f"{ref}.{pin.number}"
            sources = [f"{component.schematic[0].location}; KiCad netlist pin {endpoint}: {pin.net}"]
            pads = by_number.get(pin.number, [])
            if not pads:
                project.issues.append(Issue("error", "PIN_MISSING_FROM_PCB", f"{endpoint} has no matching numbered PCB pad.",
                                            sources + [record.location], component=ref))
                continue
            sources.extend(f"{record.source_file}:{pad.line}; pad {pin.number}: {pad.net or '(no net)'}" for pad in pads)
            assignments = {pad.net for pad in pads}
            if len(assignments) != 1:
                project.issues.append(Issue("error", "PAD_NET_CONFLICT", f"{endpoint}'s repeated pads have different net assignments.",
                                            sources, component=ref))
                continue
            actual = next(iter(assignments))
            checked += 1
            evidence[endpoint] = sources
            schematic_assignments[endpoint] = pin.net
            pcb_assignments[endpoint] = actual
            expected_peers = set(project.schematic_nets.get(pin.net, [])) - {endpoint}
            actual_peers = set(project.pcb_nets.get(actual, [])) - {endpoint}
            if pin.no_connect and actual_peers:
                project.issues.append(Issue("error", "NO_CONNECT_PIN_CONNECTED", f"{endpoint} is marked no-connect but its PCB net also contains {', '.join(sorted(actual_peers))}.",
                                            sources, component=ref))
            if pin.no_connect and expected_peers:
                project.issues.append(Issue("error", "NO_CONNECT_ON_CONNECTED_NET", f"{endpoint} has a no-connect marker on a shared schematic net.",
                                            sources, component=ref))
            if expected_peers and not actual:
                project.issues.append(Issue("error", "PCB_PAD_WITHOUT_NET", f"{endpoint} should be on {pin.net!r}, but the PCB pad has no net.",
                                            sources, component=ref))
            # Generated singleton names are bookkeeping for disconnected pins.
            disconnected = pin.net.startswith("unconnected-(") and not expected_peers
            if actual and not disconnected and _name(pin.net) != _name(actual):
                # Same copper under different KiCad auto-names (e.g. Net-(SW1-A)
                # vs Net-(U4-P0)) is naming noise, not a wiring fault: the
                # split/merge group checks below compare membership separately.
                if set(expected_peers) == set(actual_peers):
                    continue
                project.issues.append(Issue("warning", "PIN_NET_NAME_MISMATCH", f"{endpoint}: schematic net {pin.net!r}; PCB net {actual!r}.",
                                            sources, component=ref,
                                            suggested_fix="Review the net assignment and net names in KiCad; connectivity groups are checked separately."))
        known = {pin.number for pin in component.schematic_pins}
        if known:
            for number in sorted(set(by_number) - known, key=reference_key):
                pads = by_number[number]
                nets = {pad.net for pad in pads if pad.net and not pad.net.startswith('unconnected-(')}
                kinds = {pad.kind for pad in pads if pad.kind}
                if not nets and bool(_MOUNTING_PAD.match(number or '')):
                    message = (f"{ref} pad {number} has no pin in the exported schematic netlist and carries no net. "
                               f"Mounting designation ({len(pads)} pad(s)) suggests a mechanical leg; no edit is supported.")
                    mechanical = True
                elif not nets and kinds and all(kind == 'np_thru_hole' for kind in kinds):
                    message = (f"{ref} pad {number} has no pin in the exported schematic netlist and carries no net. "
                               f"Unplated hole ({len(pads)} pad(s)) suggests a mechanical mounting hole; no edit is supported.")
                    mechanical = True
                elif nets:
                    net_list = ', '.join(sorted(nets))
                    message = (f"{ref} pad {number} has no pin in the exported schematic netlist but connects to {net_list}. "
                               "Exposed thermal pads usually must connect to ground; mounting or shield pads follow the "
                               "package drawing. Confirm the pad function against the part datasheet; no edit is supported "
                               "from connectivity alone.")
                    mechanical = False
                else:
                    message = (f"{ref} pad {number} has no pin in the exported schematic netlist (footprint pad, unassigned net). "
                               "Package function needs the part datasheet; no edit is supported from connectivity alone.")
                    mechanical = False
                datasheet = next((r.properties.get("Datasheet", "") for r in component.schematic
                                  if r.properties.get("Datasheet", "").strip()), "")
                measured = {pad.number: {"x_mm": pad.x_mm, "y_mm": pad.y_mm,
                                                       "width_mm": pad.width_mm, "height_mm": pad.height_mm}
                            for pad in pads
                            if pad.x_mm is not None and pad.width_mm is not None}
                pad_class, pad_basis = classify_pad_gap(number, nets, kinds)
                project.issues.append(Issue("warning", "PCB_PAD_MISSING_FROM_SYMBOL", message,
                    [f"{record.source_file}:{pad.line}" for pad in pads], component=ref,
                    details={"pad_number": number, "mechanical_mount": mechanical,
                             "pad_nets": sorted(nets), "pad_kinds": sorted(kinds),
                             "pad_geometry_mm": measured,
                             "footprint_measurements": pad_measurements(component),
                             "pad_classification": {"class": pad_class, "basis": pad_basis},
                             "datasheet_url": datasheet}))
    expected_to_actual = defaultdict(lambda: defaultdict(list))
    actual_to_expected = defaultdict(lambda: defaultdict(list))
    for endpoint, expected in schematic_assignments.items():
        actual = pcb_assignments[endpoint]
        if actual:
            expected_to_actual[expected][actual].append(endpoint)
            actual_to_expected[actual][expected].append(endpoint)
    for expected, groups in expected_to_actual.items():
        if len(groups) > 1:
            members = [pin for pins in groups.values() for pin in pins]
            project.issues.append(Issue("error", "SCHEMATIC_NET_SPLIT_ON_PCB",
                f"Schematic net {expected!r} is split between PCB nets: {', '.join(sorted(groups))}.",
                [item for pin in members for item in evidence[pin]]))
    for actual, groups in actual_to_expected.items():
        if len(groups) > 1:
            members = [pin for pins in groups.values() for pin in pins]
            project.issues.append(Issue("error", "PCB_NET_MERGES_SCHEMATIC_NETS",
                f"PCB net {actual!r} merges distinct schematic nets: {', '.join(sorted(groups))}.",
                [item for pin in members for item in evidence[pin]]))
    project.connectivity = {
        "status": "compared", "pins_compared": checked,
        "schematic_pins": sum(len(c.schematic_pins) for c in project.components.values()),
        "pcb_pads": sum(len(r.pads) for c in project.components.values() for r in c.pcb),
        "schematic_nets": len(project.schematic_nets), "pcb_nets": len(project.pcb_nets),
    }
    project.checks_performed.extend(["pin_pad_correspondence", "pin_net_assignment_comparison",
                                     "net_split_and_merge_detection", "no_connect_comparison"])

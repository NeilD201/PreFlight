"""Explain findings without changing the engine's original severity or hiding evidence."""

from collections import Counter, defaultdict
from dataclasses import asdict
import re

from .checks.circuit import build_circuit, pcb_pinfunction_swap_findings
from .checks.components import reference_key
from .component_phrases import display_name
from .checks.values import value_sanity_findings
from .checks.requirements import requirements_audit_findings
from .evidence_analysis import erc_findings
from .models import Project
from .footprint_context import footprint_rule_context


CATEGORIES = {
    "electrical": "Circuit / connectivity", "signal": "Signal paths", "manufacturing": "Fabrication limits",
    "assembly": "Assembly", "documentation": "Schematic / library setup",
    "cosmetic": "Cosmetic", "expected": "Expected / excluded", "coverage": "Checks unavailable",
}
ORDER = {"fix": 0, "review": 1, "note": 2}
VIA_CODES = {"KICAD_DRC_ANNULAR_WIDTH", "KICAD_DRC_DRILL_OUT_OF_RANGE", "KICAD_DRC_VIA_DIAMETER"}
COSMETIC_CODES = {"KICAD_DRC_SILK_OVERLAP", "KICAD_DRC_SILK_OVER_COPPER", "KICAD_DRC_SILK_EDGE_CLEARANCE",
                  "KICAD_DRC_TEXT_HEIGHT", "KICAD_DRC_TEXT_THICKNESS"}
MISMATCH_CODES = {"SCHEMATIC_NET_SPLIT_ON_PCB", "PCB_NET_MERGES_SCHEMATIC_NETS", "PAD_NET_CONFLICT",
    "PCB_PAD_WITHOUT_NET", "PIN_MISSING_FROM_PCB", "NO_CONNECT_PIN_CONNECTED", "NO_CONNECT_ON_CONNECTED_NET",
    "SCHEMATIC_COMPONENT_MISSING_FROM_PCB", "VALUE_MISMATCH", "KICAD_DRC_SHORTING_ITEMS",
    "KICAD_DRC_UNCONNECTED_ITEMS"}
COPPER_STUB_CODES = {"KICAD_DRC_TRACK_DANGLING", "KICAD_DRC_ISOLATED_COPPER"}


def issue_id(index: int) -> str:
    return f"F{index + 1:04d}"


def _references(issue) -> list[str]:
    refs = {issue.component} if issue.component else set()
    for item in issue.items:
        refs.update(re.findall(r"(?:of |Symbol |field of |Footprint )([A-Za-z]+[0-9]+)\b", item.get("description", "")))
    return sorted(refs, key=reference_key)


def _classification(issue):
    """Return family, category, priority, title, interpretation, next action."""
    code = issue.code
    if code == 'MEMORY_CONNECTION_REVIEW':
        return (code, 'electrical', 'review', 'Memory signal connections need review',
                'Pin-role and net topology screening found connections that need the exact controller, memory pinout and bus mode. This is not a verified timing or functional failure.',
                issue.suggested_fix)
    if code == 'RF_FEED_NO_TERMINAL':
        return (code, 'signal', 'review', 'Antenna feed has no identified endpoint',
                'The ANT pin and matching network are visible, but the scan found no named radiator or RF connector at the output. An unconventional copper antenna can escape this check.',
                issue.suggested_fix)
    if code == 'SENSITIVE_INPUT_ROUTE_REVIEW':
        return (code, 'signal', 'note', 'ECG routing measurements',
                'Different via counts alone do not establish a wiring mistake, noise problem or a need to reroute.',
                issue.suggested_fix)
    if issue.source == 'assembly' and not any(word in code for word in ('NOT_PERFORMED', 'NOT_CONFIRMED', 'PARSE_FAILED')):
        return (code, 'assembly', 'fix' if issue.severity == 'error' else 'review',
                code.removeprefix('ASSEMBLY_').replace('_',' ').capitalize(),
                'The supplied assembly files differ from the board or contain data that cannot be used reliably. Population exceptions need an explicit decision.',
                issue.suggested_fix or 'Correct the assembly export and check it again.')
    if issue.excluded:
        return ("excluded", "expected", "note", "Excluded KiCad findings",
                "These findings were explicitly excluded in KiCad. The exclusion is preserved, not independently endorsed.",
                "Keep a reason for each intentional exclusion.")
    if code in VIA_CODES:
        return ("vias", "manufacturing", "review", "Via dimensions versus saved limits",
                "The same via can fail diameter, drill and annular-ring rules. These rules do not establish what your manufacturer can produce.",
                "Confirm the selected board process supports the actual drill and copper-ring dimensions, then align KiCad rules or resize the vias.")
    if code in COSMETIC_CODES:
        return ("silkscreen", "cosmetic", "note", "Silkscreen and label cleanup",
                "Usually a readability or appearance issue. Polarity, pin-1 marks and exposed solderable pads still matter.",
                "Check essential assembly markings; tidy other text when preparing fabrication files.")
    if code == "KICAD_DRC_CLEARANCE":
        return (code, "manufacturing", "review", "Copper spacing versus saved clearance",
                "A positive gap below a configured minimum is a rule conflict. It does not by itself demonstrate a short circuit.",
                "Compare the measured gap and footprint land pattern with the intended fabrication process.")
    if code == "KICAD_DRC_SOLDER_MASK_BRIDGE":
        return (code, "assembly", "review", "Shared solder-mask openings",
                "Mask openings join across different nets. This may increase solder-bridge risk, but shared openings can be intentional on fine-pitch parts.",
                "Check package land patterns, mask expansion and stencil design with the assembler.")
    if code == "KICAD_ERC_POWER_PIN_NOT_DRIVEN":
        return (code, "documentation", "review", "Power source is not represented for ERC",
                "ERC found no recognized power-output pin. An external supply through a passive connector can produce this finding on a working circuit.",
                "Verify the intended power source, then represent it with appropriate pin types or a power flag.")
    if code == "KICAD_ERC_PIN_NOT_CONNECTED":
        return (code, "documentation", "review", "Unconnected pins need an intent check",
                "These pins have no connection or no-connect marker. Whether they are needed depends on the circuit and firmware.",
                "Connect required pins; mark intentionally unused pins with no-connect flags.")
    if code == "KICAD_ERC_PIN_TO_PIN":
        descriptions = ' '.join(i.get("description", "") for i in issue.items)
        supply_type = bool(re.search(r"\[(?:VDD(?:IO)?|VSS|GND(?:@\d+)?), Bidirectional", descriptions))
        return (("supply_pin_types" if supply_type else code), "documentation" if supply_type else "electrical", "review",
                "Supply pins declared bidirectional" if supply_type else "Pin-type conflicts need operating-mode context",
                "A supply-like pin is typed as a bidirectional signal; check its symbol definition." if supply_type else
                "A configuration/address strap can legitimately connect to a supply. A signal output driven against a supply can be a real fault; the selected mode matters.",
                "Check the manufacturer's pin function and selected interface mode before changing wiring or suppressing ERC.")
    if code == 'KICAD_ERC_MULTIPLE_NET_NAMES':
        return (code, 'documentation', 'note', 'Different names for the same connection',
                'KiCad found two names on one connected wire and chose one for the exported net. This message alone does not show a short or a wrong supply connection.',
                'No wiring change is indicated by this naming warning. The names and saved connections are shown below.')
    if "LIB_" in code or code == "FOOTPRINT_MISMATCH":
        return (code, "documentation", "review", code.replace("KICAD_ERC_", "").replace("KICAD_DRC_", "").replace('_', ' ').capitalize(),
                "A library or naming difference was detected. Its electrical significance requires inspecting what changed.",
                "Review pin numbers, footprint geometry and naming intent; do not replace custom parts blindly.")
    if code in MISMATCH_CODES:
        return (code, "electrical", "fix", code.replace('KICAD_DRC_', '').replace('_', ' ').capitalize(),
                "The available checks found a concrete connectivity or component discrepancy. Resolve the discrepancy or establish the intended exception.",
                issue.suggested_fix or "Inspect the listed components and nets in the schematic and PCB before ordering.")
    if any(word in code for word in ("NOT_IMPLEMENTED", "NOT_PERFORMED", "UNAVAILABLE", "UNSUPPORTED", "NOT_SUPPORTED", "FAILED", "PARSE_FAILED", "AMBIGUOUS", "SELECT_", "NOT_CONFIRMED")):
        return (code, "coverage", "note", "Check coverage: " + code.replace('_', ' ').lower(),
                issue.message, issue.suggested_fix or "Supply the missing inputs or resolve the import/engine problem to run this check.")
    if code == "ROUTING_DIFF_PAIR":
        pair = (issue.details or {}).get("pair", [])
        label = "/".join(pair) if pair else "pair"
        return (code, "signal", "review", f"Differential pair {label} routed lengths",
                ("Measured copper spans for the two legs are reported as facts. A mismatch is only a reason "
                 "to compare against the interface skew budget, not proof of a functional failure."),
                issue.suggested_fix or "Compare the measured mismatch against the interface timing budget; adjust tuning only if the budget requires it.")
    if code == "ROUTING_ACUTE_BEND":
        return (code, "manufacturing", "review", "Acute copper bends need a fabrication check",
                ("Sharp interior corners can trap etchant during fabrication; they do not change the circuit "
                 "topology. Obtuse bends are normal routing and are never reported."),
                issue.suggested_fix or "Confirm the bends against the fabricator's etch rules or widen them; no electrical change is implied.")
    if code == "PCB_PAD_MISSING_FROM_SYMBOL":
        if (issue.details or {}).get("mechanical_mount"):
            return (code, "electrical", "note", "Mechanical mounting pad not in the symbol",
                    "This mounting-designated pad (MP/MH) carries no net and reads as a mechanical leg, which is a valid design choice. "
                    "Only the part datasheet establishes its exact function; no connection is assumed.",
                    "No edit is supported from connectivity alone; confirm the mounting role against the part datasheet only if the assembly needs it.")
        url = (issue.details or {}).get("datasheet_url", "")
        doc = f" The symbol lists {url} — confirm the pad function there before changing copper." if url else ""
        classification = ((issue.details or {}).get("pad_classification") or {})
        basis = classification.get("basis", "") if isinstance(classification, dict) else ""
        structural = f" Structural check: {basis}" if basis else ""
        return (code, "electrical", "review", "Footprint pads missing from the symbol",
                "Numbered pads absent from the symbol may be mounting legs, exposed pads, NC pads, or a wrong symbol/footprint pairing. "
                f"An unconnected mounting-designated pad (MP/MH) is normally a mechanical leg, but only the part datasheet establishes its function.{structural}{doc}",
                "No edit is supported from connectivity alone. Package function needs the part datasheet; do not ground pads without a manufacturer-specified connection." + doc)
    return (code, "electrical" if issue.severity == "error" else "documentation",
            "review" if issue.severity != "info" else "note", code.replace('KICAD_DRC_', '').replace('KICAD_ERC_', '').replace('_', ' ').capitalize(),
            "This finding has not been classified as a confirmed functional failure. Review its evidence and the project's intent.",
            issue.suggested_fix or "Inspect the source items and decide whether the saved rule matches the intended design.")


def _issue_net_names(issue) -> list[str]:
    """Designer-assigned net names quoted in a DRC issue's item descriptions.

    KiCad quotes the net in item text such as ``Track [Q_CLK] on F.Cu``.
    ERC brackets quote pin electrical types rather than nets, so ERC issues
    are excluded here (their nets come from erc_findings instead).
    Auto-generated ``Net-*`` names carry no design intent and are excluded.
    """
    if issue.source == "kicad_erc":
        return []
    names = []
    for item in issue.items:
        for name in re.findall(r"\[([^\]]+)\]", item.get("description", "")):
            name = name.strip()
            if name and not re.match(r"(?i)^net[-_(\[]", name) and name not in names:
                names.append(name)
    return names


def _erc_refs_nets(project, group):
    """Component references and schematic nets behind a group's ERC pins."""
    refs, nets = set(), set()
    for finding in erc_findings(project, group):
        for connection in finding["connections"]:
            if connection.get("virtual"):
                continue
            refs.add(connection["reference"])
            if connection.get("net"):
                nets.add(connection["net"])
    return refs, nets


def _escalate_stub_copper(project, issue, classification):
    """Keep net-named copper stubs visible to Vector.

    A dangling track or isolated copper island on a designer-named net would
    otherwise classify as documentation-grade and never reach the AI review.
    Even with a schematic loaded, the netlist confirms the connected net but
    nothing establishes whether the stub itself is an intentional test point
    or antenna, or a forgotten fragment that leaves a signal open. Escalation
    is review-grade: a reason to look, not proof of a fault.
    """
    family, category, priority, title, why, action = classification
    if category != "documentation" or issue.code not in COPPER_STUB_CODES:
        return classification
    nets = _issue_net_names(issue)
    if not nets:
        return classification
    label = nets[0] + (f" (+{len(nets) - 1} more)" if len(nets) > 1 else "")
    if "schematic" in project.selected_files:
        context = (f"The schematic confirms the {label} net, but nothing confirms what this "
                   "stub itself is for. ")
    else:
        context = (f"No schematic is loaded, so nothing confirms whether this stub is an "
                   "intentional test point, antenna, or heatsink fragment, or a forgotten track end. ")
    return (family, "electrical", "review",
            f"Unconnected copper stub on {label} needs intent review",
            ("KiCad reports unconnected copper touching the designer-named net "
             f"{label}. {context}"
             "A floating stub can radiate or couple noise; a stub that should have connected "
             "somewhere is an open circuit. This is a reason to look, not proof of a fault."),
            (f"Decide what the stub on {label} is for. If it is intentional, record that. "
             "If it should connect somewhere, route it or remove it, then run KiCad's board "
             "check and this preflight again."))


PIN_IMPLICATION_CODES = {"KICAD_ERC_PIN_NOT_CONNECTED", "KICAD_ERC_NOCONNECT_CONNECTED",
                         "KICAD_ERC_NOCONNECT_NOT_CONNECTED"}


# Schematic/PCB field-identity checks (value, footprint) describe the same physical
# part as a circuit part-finding on that reference. Keep one finding per defect with
# both-domain evidence so the repair can be coordinated instead of split in two.
_IDENTITY_CODES = {'VALUE_MISMATCH', 'FOOTPRINT_MISMATCH'}


def _pad_gap_steps(project, entries):
    """Concrete beginner steps for a footprint pad missing from the symbol."""
    first = next((issue for _, issue in entries if issue.code == "PCB_PAD_MISSING_FROM_SYMBOL"), None)
    if first is None or (first.details or {}).get("mechanical_mount"):
        return []
    ref = first.component
    pad = (first.details or {}).get("pad_number", "?")
    url = (first.details or {}).get("datasheet_url", "")
    component = project.components.get(ref)
    output_net, output_pad = "", ""
    if component is not None:
        for pin in component.schematic_pins:
            if (pin.name or "").upper() in ("VO", "VOUT", "VCC", "VDD", "OUT"):
                output_net = pin.net
                break
        if output_net:
            for record in component.pcb:
                for footprint_pad in record.pads:
                    if footprint_pad.net == output_net:
                        output_pad = footprint_pad.number
                        break
                if output_pad:
                    break
    open_doc = f"Open this datasheet page in a browser: {url}. " if url else "Open the part's datasheet. "
    find_fig = (open_doc + "Find the package pinout figure (for example SOT-223 top view) and read which pin the wide tab pad connects to. "
                "The tab is part of the package drawing, not a guess.")
    steps = [find_fig]
    if output_net and output_pad:
        steps.append(f"If the figure ties the tab to the output pin: in the PCB editor, draw copper from {ref} pad {pad} "
                     f"to the {output_net} net (the net already on {ref} pad {output_pad}). Save in KiCad, then re-run checks; "
                     "this warning should disappear.")
    else:
        steps.append(f"If the figure ties the tab to a numbered pin: in the PCB editor, draw copper from {ref} pad {pad} "
                     "to that pin's net. Save in KiCad, then re-run checks; this warning should disappear.")
    steps.append("If the figure marks the tab NC or thermal-only: change nothing. Ask Vector chat to record the datasheet "
                 "page against this finding so a later review can clear it.")
    return steps


def _merge_identity_issues_into_part_findings(project, groups):
    part_group_by_ref = {}
    for group in groups:
        refs = group.get('components') or []
        if 'reference' in group and len(refs) == 1:
            part_group_by_ref.setdefault(refs[0], group)
    if not part_group_by_ref:
        return
    for group in list(groups):
        if 'reference' in group or not group.get('issue_ids'):
            continue
        entries = []
        for fid in group['issue_ids']:
            if isinstance(fid, str) and fid.startswith('F') and fid[1:].isdigit():
                index = int(fid[1:]) - 1
                if 0 <= index < len(project.issues):
                    entries.append(project.issues[index])
        if not entries:
            continue
        refs = {issue.component for issue in entries if issue.component}
        codes = {issue.code for issue in entries}
        if len(refs) != 1 or not codes or not codes <= _IDENTITY_CODES:
            continue
        target = part_group_by_ref.get(next(iter(refs)))
        if target is None or any(fid in target['issue_ids'] for fid in group['issue_ids']):
            continue
        target['issue_ids'].extend(fid for fid in group['issue_ids'] if fid not in target['issue_ids'])
        target['raw_count'] = len(target['issue_ids'])
        for evidence in group.get('evidence', []):
            if evidence not in target.get('evidence', []) and len(target.get('evidence', [])) < 8:
                target.setdefault('evidence', []).append(evidence)
        target.setdefault('merged_issue_codes', []).extend(
            code for code in sorted(codes) if code not in target['merged_issue_codes'])
        groups.remove(group)


def _promote_implicated_pins(project, groups):
    """Route unconnected-pin notes into review when they touch a live finding.

    An unconnected pin is usually intent, but not when its component or net is
    already implicated by a fix-grade or electrical-review group (a missing
    footprint, a net conflict, a swapped signal). Promoted groups keep their
    ERC evidence; only the routing changes so Vector weighs them together.
    """
    hot_refs, hot_nets = set(), set()
    for group in groups:
        if group.get("priority") == "fix" or (group.get("priority") == "review"
                                              and group.get("category") in ("electrical", "signal")):
            hot_refs.update(group.get("components", []))
            refs, nets = _erc_refs_nets(project, group)
            hot_refs.update(refs)
            hot_nets.update(nets)
            for fid in group.get("issue_ids", []):
                if fid.startswith("F") and fid[1:].isdigit() and 0 < int(fid[1:]) <= len(project.issues):
                    hot_nets.update(_issue_net_names(project.issues[int(fid[1:]) - 1]))
    if not hot_refs and not hot_nets:
        return
    for group in groups:
        if group.get("category") != "documentation" or group.get("priority") != "review":
            continue
        codes = {project.issues[int(fid[1:]) - 1].code for fid in group.get("issue_ids", [])
                 if fid.startswith("F") and fid[1:].isdigit() and 0 < int(fid[1:]) <= len(project.issues)}
        if not codes or codes - PIN_IMPLICATION_CODES:
            continue
        refs = set(group.get("components", []))
        nets = set()
        extra_refs, extra_nets = _erc_refs_nets(project, group)
        refs.update(extra_refs)
        nets.update(extra_nets)
        overlap = sorted((refs & hot_refs) | (nets & hot_nets))
        if not overlap:
            continue
        group["category"] = "electrical"
        group["why"] = (group.get("why", "") + " These unconnected pins touch "
                        + ", ".join(overlap) + ", which is already implicated by another finding, "
                        "so intent cannot be assumed from the ERC note alone.")


def _merge_same_cause_part_groups(project, groups):
    """Merge per-part groups that share one root cause into a single group.

    Several parts with the same rule and finding (e.g. three LED series
    resistors) become one group listing every component, preserving each
    part's evidence. Groups with an automatic deterministic edit stay
    separate so one click still prepares exactly one safe edit.
    """
    from . import file_fixes as _fixes
    buckets = {}
    order = []
    for group in groups:
        key = group.pop('_merge_key', None)
        if not key or not key[0]:
            merged = False
        else:
            mode = _fixes.availability(project, {**group, 'issue_ids': group.get('issue_ids', [])}).get('mode')
            group['_auto'] = mode == 'automatic'
            if mode == 'automatic':
                merged = False
            else:
                bucket = buckets.setdefault((key, group.get('basis'), group.get('priority')), [])
                bucket.append(group)
                merged = True
        if not merged:
            order.append(group)
    for (key, _basis, _priority), bucket in buckets.items():
        if len(bucket) < 2:
            order.extend(bucket)
            continue
        _rule, base = key
        first = bucket[0]
        refs = []
        for group in bucket:
            refs.extend(r for r in group.get('components', []) if r not in refs)
            first['issue_ids'].extend(i for i in group.get('issue_ids', []) if i not in first['issue_ids'])
            first['evidence'] = list(dict.fromkeys(first.get('evidence', []) + group.get('evidence', [])))[:8]
            first['raw_count'] = first.get('raw_count', 0) + group.get('raw_count', 0)
            if isinstance(first.get('item_count'), int) and isinstance(group.get('item_count'), int):
                first['item_count'] += group['item_count']
        first['components'] = refs
        first['title'] = f"{base} ({', '.join(refs)})"
        order.append(first)
    return order


def _finding_outcome(project, group):
    """Explicit repair outcome for every finding.

    verified_correction: a deterministic edit exists and the preview
        validates it on an isolated copy before anything is applied.
    conditional: evidence supports a specific correction once the stated
        assumptions are confirmed.
    investigation_required: evidence is insufficient to recommend a change.
    informational: no correction is justified; kept for context.
    """
    from . import file_fixes as _fixes
    if group.get('no_change_basis') or group.get('reference_only') or group.get('priority') == 'note':
        return ('informational', 'No correction is justified; kept for context.')
    mode = group.get('_auto')
    if mode is None:
        mode = _fixes.availability(project, group).get('mode')
        group['_auto'] = mode == 'automatic'
    if mode == 'automatic' and group.get('priority') == 'fix':
        return ('verified_correction', 'A deterministic edit exists; the preview validates it on an isolated copy before anything is applied.')
    if mode == 'automatic' or group.get('priority') == 'fix':
        return ('conditional', 'Evidence supports a specific correction once the stated assumptions are confirmed.')
    if group.get('steps'):
        return ('conditional', 'The listed checks name a concrete correction to confirm before changing the board.')
    return ('investigation_required', 'Evidence is insufficient to recommend a modification.')


def build_review(project: Project) -> dict:
    project.circuit = build_circuit(project)
    from .checks.schematic import schematic_checks
    schematic_findings, schematic_coverage = schematic_checks(project)
    project.circuit['part_findings'].extend(schematic_findings)
    project.circuit['part_findings'].extend(pcb_pinfunction_swap_findings(project))
    project.circuit['part_findings'].extend(value_sanity_findings(project))
    project.circuit['part_findings'].extend(requirements_audit_findings(project))
    if 'requirements_audit' not in project.checks_performed:
        project.checks_performed.append('requirements_audit')
    project.circuit['schematic_checks'] = schematic_coverage
    if schematic_coverage and 'sourced_schematic_checks' not in project.checks_performed:
        project.checks_performed.append('sourced_schematic_checks')
    groups = []
    consumed = set()
    def _titled(ref, title):
        component = project.components.get(ref)
        if not component:
            return title
        label = display_name(ref, component)
        if title.startswith(f"{ref}:"):
            return label + ":" + title[len(ref) + 1:]
        if title.startswith(f"{ref} "):
            return label + " " + title[len(ref) + 1:]
        return f"{label}: {title}"

    for finding in project.circuit["part_findings"]:
        base_title = re.sub(r'^[A-Za-z0-9_#]+:\s*', '', finding.get("title", ""))
        finding = {**finding, "title": _titled(finding["reference"], finding.get("title", ""))}
        linked = []
        for index, issue in enumerate(project.issues):
            if (issue.code == "PCB_PAD_MISSING_FROM_SYMBOL" and issue.component == finding["reference"]
                    and issue.details.get("pad_number") in finding["pads"] and not issue.excluded):
                consumed.add(index)
                linked.append(issue_id(index))
        refs = [r for r in (finding.get("references") or [finding["reference"]]) if r]
        groups.append({**finding, "components": refs, "issue_ids": linked,
            "raw_count": len(linked), "item_count": len(finding["pads"]), "basis": finding.get("basis", "Sourced part rule"), "confidence": "high",
            "_merge_key": (finding.get("rule"), base_title)})
    groups = _merge_same_cause_part_groups(project, groups)
    buckets = defaultdict(list)
    for index, issue in enumerate(project.issues):
        if index in consumed:
            continue
        classification = _escalate_stub_copper(project, issue, _classification(issue))
        # Internal footprint gaps are useful per component; vias/silk are useful as a group.
        refs = tuple(_references(issue)) if issue.code in {"KICAD_DRC_CLEARANCE", "KICAD_DRC_SOLDER_MASK_BRIDGE", "PCB_PAD_MISSING_FROM_SYMBOL", "RF_FEED_NO_TERMINAL", "SENSITIVE_INPUT_ROUTE_REVIEW"} else ()
        buckets[(classification, refs)].append((index, issue))
    for (classification, grouped_refs), entries in buckets.items():
        family, category, priority, title, why, action = classification
        refs = sorted(({issue.component for _, issue in entries if issue.component and not issue.component.startswith("#")}
                       | {r for _, issue in entries for r in _references(issue) if not r.startswith("#")}), key=reference_key)
        if grouped_refs:
            if len(grouped_refs) == 1 and grouped_refs[0] in project.components:
                title += ': ' + display_name(grouped_refs[0], project.components[grouped_refs[0]])
            else:
                title += ': ' + ', '.join(grouped_refs)
        ids = {i.get('uuid') for _, issue in entries for i in issue.items if i.get('uuid')}
        examples = list(dict.fromkeys(issue.message for _, issue in entries))
        summary = examples[0]
        if family == "vias" and ids:
            summary = f"{len(ids)} vias produced {len(entries)} rule findings. " + examples[0]
        elif len(entries) > 1:
            summary = f"{len(entries)} findings. Example: {examples[0]}"
        first_signal = next((issue.details for _, issue in entries if issue.source == 'signal_path'), {})
        gap_steps = _pad_gap_steps(project, entries)
        group = {"category": category, "priority": priority, "title": title, "summary": summary,
            "why": why, "next_step": action, "components": refs,
            "issue_ids": [issue_id(i) for i, _ in entries], "raw_count": len(entries),
            "item_count": len(ids) if ids else None, "basis": "Datasheet-guided PCB geometry review" if first_signal else "Local rule interpretation",
            "confidence": "heuristic" if first_signal else "rule-dependent", "signal_path_id": first_signal.get('signal_path_id'),
            "chain_targets": [{"reference": issue.component, "pin": issue.details.get("pad_number")}
                              for _, issue in entries
                              if issue.code == "PCB_PAD_MISSING_FROM_SYMBOL" and issue.component
                              and issue.details.get("pad_number")],
            "evidence": list(dict.fromkeys(e for _, issue in entries for e in issue.evidence))[:8], "sources": first_signal.get('sources', [])}
        if gap_steps:
            group["steps"] = gap_steps
        if family == 'SENSITIVE_INPUT_ROUTE_REVIEW':
            group['no_change_basis']='These are drawn-copper measurements. Different via counts alone do not support a routing change. Datasheet and use-case analysis may identify a separate supported concern.'
        if family == 'KICAD_ERC_MULTIPLE_NET_NAMES':
            from .evidence_analysis import net_aliases
            group['net_aliases'] = net_aliases(project, group)
            if len(group['net_aliases']) == len(entries):
                group['summary'] = '; '.join(' / '.join(row['names'])+' → '+row['chosen_net'] for row in group['net_aliases'])
                group['no_change_basis'] = 'This warning reports how KiCad names a connection. It does not identify a wiring fault. Other electrical checks still apply.'
            else:
                group['priority'] = 'review'
        groups.append(group)
    _merge_identity_issues_into_part_findings(project, groups)
    _promote_implicated_pins(project, groups)
    context_data = {'issues': [asdict(i) for i in project.issues],
                    'components': {ref: {'pcb': [asdict(r) for r in c.pcb]} for ref, c in project.components.items()}}
    for group in groups:
        context = footprint_rule_context(group, context_data)
        if context:
            group.update(priority='note', reference_only=True, footprint_context=context)
    for group in groups:
        outcome, outcome_why = _finding_outcome(project, group)
        group['outcome'] = outcome
        group['outcome_why'] = outcome_why
        group.pop('_auto', None)
    for group in groups:
        # A review must never die on an unknown priority label: an unmapped
        # rule degrades to needs-review attention instead of crashing the
        # whole run after minutes of KiCad work with no report saved.
        if group.get('priority') not in ORDER:
            group['priority'] = 'review'
    groups.sort(key=lambda g: (ORDER[g['priority']], g['basis'] != 'Sourced part rule', list(CATEGORIES).index(g['category']), g['title']))
    for index, group in enumerate(groups):
        group['id'] = f"R{index + 1:03d}"
    counts = Counter(g['category'] for g in groups)
    coverage_missing = any(g['category'] == 'coverage' and any(word in project.issues[int(fid[1:])-1].code
                           for word in ('FAILED', 'NOT_PERFORMED', 'UNAVAILABLE', 'NOT_SUPPORTED', 'NOT_CONFIRMED'))
                           for g in groups for fid in g['issue_ids'])
    compared = project.connectivity.get('status') == 'compared'
    fixes = sum(g['priority'] == 'fix' for g in groups)
    reviews = sum(g['priority'] == 'review' for g in groups)
    headline = (f"{fixes} concrete discrepancy groups need attention." if fixes else
                "No confirmed connectivity discrepancy found in the checks that ran." if compared else
                "Connectivity has not been verified.")
    return {"headline": headline, "groups": groups, "categories": dict(counts), "fix_count": fixes,
            "review_count": reviews, "coverage_incomplete": coverage_missing or not compared,
            "raw_counts": dict(Counter(i.severity for i in project.issues)),
            "explanation": "A rule count is not a count of failed components. Fabrication and assembly findings depend on the selected process; AI conclusions remain interpretations.",
            "limitations": project.circuit['limits'] + ["Saved copper fills were checked; no refill or physical prototype testing."]}

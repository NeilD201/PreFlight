"""Requirements and omissions audit: support circuitry the design needs but lacks.

Complements the value-sanity checks (which flag what's wrong) by detecting
what is ABSENT or unverifiable: regulator support capacitors and their
datasheet-quoted values, per-rail decoupling gaps, USB data-line
protection, USB overcurrent protection, unsourceable value/package
combinations, and LED current-limiting that is wrong for every LED color.

Every rule here is deterministic and conditional. A missing element is
reported with the identification evidence, what a compliant design looks
like, and exactly which datasheet or standard settles the question — never
as a proven defect. Electrically equivalent implementations (a bulk
electrolytic instead of tantalum, an eFuse instead of a fuse, integrated
ESD in the connector) silence the rule without layout changes.
"""

import math
import re

from ..datasheets import package_code
from ..models import Project
from .components import _passive_value, reference_key

LDO_MPN = re.compile(r"(?i)\b(ld|ams|lm|az)1117[\w.\-]*|\b(lm|ua|mc|ka|st)78(05|09|12|15)[\w.\-]*|\blm317[\w.\-]*|\bxc62\w*|\bap2112[\w.\-]*|\bmcp17\d\d[\w.\-]*|\btps7\w*")
USB_CONNECTOR = re.compile(r"(?i)\b(edp|usb)[-_ ]?(c|a|b|micro|mini|4085)\b|usb_c|usb-c|type.?c")
ESD_TOKENS = ("esd", "tvs", "pesd", "prtr", "usblc", "usb6b", "tpd", "esd5v",
              "sr05", "sr24", "cm1293", "emi-filter")
FUSE_TOKENS = ("fuse", "ptc", "polyfuse", "polyswitch", "efuse", "mf-msmf",
               "mf-nsmf", "0zc", "miniSMDC")
SMALL_MLCC = re.compile(r"(?:^|[^0-9])(0201|0402|0603)(?:[^0-9]|$)")
BULK_TOKENS = ("tantalum", "tant", "electrolytic", "alu", "polymer", "poscap")


def _pins(component):
    """Pin number -> (function text, net), schematic first, copper fallback."""
    pins = {}
    for pin in component.schematic_pins:
        pins.setdefault(pin.number, ((pin.name or "").strip(), pin.net or ""))
    if not pins:
        for record in component.pcb:
            for pad in record.pads:
                pins.setdefault(pad.number, ((pad.name or "").strip(), pad.net or ""))
    return pins


def _haystack(component):
    parts = []
    for record in component.schematic + component.pcb:
        parts += [record.value or "", record.symbol or "", record.footprint or ""]
    return " ".join(parts).lower()


def _value(component):
    for record in component.schematic + component.pcb:
        if (record.value or "").strip():
            return record.value.strip()
    return ""


def _dnp(component):
    return any(r.dnp for r in component.schematic + component.pcb)


def _caps_on_net(project, net, exclude=()):
    """References of fitted capacitors with at least one pin on net."""
    found = []
    for ref, other in project.components.items():
        if ref in exclude or _dnp(other) or not re.match(r"(?i)^C\d", ref):
            continue
        if any(net == pin_net for _, pin_net in _pins(other).values() if pin_net):
            found.append(ref)
    return sorted(found)


def _regulator_role(pins):
    """Return (input_net, output_net) for a linear regulator, else (None, None)."""
    vin = vout = None
    for _, (fn, net) in pins.items():
        norm = fn.upper().replace("_", "").replace("-", "")
        if not net:
            continue
        if norm in ("VIN", "VI", "IN", "VCC", "VDD") and vin is None:
            vin = net
        elif norm in ("VOUT", "VO", "OUT") and vout is None:
            vout = net
    return vin, vout


def _regulator_support_findings(project):
    findings = []
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^U\d", ref):
            continue
        pins = _pins(component)
        if len(pins) < 3:
            continue
        hay = _haystack(component)
        vin, vout = _regulator_role(pins)
        if vout is None or not LDO_MPN.search(hay + " " + _value(component).lower()):
            continue
        output_caps = _caps_on_net(project, vout, exclude={ref})
        gnds = {net for _, net in pins.values() if net and net.strip().lower() in ("gnd", "gnda", "gndd", "0v")}
        location = (component.schematic[0].location if component.schematic else "")
        base_evidence = ([location] if location else []) + [
            f"{ref} output rail: {vout}; capacitors on that rail: {', '.join(output_caps) or 'none'}.",
            "Source: part marking, pin functions, and drawn copper only."]
        if not output_caps:
            findings.append({
                "reference": ref, "rule": "regulator_output_capacitor", "pads": [],
                "category": "electrical", "priority": "review",
                "title": f"{ref}: regulator output has no capacitor",
                "summary": f"{ref} ({_value(component)}) drives {vout} with no output capacitor fitted.",
                "why": ("1117-class linear regulators require output capacitance for loop stability; "
                        "without it the rail can oscillate under load steps. The exact minimum and type "
                        "come from the fitted variant's datasheet."),
                "next_step": f"Fit the datasheet-specified output capacitor from {vout} to ground, close to {ref}.",
                "steps": [f"Confirm the exact {ref} variant and read its output-capacitor requirement (value, type, ESR range).",
                          f"Fit that capacitor from {vout} to ground, close to {ref}, plus a small ceramic for high-frequency bypass.",
                          "Re-run the checks; this item should disappear."],
                "evidence": base_evidence, "sources": [],
                "chain_targets": [{"reference": ref, "pin": "", "net": vout, "name": ""}],
                "basis": "Regulator support-capacitor audit"})
        bulky = [c for c in output_caps
                 if any(t in _haystack(project.components[c]) for t in BULK_TOKENS)]
        small_ceramics = [c for c in output_caps
                          if SMALL_MLCC.search(project.components[c].pcb[0].footprint + " " +
                                               (project.components[c].schematic[0].footprint if project.components[c].schematic else "")) and not any(
                              t in _haystack(project.components[c]) for t in BULK_TOKENS)] if output_caps else []
        if output_caps and not bulky and small_ceramics:
            findings.append({
                "reference": ref, "rule": "regulator_output_capacitor_type", "pads": [],
                "category": "electrical", "priority": "review",
                "title": f"{ref}: regulator output relies on small ceramics only",
                "summary": (f"{ref} ({_value(component)}) output {vout} is decoupled only by "
                            f"{', '.join(output_caps)} with no bulk/tantalum part visible."),
                "why": ("Some 1117 variants were characterized with tantalum output capacitors and specify an "
                        "ESR stability window: ultra-low-ESR ceramics alone can ring or oscillate, and large "
                        "small-package MLCCs lose much of their capacitance under DC bias. Whether this "
                        "instance is affected depends on the exact variant, so this is a verification item, "
                        "not a proven defect."),
                "next_step": f"Fit exactly what the {ref} datasheet specifies for {vout} output capacitance (quoted in the evidence when retrieved), or give the missing part number below.",
                "steps": [f"Established: {ref} ({_value(component)}) drives {vout}; the only output capacitance is {', '.join(output_caps)} (small MLCC, no bulk).",
                          "If the evidence quotes the output-capacitor requirement, fit exactly that part (value, type, ESR range) from "
                          f"{vout} to ground near {ref}. Add bulk only when the quoted requirement calls for it — never blindly.",
                          f"Otherwise answer one question to close this item: what is the manufacturer part number of {', '.join(output_caps)}? "
                          "The DC-bias curve and ESR rating come from that datasheet; without it there is no verdict."],
                "evidence": base_evidence + [
                    "Capacitor types present: small-package MLCC only; no bulk/tantalum/electrolytic found."],
                "sources": [],
                "chain_targets": [{"reference": c, "pin": "", "net": vout, "name": ""} for c in output_caps],
                "basis": "Regulator support-capacitor audit"})
        if vin is not None and not _caps_on_net(project, vin, exclude={ref}):
            findings.append({
                "reference": ref, "rule": "regulator_input_capacitor", "pads": [],
                "category": "electrical", "priority": "review",
                "title": f"{ref}: regulator input has no local capacitor",
                "summary": f"{ref} input rail {vin} shows no local input capacitor.",
                "why": ("A local input capacitor keeps the regulator stable when the upstream supply has "
                        "inductance (long leads, connectors); 1117 application notes recommend one. "
                        "Conditional on the upstream source impedance."),
                "next_step": f"Consider a small ceramic plus bulk capacitor from {vin} to ground near {ref}.",
                "steps": [f"Check how {vin} is fed; if it comes through a connector or long run, fit input capacitance near {ref}.",
                          "Re-run the checks."],
                "evidence": ([location] if location else []) + [
                    f"{ref} input rail: {vin}; capacitors on that rail: none."],
                "sources": [],
                "chain_targets": [{"reference": ref, "pin": "", "net": vin, "name": ""}],
                "basis": "Regulator support-capacitor audit"})
    return findings


EVIDENCE_RULES = ('regulator_output_capacitor', 'regulator_output_capacitor_type',
                  'regulator_output_capacitor_value',
                  'regulator_input_capacitor', 'mlcc_package_plausibility')
_REGULATOR_QUOTE_KEYS = ('output capacitor', 'output capacitance', 'stability', 'esr',
                         'tantalum', 'bulk capacitor')
_MLCC_QUOTE_KEYS = ('dc bias', 'dc-bias', 'bias characteristic', 'rated voltage',
                    'capacitance change', 'temperature characteristic')


_VALUE_TOKEN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:µF|uF|nF|pF|mF|mV|V|A|mA|ohm|Ω|kΩ|MΩ|mm|mil|°C)\b", re.IGNORECASE)
_REQUIREMENT_WORD = re.compile(
    r"\b(required|requires|requirement|needed|needs|must|recommend(?:ed|s)?|minimum|maximum|at least|up to)\b",
    re.IGNORECASE)
_LOAD_CAP_QUOTE_KEYS = ('decoupling', 'bypass capacitor', 'bypass capacitance',
                        'bulk capacitor', 'bulk capacitance', 'supply bypass')
_FARAD_UNIT = {'pf': 1e-12, 'nf': 1e-9, 'uf': 1e-6, 'µf': 1e-6, 'μf': 1e-6, 'mf': 1e-3}
_FARAD_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*(µF|uF|nF|pF|mF)\b", re.IGNORECASE)


def _quote_passage(doc, keywords):
    """Best retrieved passage for a rule, or None when nothing was retrieved.

    Scores individual sentences so a requirement sentence carrying a value
    beats boilerplate that merely mentions a keyword. Only quotes collected
    datasheet text; never invents specifications.
    """
    best = None
    for page in doc.get('pages', []) or []:
        if isinstance(page, str):
            number, text = None, page
        else:
            number, text = page.get('page'), str(page.get('text', ''))
        if not any(key in text.lower() for key in keywords):
            continue
        for index, sentence in enumerate(re.split(r"(?<=[.!?])\s+", text)):
            lowered = sentence.lower()
            hits = sum(1 for key in keywords if key in lowered)
            if not hits:
                continue
            score = (hits,
                     1 if _VALUE_TOKEN.search(sentence) else 0,
                     1 if _REQUIREMENT_WORD.search(sentence) else 0)
            order = (0 if number is None else number, index)
            if best is None or (score, tuple(-o for o in order)) > (best[0], tuple(-o for o in best[1])):
                best = (score, order, number, sentence)
    if best is None:
        return None
    return best[2], ' '.join(best[3].split())[:400]


def _recommended_farads(sentence):
    """(display text, farads) pairs named in a quoted requirement sentence."""
    found = []
    for number, unit in _FARAD_TOKEN.findall(sentence):
        try:
            farads = float(number) * _FARAD_UNIT[unit.lower()]
        except (ValueError, KeyError):
            continue
        found.append((f"{float(number):g} {unit}", farads))
    return found


def _rail_cap_farads(project, net):
    """(reference, value text, farads|None) for fitted capacitors on net."""
    rows = []
    for ref, other in project.components.items():
        if _dnp(other) or not re.match(r"(?i)^C\d", ref):
            continue
        if net not in {pin_net for _, pin_net in _pins(other).values() if pin_net}:
            continue
        text = _value(other)
        rows.append((ref, text, _passive_value(ref, text)))
    return sorted(rows)


def _attach_load_requirements(project, group, parts, docs):
    """Compare rail capacitance against the powered ICs' own datasheets.

    For each load IC drawing the flagged rail, quote its retrieved decoupling
    requirement (when a document exists) and check whether any capacitor on
    the rail matches the recommended value within 3x. A quote without a
    parseable value is evidence only, never a verdict; a missing document
    stays a question, handled by the finding's own steps.
    """
    from .values import _rail_loads
    targets = group.get('chain_targets') or []
    member_refs = {t.get('reference') for t in targets if isinstance(t, dict)}
    nets = sorted({t.get('net') for t in targets if isinstance(t, dict) and t.get('net')})
    attached = 0
    for net in nets:
        rail = _rail_cap_farads(project, net)
        for load, number, name in _rail_loads(project, net, exclude=member_refs):
            part = parts.get(load) or {}
            if part.get('status') not in ('ready', 'provisional'):
                continue
            doc = docs.get(part.get('code'), {})
            if not doc:
                continue
            found = _quote_passage(doc, _LOAD_CAP_QUOTE_KEYS)
            if found is None:
                continue
            page, quote = found
            mpn = doc.get('mpn') or part.get('mpn') or load
            evidence = group.setdefault('evidence', [])
            line = f'{mpn} datasheet (p.{page}): "{quote}"'
            if not any(mpn in entry and quote[:60] in entry for entry in evidence):
                evidence.append(line)
                attached += 1
            sources = group.setdefault('sources', [])
            title = f'{mpn} datasheet, page {page}'
            if not any(s.get('title') == title for s in sources if isinstance(s, dict)):
                entry = {'title': title}
                if doc.get('product_url'):
                    entry['url'] = doc['product_url']
                sources.append(entry)
            recs = _recommended_farads(quote)
            if not recs:
                continue
            pin = f"pin {number}{f' {name}' if name else ''}"
            for text, farads in recs:
                covered = [ref for ref, _, rail_farads in rail
                           if rail_farads and min(farads, float(rail_farads)) / max(farads, float(rail_farads)) >= 1 / 3]
                key = f'{mpn} recommends {text} for {load}'
                if any(key in entry for entry in evidence):
                    continue
                if covered:
                    evidence.append(
                        f'Rail {net} provides the recommended ≈{text} local capacitance '
                        f"({', '.join(covered)}) — {key} ({pin}) is covered.")
                else:
                    have = ', '.join(f"{ref} ({value})" for ref, value, _ in rail) or 'no capacitors'
                    evidence.append(
                        f'Rail {net} carries {have} but no ≈{text} local capacitance. '
                        f'{key} ({pin}) — fit the recommended local value close to the load pins '
                        f'or confirm bulk-only operation is intended.')
                attached += 1
    return attached


def attach_datasheet_evidence(project):
    """Quote retrieved requirement passages into support-circuitry findings.

    Runs after datasheets are collected (design review, repair drafting).
    Findings without a retrieved document are left untouched: a missing
    document is a question, not a silent pass.
    """
    store = project.datasheets or {}
    docs = store.get('documents', {}) or {}
    parts = {p.get('reference'): p for p in store.get('parts', []) or []}
    review = project.review or {}
    attached = 0
    for group in review.get('groups', []) or []:
        rule = group.get('rule', '')
        if rule not in EVIDENCE_RULES:
            continue
        refs = list(group.get('components') or [])
        if group.get('reference') and group['reference'] not in refs:
            refs.append(group['reference'])
        keywords = _MLCC_QUOTE_KEYS if rule == 'mlcc_package_plausibility' else _REGULATOR_QUOTE_KEYS
        for ref in refs:
            part = parts.get(ref) or {}
            if part.get('status') not in ('ready', 'provisional'):
                continue
            doc = docs.get(part.get('code'), {})
            if not doc:
                continue
            found = _quote_passage(doc, keywords)
            if found is None:
                continue
            page, quote = found
            mpn = doc.get('mpn') or part.get('mpn') or ref
            line = f'{mpn} datasheet (p.{page}): "{quote}"'
            evidence = group.setdefault('evidence', [])
            if any(mpn in e and quote[:60] in e for e in evidence):
                continue
            evidence.append(line)
            sources = group.setdefault('sources', [])
            title = f'{mpn} datasheet, page {page}'
            if not any(s.get('title') == title for s in sources if isinstance(s, dict)):
                entry = {'title': title}
                if doc.get('product_url'):
                    entry['url'] = doc['product_url']
                sources.append(entry)
            attached += 1
        if rule == 'mlcc_package_plausibility':
            attached += _attach_load_requirements(project, group, parts, docs)
    return attached


def _usb_data_nets(component):
    pins = _pins(component)
    dp = {net for _, (fn, net) in pins.items()
          if net and fn.upper().replace("_", "").replace("-", "") in ("USBDP", "DP", "DPLUS", "D+")}
    dn = {net for _, (fn, net) in pins.items()
          if net and fn.upper().replace("_", "").replace("-", "") in ("USBDN", "DN", "DM", "DMINUS", "D-", "D")}
    return dp, dn


def _is_usb_connector(ref, component):
    if _dnp(component) or not re.match(r"(?i)^J\d", ref):
        return False
    pins = _pins(component)
    fns = {fn.upper().replace("_", "").replace("-", "") for _, (fn, _) in pins.items()}
    if fns & {"USBDP", "USBDN", "DP", "DM", "DPLUS", "DMINUS", "D+", "D-"}:
        return True
    return bool(USB_CONNECTOR.search(_haystack(component) + " " + _value(component).lower()))


def _usb_esd_findings(project):
    findings = []
    protectors = set()
    for ref, other in project.components.items():
        if _dnp(other):
            continue
        hay = _haystack(other)
        if any(t in hay for t in ESD_TOKENS):
            for _, net in _pins(other).values():
                if net:
                    protectors.add(net)
    for ref, component in project.components.items():
        if not _is_usb_connector(ref, component):
            continue
        dp, dn = _usb_data_nets(component)
        if not dp or not dn:
            continue
        unguarded = sorted(n for n in (dp | dn) if n not in protectors)
        if len(unguarded) != len(dp | dn):
            continue
        location = (component.schematic[0].location if component.schematic else "")
        findings.append({
            "reference": ref, "rule": "usb_data_line_protection", "pads": [],
            "category": "electrical", "priority": "review",
            "title": f"{ref}: USB data lines have no ESD protection device",
            "summary": (f"{ref} data nets {', '.join(sorted(dp | dn))} show no ESD/TVS array on either line."),
            "why": ("USB data lines that leave the enclosure are exposed to contact ESD; reference designs "
                    "and connector vendors recommend a low-capacitance TVS array on D+/D-. This is conditional: "
                    "internal-only ports, connectors with integrated protection, or a protected hub upstream "
                    "make it unnecessary."),
            "next_step": f"If {ref} leaves the product enclosure, fit a USB-rated TVS array across {', '.join(sorted(dp | dn))}.",
            "steps": [f"Decide whether {ref} is user-accessible from outside the enclosure.",
                      "If yes, fit a low-capacitance (sub-pF) USB TVS array on the data lines, close to the connector.",
                      "If the port is internal-only or already protected upstream, record that and ignore this item.",
                      "Re-run the checks."],
            "evidence": ([location] if location else []) + [
                f"{ref} D+/D- nets: {', '.join(sorted(dp | dn))}; ESD/TVS parts on those nets: none.",
                "Source: connector pin functions and drawn copper only."],
            "sources": [],
            "chain_targets": [{"reference": ref, "pin": "", "net": n, "name": ""} for n in sorted(dp | dn)],
            "basis": "USB protection requirements audit"})
    return findings


def _usb_fuse_findings(project):
    findings = []
    for ref, component in project.components.items():
        if not _is_usb_connector(ref, component):
            continue
        pins = _pins(component)
        vbus = {net for _, (fn, net) in pins.items()
                if net and fn.upper().replace("_", "").replace("-", "") in ("VBUS", "VBUS5V", "5V", "VBUSIN")}
        if not vbus:
            continue
        fused = set()
        for other_ref, other in project.components.items():
            if _dnp(other) or other_ref == ref:
                continue
            hay = _haystack(other)
            if other_ref.upper().startswith("F") or any(t in hay for t in FUSE_TOKENS):
                for _, net in _pins(other).values():
                    if net in vbus:
                        fused.add(net)
        bare = sorted(vbus - fused)
        if not bare:
            continue
        location = (component.schematic[0].location if component.schematic else "")
        findings.append({
            "reference": ref, "rule": "usb_vbus_overcurrent", "pads": [],
            "category": "electrical", "priority": "review",
            "title": f"{ref}: USB power rail has no overcurrent protection",
            "summary": f"{ref} VBUS net(s) {', '.join(bare)} show no fuse, PTC, or eFuse in series.",
            "why": ("A fault downstream of an unprotected VBUS can draw unlimited current from the host or "
                    "backpower it; USB-powered designs normally fuse VBUS or use a load switch with current "
                    "limit. This is conditional: a self-powered board that never sources VBUS, or protection "
                    "already integrated in an upstream module, does not need it. A ferrite bead alone is not "
                    "overcurrent protection."),
            "next_step": f"If this board is USB-powered or sources VBUS, fit a fuse/PTC on {', '.join(bare)}.",
            "steps": [f"Determine whether {', '.join(bare)} is powered from USB or powers anything over USB.",
                      "If yes, fit a suitably rated fuse or PTC (plus the connector's current rating) in series with VBUS.",
                      "If the board is self-powered and VBUS is unused, record that and ignore this item.",
                      "Re-run the checks."],
            "evidence": ([location] if location else []) + [
                f"{ref} VBUS nets: {', '.join(sorted(vbus))}; series fuse/PTC/eFuse found: none.",
                "Source: connector pin functions and drawn copper only."],
            "sources": [],
            "chain_targets": [{"reference": ref, "pin": "", "net": n, "name": ""} for n in bare],
            "basis": "USB protection requirements audit"})
    return findings


def _rail_gap_findings(project):
    """ICs whose SOME (not all) supply rails lack capacitors name the bare rails."""
    from .circuit import is_ground
    findings = []
    for ref, component in project.components.items():
        if _dnp(component):
            continue
        pins = [(p.number, p.net or "") for p in component.schematic_pins
                if (p.electrical_type or "") in ("power_in", "power_out")]
        if len({n for _, n in pins if n}) < 2:
            continue
        haystack = " ".join([_value(component)] + [r.symbol or "" for r in component.schematic]).lower()
        if any(token in haystack for token in ("module", "wroom", "devkit", "pico")):
            continue
        rails = sorted({net for _, net in pins if net and not is_ground(net)})
        if len(rails) < 2:
            continue
        bare = [net for net in rails if not _caps_on_net(project, net, exclude={ref})]
        if not bare or len(bare) == len(rails):
            continue
        location = (component.schematic[0].location if component.schematic else "")
        findings.append({
            "reference": ref, "rule": "ic_decoupling_rail_gap", "pads": [],
            "category": "electrical", "priority": "review",
            "title": f"{ref}: some supply rails lack decoupling",
            "summary": (f"{ref} rails {', '.join(rails)}: {', '.join(bare)} "
                        f"{'has' if len(bare) == 1 else 'have'} no capacitor while the other rails do."),
            "why": ("A partially bypassed IC can still ring on the unbypassed rail; the covered rails do not "
                    "protect the bare ones. Conditional on the rail actually powering switching logic "
                    "(analog/reference rails sometimes intentionally share bulk capacitance)."),
            "next_step": f"Fit 100 nF from each bare rail ({', '.join(bare)}) to ground near {ref}, unless the rail is intentionally tied to a shared bulk rail.",
            "steps": [f"Check what {', '.join(bare)} powers inside {ref}.",
                      "Fit 100 nF ceramics to ground near the pins unless the rail is intentionally shared.",
                      "Re-run the checks."],
            "evidence": ([location] if location else []) + [
                f"{ref} supply rails: {', '.join(rails)}; rails with no capacitor: {', '.join(bare)}."],
            "sources": [],
            "chain_targets": [{"reference": ref, "pin": "", "net": n, "name": ""} for n in bare],
            "basis": "IC decoupling-rail-gap audit"})
    return findings


def _part_statuses(project):
    store = project.datasheets or {}
    return {p.get('reference'): p for p in store.get('parts', []) or []}


def _sourcing_anomaly_findings(project):
    """Capacitors whose complete value+package specs match no catalog part.

    Fires only when the value parses, a footprint is present, and matching
    actually ran (status unmatched). Missing specs are a question, not this.
    """
    statuses = _part_statuses(project)
    if not statuses:
        return []
    by_spec = {}
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^C\d", ref):
            continue
        value = _value(component)
        if _passive_value(ref, value) is None:
            continue
        footprints = {r.footprint for r in component.schematic + component.pcb if r.footprint}
        if not footprints:
            continue
        part = statuses.get(ref)
        if not part or part.get('status') != 'unmatched':
            continue
        package = next((package_code(f) for f in sorted(footprints) if package_code(f)), None)
        by_spec.setdefault((value, package, tuple(sorted(footprints))), []).append(ref)
    findings = []
    ordered = sorted(by_spec.items(), key=lambda kv: (kv[0][0], kv[0][1] or '', kv[0][2]))
    for (value, package, footprints), refs in ordered:
        names = sorted(refs, key=reference_key)
        if package is None:
            why = (f"No catalog package mapping exists for footprint {footprints[0]}; "
                   "the part could not even be searched. Either the footprint is unusual "
                   "or the value/package combination is not stocked.")
            next_step = (f"Give the exact manufacturer part number of {', '.join(names)}; "
                         "without it neither the value nor the package can be verified.")
        else:
            why = (f"No LCSC candidate exists for {value} in {package}; the value/package "
                   "combination is likely wrong (a 100µF MLCC does not fit a 0402 body) "
                   "or the part is exotic/unobtainable.")
            next_step = (f"Correct the value or package of {', '.join(names)}, or give the exact "
                         "manufacturer part number if the combination is intentional.")
        findings.append({
            "reference": names[0], "rule": "mlcc_package_plausibility", "pads": [],
            "category": "electrical", "priority": "review",
            "title": f"{', '.join(names)}: {value} in {footprints[0]} has no catalog candidate",
            "summary": (f"{', '.join(names)} ({value}, {', '.join(footprints)}) matched no LCSC part."),
            "why": why,
            "next_step": next_step,
            "steps": [f"Established: {', '.join(names)} are {value} in {', '.join(footprints)} with no catalog match.",
                      next_step,
                      "Re-run the checks; this item disappears once the parts match."],
            "evidence": [f"{', '.join(names)}: value {value}, footprint {', '.join(footprints)}, LCSC status unmatched.",
                         "Source: schematic value, PCB footprint, and catalog search only."],
            "sources": [],
            "chain_targets": [{"reference": r, "pin": "", "net": "", "name": ""} for r in names],
            "basis": "Sourcing plausibility audit"})
    return findings


_RAIL_VOLTS = ((r"5V|VBUS", 5.0), (r"3[._]?3V\b|3V3|GPIO|MCU", 3.3), (r"1[._]?8V\b|1V8", 1.8))


def _rail_volts(net):
    text = (net or "").upper()
    for pattern, volts in _RAIL_VOLTS:
        if re.search(pattern, text):
            return volts
    return None


_E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)


def _nearest_e12(ohms):
    if ohms <= 0:
        return None
    decade = 10 ** math.floor(math.log10(ohms))
    mantissa = ohms / decade
    nearest = min(_E12, key=lambda step: abs(step - mantissa))
    return nearest * decade


_LED_VF_LO, _LED_VF_HI = 1.8, 3.4
_LED_DIM_MA, _LED_HOT_MA = 0.5, 25.0


def _led_resistor_findings(project):
    """LED current-limiting that is wrong for every LED color.

    The LED's own color is often unassigned, so no single Vf can be assumed.
    The rule only fires when the current is out of bounds across the whole
    plausible Vf range (dim red through blue) — otherwise it stays silent
    instead of demanding the color.
    """
    findings = []
    resistors = {}
    for rref, resistor in project.components.items():
        if _dnp(resistor) or not re.match(r"(?i)^R\d", rref):
            continue
        ohms = _passive_value(rref, _value(resistor))
        if ohms is None or ohms <= 0:
            continue
        nets = {net for _, net in _pins(resistor).values() if net}
        if len(nets) == 2:
            resistors[rref] = (float(ohms), nets)
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^D\d", ref):
            continue
        if "led" not in _haystack(component):
            continue
        pins = _pins(component)
        if len(pins) != 2:
            continue
        nets = {net for _, net in pins.values() if net}
        if len(nets) != 2:
            continue
        for rref, (ohms, rnets) in sorted(resistors.items()):
            shared = rnets & nets
            if len(shared) != 1:
                continue
            supply = next(iter(rnets - shared))
            volts = _rail_volts(supply)
            if volts is None or volts <= _LED_VF_LO:
                continue
            i_bright = max(0.0, (volts - _LED_VF_LO)) / ohms
            i_dim = max(0.0, (volts - _LED_VF_HI)) / ohms
            location = (component.schematic[0].location if component.schematic else "")
            measured = (f"{ref} via {rref} ({_value(project.components[rref])}) from {supply} "
                        f"({volts:g} V assumed from the net name): "
                        f"{i_dim * 1000:.2g}–{i_bright * 1000:.2g} mA across Vf 1.8–3.4 V.")
            if i_bright * 1000 < _LED_DIM_MA:
                target = (volts - 2.1) / 0.002
                suggestion = _nearest_e12(target)
                findings.append({
                    "reference": ref, "rule": "led_current_limiting", "pads": [],
                    "category": "electrical", "priority": "review",
                    "title": f"{ref}: indicator LED current is too low for any LED color",
                    "summary": measured + " The LED will be dim or dark whether it is red or blue.",
                    "why": ("The series resistor sets less than 0.5 mA even for a low-Vf red LED; "
                            "indicator LEDs are normally run at 1–5 mA. No color assignment can fix this."),
                    "next_step": f"Change {rref} to {suggestion:g} Ω (≈{(volts - 2.1) / suggestion * 1000:.1f} mA at Vf 2.1 V).",
                    "steps": [f"Established: {measured}",
                              f"Fit {suggestion:g} Ω for {rref} and re-run the checks; this item should disappear.",
                              "If unusual brightness is intended, record the target current and ignore this item."],
                    "evidence": ([location] if location else []) + [measured,
                        "Source: drawn copper, resistor value, and net-name rail voltage only."],
                    "sources": [],
                    "chain_targets": [{"reference": rref, "pin": "", "net": next(iter(shared)), "name": ""}],
                    "basis": "LED current-limiting audit"})
                break
            if i_dim * 1000 > _LED_HOT_MA:
                findings.append({
                    "reference": ref, "rule": "led_current_limiting", "pads": [],
                    "category": "electrical", "priority": "review",
                    "title": f"{ref}: indicator LED current is excessive for any LED color",
                    "summary": measured + " The LED is overdriven even at blue Vf.",
                    "why": ("The series resistor lets more than 25 mA flow even for a high-Vf blue LED; "
                            "no color assignment makes this safe for an indicator."),
                    "next_step": f"Raise {rref} so the current at the lowest plausible Vf stays under 20 mA.",
                    "steps": [f"Established: {measured}",
                              f"Raise {rref} and re-run the checks; this item should disappear."],
                    "evidence": ([location] if location else []) + [measured,
                        "Source: drawn copper, resistor value, and net-name rail voltage only."],
                    "sources": [],
                    "chain_targets": [{"reference": rref, "pin": "", "net": next(iter(shared)), "name": ""}],
                    "basis": "LED current-limiting audit"})
                break
    return findings


_MIN_UF = re.compile(r"(\d+(?:\.\d+)?)\s*[uµμ]F\s+(?:minimum|min\b)"
                     r"|(?:minimum|min(?:imum)?|at least)[^.?!;]{0,80}?(\d+(?:\.\d+)?)\s*[uµμ]F",
                     re.IGNORECASE)


def _required_min_uf(doc):
    """(page, microfarads, sentence) for a quoted output-capacitor minimum."""
    found = _quote_passage(doc, _REGULATOR_QUOTE_KEYS)
    if not found:
        return None
    page, sentence = found
    match = _MIN_UF.search(sentence)
    if not match:
        return None
    return page, float(match.group(1) or match.group(2)), sentence


def _regulator_value_findings(project):
    """Datasheet-quoted output-capacitance minimum versus fitted capacitors.

    Uses only retrieved regulator documents: the quoted minimum, the nominal
    fitted total, and whether solid (non-MLCC) capacitance covers it. MLCC
    effective capacitance under DC bias cannot be shown from nominal values,
    so an MLCC-only output counts as unproven, never as compliant.
    """
    findings = []
    store = project.datasheets or {}
    docs = store.get('documents', {}) or {}
    parts = _part_statuses(project)
    if not docs:
        return []
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^U\d", ref):
            continue
        pins = _pins(component)
        if len(pins) < 3:
            continue
        hay = _haystack(component)
        vin, vout = _regulator_role(pins)
        if vout is None or not LDO_MPN.search(hay + " " + _value(component).lower()):
            continue
        output_caps = _caps_on_net(project, vout, exclude={ref})
        if not output_caps:
            continue
        part = parts.get(ref) or {}
        doc = docs.get(part.get('code') or "") or {}
        if not doc:
            continue
        required = _required_min_uf(doc)
        if required is None:
            continue
        nominal = 0.0
        solid = 0.0
        unknown = []
        for cap in output_caps:
            amount = _passive_value(cap, _value(project.components[cap]))
            if amount is None:
                unknown.append(cap)
                continue
            microfarads = float(amount) * 1e6
            nominal += microfarads
            cap_hay = _haystack(project.components[cap])
            if any(t in cap_hay for t in BULK_TOKENS) and (parts.get(cap) or {}).get('status') in ('ready', 'provisional'):
                solid += microfarads
        page, minimum, _sentence = required
        if nominal < minimum:
            findings.append({
                "reference": ref, "rule": "regulator_output_capacitor_value", "pads": [],
                "category": "electrical", "priority": "review",
                "title": f"{ref}: output capacitance below the datasheet minimum",
                "summary": (f"{ref} drives {vout} with {nominal:g} µF nominal fitted; "
                            f"the retrieved document requires at least {minimum:g} µF for stability."),
                "why": ("Loop stability needs the quoted minimum on the output; less "
                        "capacitance risks oscillation under load steps."),
                "next_step": f"Fit at least {minimum:g} µF from {vout} to ground, close to {ref}.",
                "steps": [f"Established: {', '.join(output_caps)} total {nominal:g} µF nominal on {vout}; requirement {minimum:g} µF.",
                          f"Fit at least {minimum:g} µF from {vout} to ground near {ref}.",
                          "Re-run the checks; this item should disappear."],
                "evidence": [f"{ref} output rail: {vout}; fitted output capacitance: {', '.join(output_caps)} ({nominal:g} µF nominal).",
                             "Source: drawn copper and schematic values only. Quote attached from the retrieved document."],
                "sources": [],
                "chain_targets": [{"reference": c, "pin": "", "net": vout, "name": ""} for c in output_caps],
                "basis": "Regulator support-capacitor audit"})
        elif solid < minimum:
            unverified = sorted(set(output_caps + unknown))
            findings.append({
                "reference": ref, "rule": "regulator_output_capacitor_value", "pads": [],
                "category": "electrical", "priority": "review",
                "title": f"{ref}: output capacitance effective value unproven",
                "summary": (f"{ref} drives {vout} with {nominal:g} µF nominal, meeting the quoted "
                            f"{minimum:g} µF minimum on paper — but only MLCC capacitance counts, "
                            "and MLCC effective value under DC bias is unproven here."),
                "why": ("Small-package MLCCs lose much of their capacitance under DC bias, so a "
                        "nominal total at or above the minimum does not prove the effective total "
                        "meets it. The retrieved document sets the minimum; the fitted parts' "
                        "bias curves or measurements settle compliance."),
                "next_step": (f"Show that {', '.join(unverified)} hold at least {minimum:g} µF effective "
                              f"at the {vout} rail voltage (dielectric/bias curve or measurement), "
                              "or fit the quoted bulk capacitance."),
                "steps": [f"Established: {', '.join(output_caps)} total {nominal:g} µF nominal on {vout}; requirement {minimum:g} µF; no verified bulk part.",
                          f"Check the dielectric and rated voltage of {', '.join(unverified)} against their bias curves at the rail voltage.",
                          f"Fit the quoted bulk capacitance from {vout} to ground near {ref} if the effective total cannot be shown.",
                          "Re-run the checks; this item should disappear."],
                "evidence": [f"{ref} output rail: {vout}; fitted: {', '.join(output_caps)} ({nominal:g} µF nominal, MLCC only).",
                             "Source: drawn copper, schematic values, and catalog match statuses only. Quote attached from the retrieved document."],
                "sources": [],
                "chain_targets": [{"reference": c, "pin": "", "net": vout, "name": ""} for c in unverified],
                "basis": "Regulator support-capacitor audit"})
    return findings


def requirements_audit_findings(project: Project) -> list[dict]:
    """Omissions audit: missing support circuitry, each item conditional and evidenced."""
    valued_findings = _regulator_value_findings(project)
    valued = {f["reference"] for f in valued_findings}
    support = [f for f in _regulator_support_findings(project)
               if not (f["rule"] == "regulator_output_capacitor_type" and f["reference"] in valued)]
    return (support + valued_findings
            + _sourcing_anomaly_findings(project) + _led_resistor_findings(project)
            + _usb_esd_findings(project) + _usb_fuse_findings(project) + _rail_gap_findings(project))

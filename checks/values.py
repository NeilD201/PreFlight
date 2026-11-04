"""Evidence-based component value and package sanity checks (no datasheet needed).

Every rule here rests on a board-visible standard or physical plausibility
bound, never on a guess about design intent:

- USB-C Sink ports must present Rd 5.1k (+/-10%) from CC1/CC2 to ground;
  without it the source supplies nothing (USB Type-C specification).
- An indicator LED behind several kilo-ohms of series resistance is starved
  below ~1 mA on any logic supply; brightness must be verified.
- A high-capacitance MLCC in a small chip package on a supply rail needs its
  voltage rating and DC-bias derating verified, not assumed.
- A multi-pin IC with supply pins but zero decoupling capacitors on those
  nets is missing the charge reservoir digital switching needs; modules with
  integrated decoupling are exempted by explicit marking, not by assumption.

Regulator output-capacitor stability windows and ESD/fuse placement are NOT
checked here: they depend on the exact part and product context, and inventing
a requirement would be worse than reporting the gap.
"""

import re
from collections import defaultdict
from decimal import Decimal

from ..models import Project
from .circuit import is_ground
from .components import _passive_value

SUPPLY_NET = re.compile(r"(VCC|VDD|VBUS|VBAT|VIN|VOUT|V5|V3|3V3|5V0|PWR|VSYS|VREG|VDDIO|VCCA|VDDA|\+)", re.I)
CHIP_PACKAGE = re.compile(r"(?:^|[^0-9])(0201|0402|0603|0805|1206|1210)(?:[^0-9]|$)")
MODULE_TOKENS = ("module", "wroom", "wrover", "devkit", "pico", "nano", "feather", "stamp")


def _records(component):
    return component.schematic or component.pcb


def _value(component):
    for record in _records(component):
        if (record.value or "").strip():
            return record.value.strip()
    return ""


def _pin_functions(component):
    """Pin number -> (function text, net) from schematic pins, else copper."""
    pins = {}
    for pin in component.schematic_pins:
        pins.setdefault(pin.number, ((pin.name or "").strip(), pin.net or ""))
    if not pins:
        for record in component.pcb:
            for pad in record.pads:
                pins.setdefault(pad.number, ((pad.name or "").strip(), pad.net or ""))
    return pins


def _dnp(component):
    return any(r.dnp for r in component.schematic + component.pcb)


def _ohms(value: Decimal) -> str:
    if value >= 1000 and value % 1000 == 0:
        return f"{float(value / 1000):g}k"
    return f"{float(value):g}"


def _usb_cc_findings(project):
    findings = []
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^[A-Z]+\d", ref):
            continue
        pins = _pin_functions(component)
        cc_pins = {number: net for number, (fn, net) in pins.items()
                   if fn.upper().replace("_", "") in ("CC1", "CC2") and net}
        if len(cc_pins) < 2:
            continue
        problems = []
        for number, net in sorted(cc_pins.items()):
            resistors = []
            for other_ref, other in project.components.items():
                if _dnp(other) or not re.match(r"(?i)^R\d", other_ref):
                    continue
                value = _passive_value(other_ref, _value(other))
                if value is None:
                    continue
                other_pins = _pin_functions(other)
                nets = [n for _, n in other_pins.values()]
                if net in nets and any(is_ground(n) for n in nets):
                    resistors.append((other_ref, value))
            if not resistors:
                problems.append(f"CC pin {number} (net {net}) has no resistor to ground")
            elif not any(Decimal("4590") <= value <= Decimal("5610") for _, value in resistors):
                problems.append(f"CC pin {number} (net {net}) uses " +
                                ", ".join(f"{r} ({_ohms(v)} ohms)" for r, v in resistors) +
                                " instead of the required 5.1k pull-down")
        if problems:
            location = (_records(component)[0].location if _records(component) else "")
            detail = "; ".join(problems)
            findings.append({
                "reference": ref, "rule": "usb_c_cc_pull_down", "pads": sorted(cc_pins),
                "category": "electrical", "priority": "review",
                "title": f"{ref}: USB-C CC resistors missing or out of spec",
                "summary": f"{ref} exposes USB-C CC1/CC2 pins, but {detail}.",
                "why": ("A USB-C sink must present Rd 5.1k (+/-10%) from each CC pin to ground; "
                        "without it the source detects nothing and supplies no power. This follows "
                        "from the connector pin-functions and the drawn copper, not from intent."),
                "next_step": f"Fit 5.1k resistors from {ref} CC1/CC2 to ground: {detail}.",
                "steps": [f"On {ref}, confirm pins {', '.join(sorted(cc_pins))} are the USB-C CC lines: {detail}.",
                          "Fit a 5.1k resistor (1% or 5%) from each CC line to ground, close to the connector.",
                          "Run KiCad's board check and this preflight again; this warning should disappear."],
                "evidence": ([location] if location else []) + [detail + ".",
                    "Source: footprint pin-functions and drawn copper only; no datasheet was used."],
                "sources": [],
                "chain_targets": [{"reference": ref, "pin": n, "net": cc_pins[n],
                                   "name": pins[n][0]} for n in cc_pins],
                "basis": "USB-C receptacle pin-function and copper check"})
    return findings


def _led_dim_findings(project):
    findings = []
    leds = {}
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^D\d", ref):
            continue
        haystack = " ".join([_value(component)] + [r.symbol or "" for r in _records(component)] +
                            [r.footprint or "" for r in _records(component)]).lower()
        if "led" not in haystack:
            continue
        for number, (fn, net) in _pin_functions(component).items():
            if net:
                leds.setdefault(net, []).append((ref, number))
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^R\d", ref):
            continue
        value = _passive_value(ref, _value(component))
        if value is None or value < Decimal("4700"):
            continue
        nets = [n for _, n in _pin_functions(component).values()]
        # Ground and supply rails do not make a series connection: nearly every
        # pull-down shares GND with every LED VSS pin.
        shared = sorted({n for n in nets if n in leds and not is_ground(n)
                         and not SUPPLY_NET.search(n)})
        if not shared:
            continue
        others = "; ".join(f"{r} pin {n}" for net in shared for r, n in leds[net])
        location = (_records(component)[0].location if _records(component) else "")
        drive = sorted({n for n in nets if n not in shared and n and not n.startswith('unconnected-')
                        and not is_ground(n) and not SUPPLY_NET.search(n)})
        gpio_drive = next((n for n in drive if 'gpio' in n.lower()), '')
        steps = [f"Check what drives net {shared[0]} and confirm the intended LED current for {others}."]
        if gpio_drive and value:
            # Screening numbers only: GPIO level and LED drop are stated
            # assumptions, not measured facts. The formula stays valid when the
            # user substitutes the measured rail and the LED's actual Vf.
            current_ma = float(Decimal('1.3') / value * 1000)
            target_r = float(Decimal('1.3') / Decimal('0.002'))
            e12 = [100, 120, 150, 180, 220, 270, 330, 390, 470, 560, 680, 820, 1000,
                   1200, 1500, 1800, 2200, 2700, 3300, 3900, 4700, 5600, 6800, 8200]
            nearest = min(e12, key=lambda r: (abs(r - target_r), r))
            steps.append(f"Measure the logic voltage on {gpio_drive} (typically 3.3 V on MCU GPIOs). "
                         f"At 3.3 V with a typical 2 V LED drop this {ref} passes about (3.3-2.0)/{_ohms(value)} = {current_ma:.2f} mA: "
                         "a faint glow at best. For about 2 mA use about "
                         f"(3.3-2.0)/0.002 = {target_r:.0f} ohms (nearest standard {nearest} ohms), scaled to the measured rail "
                         "and the LED's actual forward voltage, staying within the driver's rated source current.")
        else:
            steps.append(f"Size {ref} for that current (typical indicators: 2-20 mA) and update the board.")
        steps.append("Run KiCad's board check and this preflight again.")
        findings.append({
            "reference": ref, "rule": "led_series_resistance", "pads": [],
            "category": "electrical", "priority": "review",
            "title": f"{ref}: LED series resistance may starve the LED",
            "summary": (f"{ref} ({_ohms(value)} ohms) shares net {shared[0]} with {others}, "
                        "implying under ~1 mA on logic supplies."),
            "why": ("Several kilo-ohms in series with an indicator LED leaves under ~1 mA on any "
                    "logic supply, which most indicator LEDs cannot turn into useful light. "
                    "The sharing is drawn copper; the brightness conclusion needs the drive level verified."),
            "next_step": (f"Verify {ref} ({_ohms(value)} ohms) against the intended LED current and supply." +
                          (f" Screening estimate on {gpio_drive}: about {current_ma:.2f} mA at 3.3 V logic." if gpio_drive and value else "")),
            "steps": steps,
            "evidence": ([location] if location else []) +
                        [f"{ref} ({_ohms(value)} ohms) shares {shared[0]} with {others}.",
                         "Source: component values and drawn copper only."],
            "sources": [],
            "chain_targets": [{"reference": r, "pin": n, "net": net, "name": ""}
                              for net in shared for r, n in leds[net]],
            "basis": "LED series-resistance plausibility check"})
    return findings


def _rail_volts(net):
    """Parse '+5V' or '+3V3' style rail names to volts, else None."""
    match = re.fullmatch(r'\+?(\d+)(?:[Vv](\d*))?', (net or '').strip())
    if not match:
        return None
    whole, frac = match.group(1), match.group(2) or ''
    try:
        return float(whole + '.' + frac) if frac else float(whole)
    except ValueError:
        return None


def _min_rating(volts):
    """Smallest standard MLCC rating with 2x screening margin, else None."""
    for rating in (4, 6.3, 10, 16, 25, 50):
        if rating >= 2 * volts:
            return rating
    return None


def _rail_suppliers(project, net):
    """References of regulators/converters tied to the net (regulator context).

    Only parts with both an input and an output power pin qualify, so loads
    with a power input (LEDs, logic ICs) do not flood the context.
    """
    suppliers = []
    for ref, component in project.components.items():
        pins = list(component.schematic_pins)
        if not any(p.net == net and p.electrical_type in ("power_in", "power_out") for p in pins):
            continue
        types = {p.electrical_type for p in pins}
        if {"power_in", "power_out"} <= types:
            suppliers.append(ref)
    return sorted(suppliers)


def _rail_loads(project, net, exclude=()):
    """(reference, pin number, pin name) drawing this rail through a power_in pin.

    Suppliers, passives, and the capacitors under review are not loads: the
    question is which IC actually depends on the rail, so the investigation
    can check that IC's datasheet instead of giving generic rating advice.
    """
    excluded = set(exclude)
    loads = []
    for ref, component in project.components.items():
        if ref in excluded or _dnp(component):
            continue
        if re.match(r"(?i)^[CRLFP]", ref):
            continue
        pins = list(component.schematic_pins)
        if any(p.net == net and p.electrical_type == "power_out" for p in pins):
            continue
        for pin in pins:
            if pin.net == net and pin.electrical_type == "power_in":
                loads.append((ref, pin.number, (pin.name or "").strip()))
    return sorted(loads)


def _group_rail_loads(project, loads):
    """Collapse identical parts on a rail to one line per part type.

    A rail feeding 54 identical LEDs names the part type once (first ref plus
    count) instead of emitting 54 duplicate questions.
    """
    grouped = {}
    for ref, number, name in loads:
        component = project.components.get(ref)
        identity = ""
        if component is not None:
            records = _records(component)
            identity = " ".join(text for text in (
                _value(component), records[0].footprint if records else "") if text).strip()
        key = (identity, number, name)
        grouped.setdefault(key, []).append(ref)
    ordered = []
    for (identity, number, name), refs in sorted(grouped.items(), key=lambda item: item[1][0]):
        pin = f"pin {number}{f' {name}' if name else ''}"
        if len(refs) == 1:
            ordered.append((refs[0], f"{refs[0]} ({pin})", identity))
        else:
            ordered.append((refs[0], f"{refs[0]} (+{len(refs) - 1} same parts, {pin})", identity))
    return ordered


def _rail_cap_roles(project, net, exclude=()):
    """(reference, value text, farads|None) for fitted capacitors on net."""
    excluded = set(exclude)
    rows = []
    for ref, component in project.components.items():
        if ref in excluded or _dnp(component) or not re.match(r"(?i)^C\d", ref):
            continue
        if net not in {net_or_empty for _, net_or_empty in _pin_functions(component).values()}:
            continue
        rows.append((ref, _value(component), _passive_value(ref, _value(component))))
    return sorted(rows)


def _mlcc_plausibility_findings(project):
    candidates = []
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^C\d", ref):
            continue
        value = _passive_value(ref, _value(component))
        if value is None or value < Decimal("0.000047"):
            continue
        footprint = " ".join(r.footprint or "" for r in _records(component))
        match = CHIP_PACKAGE.search(footprint)
        if not match or match.group(1) not in ("0201", "0402", "0603"):
            continue
        nets = [n for _, n in _pin_functions(component).values()]
        supply = sorted({n for n in nets if n and SUPPLY_NET.search(n)})
        if not supply:
            continue
        candidates.append((ref, component, value, match.group(1), supply[0]))
    groups = {}
    for ref, component, value, package, net in candidates:
        groups.setdefault((value, package), []).append((ref, component, net))
    findings = []
    for (value, package), members in groups.items():
        refs = sorted(ref for ref, _, _ in members)
        summary_lines, evidence_lines = [], []
        group_loads = []
        for ref, component, net in members:
            suppliers = _rail_suppliers(project, net)
            loads = _rail_loads(project, net, exclude={r for r, _, _ in members})
            group_loads.extend((net, load) for load in loads)
            line = (f"{ref} ({_value(component)}) on {net}" +
                    (f" (power pin of {', '.join(suppliers)})" if suppliers else ""))
            if loads:
                line += "; powers " + ", ".join(
                    f"{display}{f' [{identity}]' if identity else ''}"
                    for _, display, identity in _group_rail_loads(project, loads))
            else:
                line += "; no IC power pin found on this net"
            summary_lines.append(line)
            location = (_records(component)[0].location if _records(component) else "")
            evidence_lines.append(f"{location}; {ref} ({_value(component)}, {package}) on {net}.")
            siblings = [row for row in _rail_cap_roles(project, net, exclude={r for r, _, _ in members})]
            if siblings:
                parsed = sorted([row for row in siblings if row[2] is not None], key=lambda row: row[2])
                described = []
                for sibling, text, _ in siblings:
                    role = ""
                    if len(parsed) > 1:
                        if sibling == parsed[-1][0]:
                            role = ", largest on rail (bulk role unconfirmed)"
                        elif sibling == parsed[0][0]:
                            role = ", smallest on rail (local-decoupling role unconfirmed)"
                    described.append(f"{sibling} ({text}{role})")
                evidence_lines.append(f"other capacitors on {net}: " + "; ".join(described) + ".")
        label = refs[0] if len(refs) == 1 else f"{refs[0]} (+{len(refs) - 1} same-value part)"
        by_net = {}
        for net, load in group_loads:
            by_net.setdefault(net, []).append(load)
        load_notes = []
        for net in sorted(by_net):
            for _, display, identity in _group_rail_loads(project, sorted(set(by_net[net]))):
                what = f"this part type{f' ({identity})' if identity else ''}"
                load_notes.append(
                    f"{display} draws from {net}: "
                    f"the shared datasheet decoupling requirement is unverified — give {what}'s exact part number "
                    f"or the requirement stays open.")
        ratings = []
        for _, _, net in members:
            volts = _rail_volts(net)
            minimum = _min_rating(volts) if volts else None
            if volts and minimum:
                ratings.append(f"On {net} ({volts:g} V): use a part rated at least {minimum:g} V (2x screening margin), "
                               f"then confirm the DC-bias curve still leaves enough capacitance at {volts:g} V.")
        findings.append({
            "reference": refs[0], "references": refs, "rule": "mlcc_package_plausibility", "pads": [],
            "category": "electrical", "priority": "review",
            "title": f"{label}: give the capacitor part number or fit a curve-verified part",
            "summary": (f"{label} ({_value(members[0][1])} in {package}) on "
                        f"{len(members)} supply rail(s): " + "; ".join(summary_lines) + "."),
            "why": ("High-capacitance MLCCs in small packages lose much of their capacitance under DC "
                    "bias and need a voltage rating above the rail with margin. The value, package, and "
                    "net names are board facts; the rating conclusion needs the part data verified. "
                    "No replacement part number is given: only a datasheet-verified part may be specified."),
            "next_step": f"Give the capacitor part number, or fit a datasheet-verified part whose DC-bias curve holds enough capacitance at the rail.",
            "steps": (ratings if ratings else
                      ["Reject any part whose rated voltage is below twice the rail voltage."]) + load_notes + [
                      "Do not upsize the package or add bulk capacitance until a DC-bias curve shows the effective value is actually insufficient.",
                      f"One question closes this item: what is the manufacturer part number of {', '.join(refs)}? The DC-bias curve and ESR rating come from that datasheet — without it there is no verdict, only this screen."],
            "evidence": evidence_lines +
                        ["Source: component values, footprints, and net names only."],
            "sources": [],
            "chain_targets": [{"reference": ref, "pin": "", "net": net, "name": ""}
                              for ref, _, net in members],
            "basis": "MLCC package-versus-capacitance plausibility check"})
    return findings


def _decoupling_findings(project):
    findings = []
    supply_caps = defaultdict(set)
    for ref, component in project.components.items():
        if _dnp(component) or not re.match(r"(?i)^C\d", ref):
            continue
        for _, net in _pin_functions(component).values():
            if net:
                supply_caps[net].add(ref)
    for ref, component in project.components.items():
        if _dnp(component):
            continue
        pins = [(p.number, p.name or "", p.electrical_type or "", p.net or "")
                for p in component.schematic_pins]
        if len(pins) < 8:
            continue
        if not any(t in ("power_in", "power_out") for _, _, t, _ in pins):
            continue
        haystack = " ".join([_value(component)] + [r.symbol or "" for r in component.schematic] +
                            [r.footprint or "" for r in component.schematic]).lower()
        if any(token in haystack for token in MODULE_TOKENS):
            continue
        rails = sorted({net for _, _, etype, net in pins
                        if etype in ("power_in", "power_out") and net and not is_ground(net)})
        if not rails:
            continue
        bare = [net for net in rails if not supply_caps.get(net)]
        if len(bare) != len(rails):
            continue
        location = (component.schematic[0].location if component.schematic else "")
        findings.append({
            "reference": ref, "rule": "ic_decoupling_presence", "pads": [],
            "category": "electrical", "priority": "review",
            "title": f"{ref}: no decoupling capacitors visible on supply rails",
            "summary": (f"{ref} has supply rails {', '.join(rails)} with no capacitors on any of them."),
            "why": ("Switching digital ICs need local charge reservoirs on every supply rail; a complete "
                    "absence on all rails is a design gap unless the part integrates them. Modules with "
                    "integrated decoupling are exempted by marking; anything else needs confirmation."),
            "next_step": f"Confirm {ref} decoupling or fit capacitors on {', '.join(rails)}.",
            "steps": [f"If {ref} is a module with integrated decoupling, record that and ignore this item.",
                      f"Otherwise fit a small ceramic capacitor (typically 100 nF) from each of {', '.join(rails)} to ground, close to the pins, plus bulk where the design needs it.",
                      "Update the schematic and PCB, then run the checks again."],
            "evidence": ([location] if location else []) +
                        [f"{ref} supply rails: {', '.join(rails)}; capacitors found: none.",
                         "Source: schematic pin types and drawn copper only."],
            "sources": [],
            "chain_targets": [{"reference": ref, "pin": "", "net": net, "name": ""} for net in rails],
            "basis": "IC decoupling-presence check"})
    return findings


def value_sanity_findings(project: Project) -> list[dict]:
    """Standard-backed value/package checks; see module docstring for scope."""
    return (_usb_cc_findings(project) + _led_dim_findings(project)
            + _mlcc_plausibility_findings(project) + _decoupling_findings(project))

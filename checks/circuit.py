"""Circuit facts and narrowly scoped, sourced part rules. No generic decoupling errors."""

from collections import defaultdict
from decimal import Decimal
import re

from .components import _passive_value, reference_key
from .connectivity import _name
from ..models import Project


MCP_SOURCE = {
    "title": "Microchip MCP73831/2, DS20001984H, Table 3-1 / §3.6 / current regulation",
    "url": "https://ww1.microchip.com/downloads/aemDocuments/documents/APID/ProductDocuments/DataSheets/MCP73831-Family-Data-Sheet-DS20001984H.pdf#page=11",
    "checked": "2026-09-30",
}
MAX_SOURCE = {
    "title": "Analog Devices MAX30102, pin description, page 8",
    "url": "https://www.analog.com/media/en/technical-documentation/data-sheets/MAX30102.pdf#page=8",
    "checked": "2026-09-30",
}


def is_ground(net: str) -> bool:
    """Recognize explicit ground labels only; do not infer voltages from connectivity."""
    return bool(re.fullmatch(r"(?:[ADP]?GND|VSS|0V)", _name(net).rsplit('/', 1)[-1], re.I))


def part_rule(component) -> str | None:
    records = component.schematic + component.pcb
    if not records:
        return None
    # Match explicit identities, not arbitrary substrings or reference designators.
    values = {r.value.strip().upper() for r in records}
    if len(values) != 1:
        return None
    value = next(iter(values))
    if value in {"MAX30102EFD_T", "MAX30102EFD+T", "MAX30102EFD+"}:
        return "max30102"
    if re.fullmatch(r"MCP73831-[2345](?:[A-Z]{2})?(?:/I)?-?MC(?:TR)?", value):
        return "mcp73831_dfn"
    return None


def _identity(component) -> dict:
    records = component.schematic or component.pcb
    fields = {re.sub(r"[^a-z0-9]", "", key.lower()): value.strip()
              for record in records for key, value in record.properties.items() if value.strip()}
    def field(*names):
        return next((fields[n] for n in names if fields.get(n) not in (None, "~")), "")
    return {"value": records[0].value if records else "", "footprint": records[0].footprint if records else "",
            "mpn": field("mpn", "manufacturerpartnumber", "mfrpartnumber", "manufacturernumber"),
            "manufacturer": field("manufacturer", "mfr"), "lcsc": field("lcsc", "lcscpart", "lcscpartnumber"),
            "datasheet": field("datasheet"), "part_rule": part_rule(component)}


def build_circuit(project: Project) -> dict:
    available = project.connectivity.get("status") == "compared"
    identities = {ref: _identity(c) for ref, c in project.components.items()}
    result = {"status": "available" if available else "unavailable", "identities": identities,
              "supply_nets": [], "bypass_capacitors": [], "bus_candidates": [], "part_findings": [],
              "part_rules_checked": [], "limits": [
                  "Rail names and bypass connections do not verify operating voltages or capacitor placement.",
                  "Local datasheet rules cover selected MCP73831 DFN and MAX30102 connections and nearby parts. Other parts need the optional AI review or manual review.",
                  "Local rules do not check stock or prices. Optional LCSC PDF retrieval is reported separately."]}
    if not available:
        return result
    rails = set()
    for component in project.components.values():
        for pin in component.schematic_pins:
            if pin.electrical_type in ("power_in", "power_out") and pin.net and not is_ground(pin.net) and not pin.net.startswith("unconnected-("):
                rails.add(pin.net)
    result["supply_nets"] = sorted(rails)
    two_pin = {}
    for ref, component in project.components.items():
        pins = component.schematic_pins
        if len(component.pcb) > 1 or len(component.schematic) != 1 or any(r.dnp for r in component.schematic + component.pcb):
            continue
        if len(pins) == 2:
            two_pin[ref] = {p.net for p in pins}
        if re.fullmatch(r"C\d+", ref) and len(pins) == 2:
            grounds = [p.net for p in pins if is_ground(p.net)]
            others = [p.net for p in pins if p.net in rails]
            if grounds and others:
                result["bypass_capacitors"].append({"reference": ref, "value": identities[ref]["value"],
                    "rail": others[0], "ground": grounds[0], "evidence": component.schematic[0].location})
    for net, members in project.schematic_nets.items():
        bus_pins = [{'reference':ref, 'pin':p.number, 'name':p.name}
                    for ref,c in project.components.items() for p in c.schematic_pins
                    if p.net == net and not p.no_connect and any(re.fullmatch(r'(?:SDA|SCL)\d*', name.strip(), re.I) for name in p.name.split('/'))
                    and not any(r.dnp for r in c.schematic+c.pcb)]
        if not bus_pins and not re.search(r"(?:^|[/_])(?:I2C\d*[_/]?)?(?:SDA|SCL)\d*$", net, re.I):
            continue
        pullups = []
        for ref, nets in two_pin.items():
            if not re.fullmatch(r"R\d+", ref) or net not in nets:
                continue
            for rail in (nets - {net}) & rails:
                value = _passive_value(ref, identities[ref]["value"])
                if value is not None and value.is_finite() and value > 0:
                    pullups.append({"reference": ref, "value": identities[ref]["value"], "rail": rail, 'ohms':float(value)})
        parallel = [{'rail':rail, 'references':[p['reference'] for p in pullups if p['rail']==rail],
                     'ohms':round(1/sum(1/p['ohms'] for p in pullups if p['rail']==rail), 6)}
                    for rail in sorted({p['rail'] for p in pullups})]
        detail = '; '.join(f'{", ".join(p["references"])} → {p["rail"]}: {p["ohms"]:g} Ω effective' for p in parallel)
        result["bus_candidates"].append({"net": net, "members": members, "pins":bus_pins, "pullups": pullups,
            'parallel_pullups':parallel,
            "interpretation": (detail+'. Timing and rail voltages remain unverified.')
                if pullups else "No external pull-up resistor to a recognized supply found. Internal pull-ups, external boards and interface mode are unknown."})
        # Exact, dedicated pin names avoid treating alternate-function SPI pins as I²C faults.
        for ref in sorted({p['reference'] for p in bus_pins}):
            pins = [p for p in bus_pins if p['reference']==ref and p['name'].upper() in ('SDA','SCL')]
            if {p['name'].upper() for p in pins} != {'SDA','SCL'} or len({p['pin'] for p in pins}) < 2:
                continue
            result['part_findings'].append({'reference':ref, 'rule':'topology:i2c_joined', 'pads':[],
                'category':'electrical', 'priority':'review', 'title':ref+': data and clock share one wire',
                'summary':f'{ref} has both its SDA and SCL pins on {net}.',
                'why':'I²C needs separate data and clock lines. This connection cannot operate as an I²C bus; it may be intentional if this interface is unused.',
                'next_step':f'If you use I²C on {ref}, separate its SDA and SCL wires and connect each to the matching controller signal.',
                'steps':[f'If you use I²C on {ref}, separate its SDA and SCL wires and connect each to the matching controller signal.',
                         'Save, update the PCB from the schematic, then click Update here.'],
                'evidence':[project.components[ref].schematic[0].location] if project.components[ref].schematic else [],
                'pin_targets':[{**p,'net':net} for p in pins], 'sources':[],
                'basis':'Direct net equality of declared SDA and SCL pins; intended interface use is unverified.'})
    for ref, component in project.components.items():
        rule = part_rule(component)
        if not rule or len(component.pcb) != 1 or len(component.schematic) != 1 or any(r.dnp for r in component.schematic + component.pcb):
            continue
        pads = defaultdict(list)
        for pad in component.pcb[0].pads:
            pads[pad.number].append(pad)
        result["part_rules_checked"].append({"reference": ref, "rule": rule})
        if rule == "max30102":
            numbers = [n for n in ("1", "5", "6", "7", "8", "14") if n in pads]
            missing = sorted({"1", "5", "6", "7", "8", "14"} - set(numbers), key=reference_key)
            connected = [n for n in numbers if any(p.net and (not p.net.startswith("unconnected-(") or
                set(project.pcb_nets.get(p.net, [])) - {f'{ref}.{n}'}) for p in pads[n])]
            result["part_findings"].append({"reference": ref, "rule": rule, "pads": numbers,
                "category": "electrical" if connected else "assembly" if missing else "expected",
                "priority": "review" if connected or missing else "note",
                "title": f"{ref}: check NC pad connections" if connected else f"{ref}: package pads missing" if missing else f"{ref}: {len(numbers)} documented NC pads",
                "summary": f"Pads {', '.join(connected)} have assigned nets although the manufacturer identifies them as NC."
                    if connected else f"Expected package pads {', '.join(missing)} are absent from the footprint." if missing else
                    f"Pads {', '.join(numbers)} have no connected net assignment, consistent with the datasheet.",
                "why": "These pads provide mechanical attachment. A missing schematic pin alone does not imply a circuit defect.",
                "next_step": "Review the footprint and assignments against the package pin table." if connected or missing else "No electrical repair indicated by these unused pads.",
                "evidence": [f"{component.pcb[0].source_file}:{p.line}; pad {n}: {p.net or '(no net)'}" for n in numbers for p in pads[n]],
                "sources": [MAX_SOURCE]})
        else:
            ep = pads.get("9", [])
            ground = {_name(p.net) for p in pads.get("6", []) if p.net}
            connected = bool(ep and len(ground) == 1 and all(_name(p.net) in ground for p in ep))
            if connected:
                continue
            prog = next((p.net for p in component.schematic_pins if p.number == "8"), None)
            currents = []
            for resistor, nets in two_pin.items():
                if re.fullmatch(r"R\d+", resistor) and prog in nets and any(is_ground(n) for n in nets - {prog}):
                    ohms = _passive_value(resistor, identities[resistor]["value"])
                    if ohms is not None and ohms.is_finite() and Decimal(2000) <= ohms <= Decimal(67000):
                        currents.append((resistor, identities[resistor]["value"], float(Decimal(1_000_000) / ohms)))
            note = ""
            if len(currents) == 1:
                resistor, value, ma = currents[0]
                note = f" {resistor} = {value} implies about {ma:g} mA nominal charging; temperature rise has not been calculated."
            result["part_findings"].append({"reference": ref, "rule": rule, "pads": ["9"],
                "category": "electrical", "priority": "review", "title": f"{ref}: connect the bottom pad to ground",
                "summary": "The metal pad under this charger (pad 9) is missing or does not have the same ground connection as pin 6." + note,
                "why": "The chip maker says the metal pad under the charger should connect to ground. This file does not show that connection. Check it before the board is made. This is not proof that the charger cannot work.",
                "next_step": "Give pad 9 the same ground connection as pin 6, then check the PCB pad and its ground copper.",
                "evidence": [component.pcb[0].location] + [f"{component.pcb[0].source_file}:{p.line}; pad 9: {p.net or '(no net)'}" for p in ep],
                "sources": [MCP_SOURCE]})
    from ..routing_chains import build_routing_chains
    result['routing_chains'] = build_routing_chains(project)
    return result


# Canonical signal roles for widely-standardized serial-bus pin-functions.
# Deliberately narrow: generic GPIO/ADC/power names are excluded because a
# function-to-net mismatch on those usually reflects naming conventions, and
# this check must not invent failures from ambiguous labels.
_SIGNAL_ALIASES = {
    "SCLK": "SPI_CLK", "SCK": "SPI_CLK", "SK": "SPI_CLK", "CLK": "SPI_CLK",
    "SS": "SPI_SEL", "CS": "SPI_SEL", "NSS": "SPI_SEL", "SEL": "SPI_SEL", "CSN": "SPI_SEL",
    "MOSI": "SPI_MOSI", "SDO": "SPI_MOSI", "DI": "SPI_MOSI",
    "MISO": "SPI_MISO", "SDI": "SPI_MISO", "DO": "SPI_MISO",
    "SDA": "I2C_DAT", "SCL": "I2C_CLK",
    "TX": "UART_TX", "TXD": "UART_TX", "RX": "UART_RX", "RXD": "UART_RX",
}
_SIGNAL_TOKEN = re.compile(
    r"(SDA|SCL|TXD|RXD|TX|RX|SCLK|SCK|SK|CLK|MOSI|SDO|DI|MISO|SDI|DO|SS|CS|NSS|SEL|CSN)(\d*)$")


def _signal_key(text: str) -> str:
    """Map a pin-function or net name to a canonical signal role, or ''.

    Only the last underscore/dash-separated token is considered, so QSPI_SCLK
    and Q_CLK both resolve to SPI_CLK. A trailing digit (SDA1) is tolerated.
    KiCad inversion markers (~, {...}) are stripped before matching, so ~{CS}
    resolves to SPI_SEL. Anything outside the narrow alias table is
    unclassifiable, never guessed.
    """
    cleaned = re.sub(r"[~{}]", "", text or "")
    parts = [p for p in re.split(r"[_\-/.\s]+", cleaned.upper()) if p]
    if not parts:
        return ""
    match = _SIGNAL_TOKEN.fullmatch(parts[-1])
    if not match:
        return ""
    return _SIGNAL_ALIASES[match.group(1)]


def _auto_net(net: str) -> bool:
    return not net or bool(re.match(r"(?i)^net[-_(\[]|^unconnected-\(", net.strip()))


def _peer_symbol_role(project, ref, number):
    """Canonical signal role of a schematic symbol pin, or '' when unclear."""
    component = project.components.get(ref)
    if not component:
        return ""
    pin = next((p for p in component.schematic_pins if p.number == number), None)
    if not pin:
        return ""
    roles = set()
    for alternative in (pin.name or "").split("/"):
        key = _signal_key(alternative)
        if key:
            roles.add(key)
    return next(iter(roles)) if len(roles) == 1 else ""


def confirm_spi_swap(project, ref, own):
    """Corroborate a copper-only SPI_CLK/SPI_SEL swap at schematic + peer side.

    Returns a confirmation record when the schematic pairs the same controller
    pins with the same nets as the copper AND the peer symbol pins show the
    crossed peripheral roles (clock function on the controller-select net and
    select function on the controller-clock net). Returns None when the
    evidence is missing, disagrees, or the peer roles are unclassifiable: the
    finding then stays a possible swap, never a confirmed one.
    """
    schematic_nets = getattr(project, 'schematic_nets', None) or {}
    if not schematic_nets or len(own) != 2:
        return None
    roles = {entry["fn_key"] for entry in own}
    if roles != {"SPI_CLK", "SPI_SEL"}:
        return None
    by_role = {entry["fn_key"]: entry for entry in own}
    clk_entry, sel_entry = by_role["SPI_CLK"], by_role["SPI_SEL"]
    nets = {entry["net"] for entry in own}
    if len(nets) != 2:
        return None
    for entry in own:
        endpoints = schematic_nets.get(entry["net"], [])
        if f"{ref}.{entry['pin']}" not in endpoints:
            return None
    peer_roles = {}
    for entry in own:
        for endpoint in schematic_nets.get(entry["net"], []):
            peer_ref, _, peer_pin = endpoint.partition(".")
            if peer_ref == ref or not peer_pin:
                continue
            role = _peer_symbol_role(project, peer_ref, peer_pin)
            if role in ("SPI_CLK", "SPI_SEL"):
                peer_roles.setdefault(entry["net"], []).append((peer_ref, peer_pin, role))
    if not peer_roles:
        return None
    clk_net_has_sel_peer = any(role == "SPI_SEL" for _, _, role in peer_roles.get(clk_entry["net"], []))
    sel_net_has_clk_peer = any(role == "SPI_CLK" for _, _, role in peer_roles.get(sel_entry["net"], []))
    if not (clk_net_has_sel_peer and sel_net_has_clk_peer):
        return None
    data_lines = []
    for net, endpoints in schematic_nets.items():
        if net in nets:
            continue
        refs = {endpoint.split(".")[0] for endpoint in endpoints}
        if ref in refs and len(refs) > 1:
            data_lines.append(net)
    return {"controller": ref,
            "clock_pin": clk_entry, "select_pin": sel_entry,
            "peer_roles": peer_roles, "data_lines": sorted(data_lines)}


def pcb_pinfunction_swap_findings(project: Project) -> list[dict]:
    """Flag mutually-crossed protocol signals from drawn copper alone.

    No schematic or datasheet is consulted: a pad whose footprint pin-function
    names signal X while its copper net names signal Y becomes a finding only
    when a second pad shows the exact mirror (function Y on a net named X).
    A single-sided mismatch is ambiguous net labelling, not evidence of a
    swap, and is left unreported rather than guessed about.
    """
    pads = []
    for ref, component in project.components.items():
        for record in component.pcb:
            for pad in record.pads:
                if not pad.number or _auto_net(pad.net):
                    continue
                keys = set()
                for alternative in (pad.name or "").split("/"):
                    key = _signal_key(alternative)
                    if key:
                        keys.add(key)
                if len(keys) != 1:
                    continue
                fn_key = keys.pop()
                net_key = _signal_key(pad.net)
                if not net_key or fn_key == net_key:
                    continue
                pads.append({"reference": ref, "pin": pad.number, "name": pad.name,
                             "net": pad.net, "fn_key": fn_key, "net_key": net_key,
                             "location": record.location})
    by_pad = defaultdict(set)
    for left in range(len(pads)):
        for right in range(left + 1, len(pads)):
            first, second = pads[left], pads[right]
            if (first["fn_key"] == second["net_key"] and second["fn_key"] == first["net_key"]
                    and first["fn_key"] != second["fn_key"] and first["net_key"] != second["net_key"]):
                by_pad[left].add(right)
                by_pad[right].add(left)
    if not by_pad:
        return []
    involved = defaultdict(list)
    for index, counterparts in by_pad.items():
        involved[pads[index]["reference"]].append((index, sorted(counterparts)))
    findings = []
    for ref, entries in involved.items():
        own = sorted({pads[i]["pin"] for i, _ in entries})
        others = sorted({(pads[j]["reference"], pads[j]["pin"], pads[j]["name"], pads[j]["net"])
                         for _, js in entries for j in js if pads[j]["reference"] != ref})
        detail = "; ".join(f"{ref} pin {pads[i]['pin']} ({pads[i]['name']}) is copper-connected to net {pads[i]['net']}"
                           for i, _ in entries)
        mirror = (" Counterparts: " + "; ".join(f"{r} pin {n} ({f}) on net {t}" for r, n, f, t in others) + "."
                  if others else " Both pads belong to the same component.")
        roles = sorted({pads[i]["fn_key"] for i, _ in entries} | {pads[j]["fn_key"] for _, js in entries for j in js})
        confirmation = confirm_spi_swap(project, ref, [pads[i] for i, _ in entries])
        if confirmation is not None:
            clk, sel = confirmation["clock_pin"], confirmation["select_pin"]
            peer_text = "; ".join(
                f"{peer_ref}.{peer_pin} ({role}) on {net}"
                for net, peers in confirmation["peer_roles"].items() for peer_ref, peer_pin, role in peers)
            rewire = (f"In the schematic, move {ref} pin {sel['pin']} ({sel['name']}) from {sel['net']} to {clk['net']} "
                      f"and {ref} pin {clk['pin']} ({clk['name']}) from {clk['net']} to {sel['net']}, so the select line "
                      f"drives the peer select pin and the clock drives the peer clock pin; then update the PCB from "
                      f"the schematic and re-run checks.")
            data_text = (f" Shared data nets with {ref}: {', '.join(confirmation['data_lines'])}." if confirmation["data_lines"] else "")
            title = f"{ref}: confirmed swapped {'/'.join(roles)} signals"
            why = ("Drawn copper, the schematic netlist, and the peer symbol pin names agree: "
                   f"the controller select output reaches a peer clock pin and the controller clock reaches a peer select pin. {detail}.{data_text}")
            steps = [rewire,
                     "Keep the other parts on those nets where they belong; only the two controller connections move.",
                     "Run KiCad's board check and this preflight again; this warning should disappear without breaking another connection."]
            evidence = ([pads[i]["location"] for i, _ in entries] + [detail + "." + mirror,
                f"Schematic netlist pairs the same controller pins with the same nets as the copper: {peer_text}." + data_text,
                "Basis: drawn-copper pad records, schematic netlist, and peer symbol pin names; no datasheet was consulted."])
        else:
            title = f"{ref}: possible swapped {'/'.join(roles)} signals"
            why = ("Two footprint pin-functions name one signal each while the drawn copper connects "
                   "each of them to the other's named net. Net names are designer labels from the board file, "
                   "and no schematic or datasheet was consulted, so this is a reason to verify the assignment, "
                   "not proof the signals are crossed. Single-sided function/name mismatches are not reported.")
            steps = [f"At {ref}, compare each listed pin's footprint function against its part datasheet: {detail}.",
                     "Confirm which net each counterpart pin must drive, then reroute or rename so each function reaches its named net.",
                     "Run KiCad's board check and this preflight again; this warning should disappear without breaking another connection."]
            evidence = [pads[i]["location"] for i, _ in entries] + [detail + "." + mirror,
                "Source: drawn-copper pad records only; no schematic or datasheet was used."]
        markers = [{"reference": ref, "pin": pads[i]["pin"], "net": pads[i]["net"], "name": pads[i]["name"]}
                   for i, _ in entries]
        markers += [{"reference": pads[j]["reference"], "pin": pads[j]["pin"], "net": pads[j]["net"], "name": pads[j]["name"]}
                    for _, js in entries for j in js if pads[j]["reference"] != ref]
        findings.append({"reference": ref, "rule": "pinfunction_net_swap", "pads": own,
            "category": "electrical", "priority": "review", "title": title,
            "summary": detail + "." + mirror, "why": why, "next_step": steps[0],
            "steps": steps, "evidence": evidence, "sources": [],
            "chain_targets": markers, "basis": "Drawn-copper pad-function comparison"})
    return findings


def inspect_net(project: Project, net: str) -> str:
    matches = [n for n in project.schematic_nets if _name(n) == _name(net)]
    if not matches:
        raise ValueError(f"Net {net!r} was not found in the exported schematic netlist.")
    name = matches[0]
    lines = [f"Net {name}"]
    for endpoint in project.schematic_nets[name]:
        ref, number = endpoint.rsplit('.', 1)
        component = project.components[ref]
        pin = next(p for p in component.schematic_pins if p.number == number)
        value = (component.schematic or component.pcb)[0].value
        lines.append(f"  {endpoint}: {pin.name or '(unnamed pin)'} | {value} | {pin.electrical_type}")
    return '\n'.join(lines)


def inspect_pin(project: Project, endpoint: str) -> str:
    if '.' not in endpoint:
        raise ValueError("Use --pin REF.NUMBER, for example U8.8.")
    ref, number = endpoint.rsplit('.', 1)
    component = project.components.get(ref)
    pin = next((p for p in component.schematic_pins if p.number == number), None) if component else None
    if pin is None:
        raise ValueError(f"Pin {endpoint!r} was not found; KiCad netlist export must succeed.")
    state = "intentionally unconnected" if pin.no_connect else "no no-connect marker"
    return f"{endpoint} ({pin.name}): {state}\n" + inspect_net(project, pin.net)

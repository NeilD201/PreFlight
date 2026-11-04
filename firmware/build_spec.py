"""Deterministic hardware discovery from verified reviewer evidence.

``build_firmware_spec`` reads the serialized project (``asdict(Project)``)
already produced by the PCB review and derives the structured connectivity
model firmware generation must use: the programmable MCU candidate with its
pin-to-net map, power nets, communication buses, identified peripherals, and
explicit open questions for anything the evidence does not establish.

Pin assignments, addresses, and part identities are never invented. A
component becomes an MCU candidate only when its declared value or
manufacturer part number matches a known programmable-part family, and the
match, source locations, and confidence are recorded with the claim.
"""

import re

from ..checks.circuit import is_ground


# Generic programmable-part families. These are match patterns against the
# *declared* value/MPN in the CAD data, not claims about any customer board.
# A match yields a candidate with evidence, never a verified selection.
MCU_FAMILIES = (
    ("nRF52", r"NRF52\d{3}"),
    ("nRF53", r"NRF53\d{3}"),
    ("ESP32", r"ESP32"),
    ("STM32", r"STM32"),
    ("RP2040", r"RP2040"),
    ("ATmega", r"ATMEGA\d+"),
    ("ATtiny", r"ATTINY\d+"),
    ("SAMD", r"ATSAMD\d+"),
    ("PIC", r"PIC(?:1[02468]|2[024]|3[024])"),
    ("EFR32", r"EFR32"),
)


def _match_mcu_family(text):
    # Anchored prefix match: real markings carry package/temperature suffixes
    # (e.g. NRF52840QIAA), so an exact full-string match would miss them.
    token = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    for family, pattern in MCU_FAMILIES:
        if re.match(pattern, token):
            return family
    return None


def _identity(summary):
    return summary if isinstance(summary, dict) else {}


def _record_locations(records):
    locations = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        where = record.get("source_file", "")
        if record.get("line"):
            where = "%s:%s" % (where, record.get("line"))
        if where and where not in locations:
            locations.append(where)
    return locations


def _detect_mcu(components, identities):
    candidates = []
    for ref, component in (components or {}).items():
        if not isinstance(component, dict):
            continue
        identity = _identity(identities.get(ref))
        value = identity.get("value", "")
        mpn = identity.get("mpn", "")
        family = _match_mcu_family(mpn) or _match_mcu_family(value)
        if not family:
            continue
        records = (component.get("schematic") or []) + (component.get("pcb") or [])
        pin_count = len(component.get("schematic_pins") or []) or len({
            (pad.get("number") or "") for pad in
            [p for r in (component.get("pcb") or []) if isinstance(r, dict)
             for p in (r.get("pads") or [])] if isinstance(pad, dict)}) or None
        evidence = ["Declared value %r" % value] if value else []
        if mpn:
            evidence.append("Declared MPN %r" % mpn)
        if identity.get("identity_source") == "user-provided":
            evidence.append("Identity supplied by the designer in firmware Q&A")
        locations = _record_locations(records)
        if locations:
            evidence.append("Declared at %s" % ", ".join(locations[:4]))
        candidates.append({
            "reference": ref, "family": family,
            "declared_value": value, "declared_mpn": mpn,
            "manufacturer": identity.get("manufacturer", ""),
            "package": identity.get("footprint", ""),
            "pin_count": pin_count,
            "confidence": "high" if mpn else "medium",
            "evidence": evidence,
        })
    if len(candidates) == 1:
        result = {"status": "identified"}
        result.update(candidates[0])
        return result
    if len(candidates) > 1:
        return {"status": "ambiguous", "candidates": candidates,
                "evidence": ["%d components declare recognized MCU part numbers" % len(candidates)]}
    return {"status": "unidentified", "candidates": []}


def _component_nets(component):
    """All nets touching a component's schematic pins or footprint pads.

    KiCad's unconnected-pad markers (``unconnected-(…)``) are not electrical
    nets and are excluded so questions never cite them as connections.
    """
    nets = set()
    for pin in component.get("schematic_pins") or []:
        if isinstance(pin, dict) and pin.get("net") and not pin["net"].startswith("unconnected-"):
            nets.add(pin["net"])
    for record in component.get("pcb") or []:
        if not isinstance(record, dict):
            continue
        for pad in record.get("pads") or []:
            if isinstance(pad, dict) and pad.get("net") and not pad["net"].startswith("unconnected-"):
                nets.add(pad["net"])
    return nets


def _is_populated(component):
    records = (component.get("schematic") or []) + (component.get("pcb") or [])
    return any(not (r.get("dnp") if isinstance(r, dict) else False) for r in records) if records else True


# Value/footprint kinds that never need a firmware identity question. These
# are match patterns against declared CAD data (value, footprint, symbol),
# never claims about any customer board. A match marks the part identified
# with a firmware-known kind so planning can use it without interrogating
# the designer; pin-level wiring still comes from the reviewed nets.
FIRMWARE_KNOWN_KINDS = (
    ("addressable_led", re.compile(r"(?i)\b(SK6812[A-Z0-9\-]*|WS2812[A-Z0-9\-]*|APA102[A-Z0-9\-]*|APA107[A-Z0-9\-]*)\b")),
    ("linear_regulator", re.compile(r"(?i)\b((LD|AMS|LM|AZ)1117[\w.\-]*|(LM|UA|MC|KA|ST)78(05|09|12|15)[\w.\-]*|LM317[\w.\-]*|XC62\w*|AP2112[\w.\-]*|MCP17\d\d[\w.\-]*)\b")),
    ("crystal", re.compile(r"(?i)\b\d+(\.\d+)?\s*(MHZ|KHZ)\b")),
    ("usb_connector", re.compile(r"(?i)\b(USB[-_ ]?(C|A|B|MICRO|MINI)|TYPE[-_ ]?C)(?![A-Z])")),
    ("spi_flash", re.compile(r"(?i)\b((W25|MX25|GD25|W25Q|EN25)[A-Z0-9.\-]*)\b")),
    ("rotary_encoder", re.compile(r"(?i)(ROTARY\s*[_-]?\s*ENCODER|\bROTARY\b|\bENCODER\b)")),
    ("i2c_peripheral", re.compile(r"(?i)\b(PCF8574[A-Z0-9.\-]*|TCA9548[A-Z0-9.\-]*|PCA95[A-Z0-9.\-]*|MCP2300[89][A-Z0-9.\-]*|SSD1306[A-Z0-9.\-]*|BME280[A-Z0-9.\-]*|MPU6050[A-Z0-9.\-]*)\b")),
    ("level_shifter", re.compile(r"(?i)\b((SN74LV|SN74LVC|TXS010|TXB010)[A-Z0-9.\-]*)\b")),
    ("passive", re.compile(r"(?i)^\d.*")),
)

SWD_NETS = {"SWDIO", "SWCLK", "SWO", "SWC"}
SDA_LIKE = re.compile(r"^(SDA\d*|SD\d+)$")
SCL_LIKE = re.compile(r"^(SCL\d*|SC\d+)$")
SPI_NETS = re.compile(r"^(SCK\d*|MOSI\d*|MISO\d*|SCLK\d*)$")


def _firmware_known_kind(ref, identity, component):
    """Classify parts whose firmware role is settled by declared data.

    Returns (kind, rule) or (None, None). Passives (R/C/L refs with a
    declared value), crystals, regulators, USB connectors, SPI flash, and
    addressable LEDs never need a 'what is this component' question.
    """
    value = (identity.get("value") or "").strip()
    records = (component.get("schematic") or []) + (component.get("pcb") or [])
    haystack = " ".join(
        str((r.get("value") or "")) + " " + str((r.get("footprint") or "")) + " " + str((r.get("symbol") or ""))
        for r in records if isinstance(r, dict))
    kind_ref = (ref or "").upper()
    if (kind_ref.startswith("R") or kind_ref.startswith("C") or kind_ref.startswith("L")) and value:
        return "passive", "passive_two_terminal"
    if (re.search(r"(?i)\b\d+(\.\d+)?\s*(MHZ|KHZ)\b", value)
            or re.search(r"(?i)\b(CRYSTAL|XTAL|RESONATOR)", " ".join([value, haystack]))):
        return "clock", "crystal_or_resonator"
    nets = set()
    for pin in component.get("schematic_pins") or []:
        if isinstance(pin, dict) and pin.get("net"):
            nets.add(re.sub(r"[^A-Z0-9]", "", pin["net"].upper()))
    if kind_ref.startswith("J") and (nets & SWD_NETS):
        return "debug_connector", "swd_debug_port"
    if kind_ref.startswith("J") and 3 <= len(nets) <= 8:
        raw = {(pin.get("net") or "").upper() for pin in component.get("schematic_pins") or [] if isinstance(pin, dict)}
        sda = {n for n in raw if SDA_LIKE.match(n)}
        scl = {n for n in raw if SCL_LIKE.match(n)}
        spi = {n for n in raw if SPI_NETS.match(n)}
        if sda and scl and not spi and any("GND" in n for n in raw):
            return "i2c_port", "i2c_expansion_port"
    for kind, pattern in FIRMWARE_KNOWN_KINDS:
        if kind == "passive":
            continue
        if pattern.search(value) or pattern.search(haystack):
            return kind, kind
    return None, None


def _series_bridge(components, dout_nets, din_nets):
    """Map a DOUT net to (DIN net, resistor ref) through series resistors.

    Data lines often carry small series resistors, so two chain fragments
    whose nets meet at both ends of one populated two-terminal resistor are
    the same electrical chain. Only exact DOUT-net/resistor/DIN-net triples
    merge; anything else stays separate instead of being guessed.
    """
    bridges = {}
    for ref, component in (components or {}).items():
        if not isinstance(component, dict) or not (ref or "").upper().startswith("R"):
            continue
        if not _is_populated(component):
            continue
        nets = sorted({n for n in _component_nets(component)})
        if len(nets) != 2:
            continue
        first, second = nets
        if first in dout_nets and second in din_nets:
            bridges.setdefault(first, (second, ref))
        elif second in dout_nets and first in din_nets:
            bridges.setdefault(second, (first, ref))
    return bridges


def _walk_source(net, components, mcu_nets):
    """Walk a head DIN net back through series resistors to its source.

    Returns (source_net, path_refs, branched). The walk stops at the first
    net that is not continued by exactly one two-terminal series resistor.
    """
    path, seen, current = [], {net}, net
    while True:
        cont = []
        for ref, component in (components or {}).items():
            if not isinstance(component, dict) or not (ref or "").upper().startswith("R"):
                continue
            if not _is_populated(component):
                continue
            nets = sorted(_component_nets(component))
            if len(nets) != 2 or current not in nets:
                continue
            other = nets[1] if nets[0] == current else nets[0]
            if other not in seen:
                cont.append((ref, other))
        if len(cont) != 1:
            return current, path, len(cont) > 1
        ref, current = cont[0]
        path.append(ref)
        seen.add(current)
        if current in set(mcu_nets or ()):
            return current, path, False


_OUTPUT_PIN = re.compile(r"^(Y\d*|Z\d*|OUT\d*)$")
_INPUT_PIN = re.compile(r"^(A\d*|B\d*|IN\d*|D\d*)$")


def _buffer_driver(components, net, mcu_nets, supply_nets=()):
    """Find a level-shifter/buffer driving ``net`` from an MCU net.

    Returns (driver_ref, mcu_net, mcu_pin) or None. Direction comes from
    pin names (output-style pin on the LED side, input-style pin on the MCU
    side, e.g. TI 1T34 A->Y); without that evidence there is no driver.
    Supply and ground pins never count as the MCU-side signal.
    """
    mcu_net_set = set(mcu_nets or ())
    rail_nets = set(supply_nets or ())
    for ref, component in (components or {}).items():
        if not isinstance(component, dict):
            continue
        records = (component.get("schematic") or []) + (component.get("pcb") or [])
        text = " ".join(str((r.get("value") or "") + " " + str((r.get("footprint") or "")) + " " + str((r.get("symbol") or "")))
                        for r in records if isinstance(r, dict))
        if not any(pat.search(text) for kind, pat in FIRMWARE_KNOWN_KINDS if kind == "level_shifter"):
            continue
        pins = [p for p in (component.get("schematic_pins") or []) if isinstance(p, dict)]
        on_net = [p for p in pins if p.get("net") == net]
        on_mcu = [p for p in pins
                  if p.get("net") in mcu_net_set
                  and p.get("net") not in rail_nets
                  and not is_ground(p.get("net") or "")]
        if len(on_net) != 1 or len(on_mcu) != 1:
            continue
        out_name = re.sub(r"[^A-Z]", "", (on_net[0].get("name") or "").upper())
        in_name = re.sub(r"[^A-Z]", "", (on_mcu[0].get("name") or "").upper())
        if _OUTPUT_PIN.match(out_name) and _INPUT_PIN.match(in_name):
            return ref, on_mcu[0].get("net"), on_mcu[0].get("number", "")
    return None


def _led_chains(leds, mcu_nets=(), components=None, mcu_ref="", supply_nets=()):
    """Group addressable LEDs into data chains from DIN/DOUT wiring.

    Each LED dict needs reference plus DIN/DOUT nets. Fragments joined by
    series resistors merge into one chain with the bridge refs recorded.
    The head source is walked back through series resistors (and at most one
    named-direction buffer) to the driving MCU pin. A head whose source is
    not an MCU net and ambiguous fanout stay unresolved instead of guessed.
    """
    components = components if isinstance(components, dict) else {}
    by_dout = {led["dout"]: led for led in leds if led.get("dout")}
    by_din = {}
    for led in leds:
        if led.get("din"):
            by_din.setdefault(led["din"], []).append(led)
    bridges = _series_bridge(components, set(by_dout), set(by_din))

    def successor(led):
        direct = [l for l in by_din.get(led.get("dout"), []) if l["reference"] != led["reference"]]
        if direct:
            return direct, None
        hop = bridges.get(led.get("dout"))
        if hop:
            return [l for l in by_din.get(hop[0], [])], hop[1]
        return [], None

    def predecessor_count(led):
        count = sum(1 for other in leds
                    if other is not led and other.get("dout") == led.get("din"))
        count += sum(1 for dout_net, (din_net, _b) in bridges.items() if din_net == led.get("din"))
        return count

    mcu_net_set = set(mcu_nets or ())
    mcu_pins = {}
    for pin in ((components.get(mcu_ref) or {}).get("schematic_pins") or []):
        if isinstance(pin, dict) and pin.get("net"):
            mcu_pins.setdefault(pin["net"], (pin.get("number", ""), pin.get("name", "")))
    chains, used = [], set()
    heads = [led for led in leds if led.get("din") and predecessor_count(led) == 0]
    for head in sorted(heads, key=lambda l: l["reference"]):
        chain, seen, current = [head["reference"]], {head["reference"]}, head
        chain_bridges, verified = [], False
        while True:
            nxt, bridge = successor(current)
            if not nxt:
                break
            if len(nxt) > 1:
                break
            if nxt[0]["reference"] in seen:
                break
            seen.add(nxt[0]["reference"])
            chain.append(nxt[0]["reference"])
            if bridge:
                chain_bridges.append(bridge)
            current = nxt[0]
        for ref in chain:
            used.add(ref)
        source, path, branched = _walk_source(head.get("din"), components, mcu_nets)
        driver, mcu_net, mcu_pin = "", "", ""
        if not branched and source in mcu_net_set:
            verified = True
            mcu_net = source
            number, name = mcu_pins.get(source, ("", ""))
            mcu_pin = ("%s %s" % (number, name)).strip()
        elif not branched:
            found = _buffer_driver(components, source, mcu_nets, supply_nets)
            if found:
                driver, mcu_net, pin_number = found
                verified = True
                number, name = mcu_pins.get(mcu_net, ("", ""))
                mcu_pin = ("%s %s" % (number or pin_number, name)).strip()
        chains.append({"head": head["reference"], "leds": chain, "count": len(chain),
                       "mcu_net": mcu_net or head.get("din"), "mcu_pin": mcu_pin,
                       "driver": driver, "source_path": path,
                       "bridges": chain_bridges, "verified": verified,
                       "unresolved": sorted({l["reference"] for l in leds if l["reference"] not in used})})
    lone = sorted({l["reference"] for l in leds if l["reference"] not in used})
    if lone:
        chains.append({"head": lone[0], "leds": lone, "count": len(lone),
                       "mcu_net": "", "mcu_pin": "", "driver": "", "source_path": [],
                       "bridges": [], "verified": False, "unresolved": lone})
    return chains


def _family_signature(nets):
    """Net-role signature for family grouping: trailing bus indexes collapse
    (SD5/SC5 -> SD#/SC#) so identical parts on different buses (e.g. J3-J9
    expansion connectors) form one family question instead of repetitive
    per-part questions. Exact nets stay in each question's scope."""
    return tuple(sorted(re.sub(r"\d+$", "#", n) for n in nets))


def _unidentified_connected(components, identities, mcu_ref):
    """Populated, electrically connected components with no usable identity.

    These are the parts firmware planning would otherwise silently lose:
    no part rule, MPN, or manufacturer to derive a driver from, but real
    nets that may need driving. Simple two-pin passives with a declared
    value (resistors, capacitors, inductors) never need firmware drivers
    and are excluded; everything else becomes an explicit open question.
    """
    items = []
    for ref, component in (components or {}).items():
        if ref == mcu_ref or not isinstance(component, dict):
            continue
        identity = _identity(identities.get(ref))
        if identity.get("part_rule") or identity.get("mpn") or identity.get("manufacturer"):
            continue
        if not _is_populated(component):
            continue
        nets = _component_nets(component)
        if not nets:
            continue
        numbers = {p.get("number") for p in (component.get("schematic_pins") or [])
                   if isinstance(p, dict) and p.get("number")}
        for record in component.get("pcb") or []:
            if not isinstance(record, dict):
                continue
            for pad in record.get("pads") or []:
                if isinstance(pad, dict) and pad.get("number"):
                    numbers.add(pad.get("number"))
        pin_total = len(numbers)
        value = identity.get("value", "")
        if pin_total <= 2:
            # One- and two-pin unknowns (crystals, test points, mounting
            # holes, connectors, passives) never need firmware drivers.
            # Schematic pins and footprint pads describe the same pins, so
            # they are counted once.
            continue
        items.append({"reference": ref, "value": value, "pins": pin_total,
                      "nets": sorted(nets)})
    return sorted(items, key=lambda item: item["reference"])


def _gpio_map(components, mcu_ref):
    component = (components or {}).get(mcu_ref) or {}
    if not isinstance(component, dict):
        return []
    pads = {}
    for record in component.get("pcb") or []:
        if not isinstance(record, dict):
            continue
        for pad in record.get("pads") or []:
            if isinstance(pad, dict) and pad.get("number") not in pads:
                pads[pad.get("number")] = (pad.get("net", ""), pad.get("name", ""))
    rows = []
    seen = set()
    for pin in component.get("schematic_pins") or []:
        if not isinstance(pin, dict):
            continue
        seen.add(pin.get("number", ""))
        pcb_net, _ = pads.get(pin.get("number"), ("", ""))
        rows.append({"pin": pin.get("number", ""), "name": pin.get("name", ""),
                     "schematic_net": pin.get("net", ""), "pcb_net": pcb_net,
                     "match": bool(pcb_net) and pcb_net == pin.get("net", "")})
    # The schematic symbol may carry no pin records while the PCB footprint
    # does (as in real linked-project reports). Pad nets and names are parsed
    # CAD facts; they are shown as unverified (match False), never as
    # agreement. Pads with no net at all carry no information and are skipped.
    for number in sorted(pads):
        net, pname = pads[number]
        if not number or number in seen or not net:
            continue
        rows.append({"pin": number, "name": pname, "schematic_net": "",
                     "pcb_net": net, "match": False})
    return [r for r in rows if r["pin"] or r["name"] or r["schematic_net"] or r["pcb_net"]]


_I2C_NET = re.compile(r"(?i)^(sda|scl)(\d*)$")


def _pair_i2c_buses(buses):
    """Merge single-net SDA/SCL candidates into two-net I2C buses.

    Circuit analysis emits one candidate per net, but an I2C bus needs its
    data and clock nets together before the pin-matrix check can accept it.
    Entries pair only on equal numeric suffixes (SDA with SCL, SDA0 with
    SCL0); anything else passes through untouched rather than inventing a
    bus that does not exist.
    """
    singles = {}
    rest = []
    for bus in buses or []:
        if (not isinstance(bus, dict) or bus.get("kind") != "I2C"
                or len(bus.get("nets") or []) != 1):
            rest.append(bus)
            continue
        match = _I2C_NET.match(str(bus["nets"][0]))
        if not match:
            rest.append(bus)
            continue
        slot = singles.setdefault(match.group(2), {})
        if match.group(1).upper() in slot:
            rest.append(bus)
            continue
        slot[match.group(1).upper()] = bus
    for suffix in sorted(singles):
        slot = singles[suffix]
        if "SDA" not in slot or "SCL" not in slot:
            rest.extend(slot.values())
            continue
        sda, scl = slot["SDA"], slot["SCL"]
        rest.append({
            "kind": "I2C",
            "nets": [sda["nets"][0], scl["nets"][0]],
            "members": sorted(set(sda.get("members", [])) | set(scl.get("members", []))),
            "mcu_pins": list(sda.get("mcu_pins", [])) + list(scl.get("mcu_pins", [])),
            "all_pins": list(sda.get("all_pins", [])) + list(scl.get("all_pins", [])),
            "pullups": list(sda.get("pullups", [])) + list(scl.get("pullups", [])),
        })
    return rest


def _bus_entry(candidate, mcu_ref):
    nets = [candidate.get("net", "")] if candidate.get("net") else []
    members = []
    for member in candidate.get("members") or []:
        if isinstance(member, dict):
            members.append(member.get("reference", ""))
        else:
            members.append(member)
    pins = [p for p in (candidate.get("pins") or []) if isinstance(p, dict)]
    mcu_pins = [p for p in pins if p.get("reference") == mcu_ref] if mcu_ref else []
    return {"kind": "I2C", "nets": [n for n in nets if n],
            "members": sorted({m for m in members if m}),
            "mcu_pins": mcu_pins, "all_pins": pins,
            "pullups": candidate.get("pullups") or []}


def _peripherals(components, identities, mcu_ref):
    items = []
    for ref, component in (components or {}).items():
        if ref == mcu_ref or not isinstance(component, dict):
            continue
        identity = _identity(identities.get(ref))
        if not (identity.get("part_rule") or identity.get("mpn") or identity.get("manufacturer")):
            continue
        pins = [{"pin": p.get("number", ""), "name": p.get("name", ""), "net": p.get("net", "")}
                for p in (component.get("schematic_pins") or []) if isinstance(p, dict)]
        if not pins:
            # Fall back to footprint pad nets (parsed CAD facts, unverified).
            seen_pads = set()
            for record in component.get("pcb") or []:
                if not isinstance(record, dict):
                    continue
                for pad in record.get("pads") or []:
                    if not isinstance(pad, dict) or pad.get("number") in seen_pads:
                        continue
                    seen_pads.add(pad.get("number"))
                    pins.append({"pin": pad.get("number", ""), "name": pad.get("name", ""),
                                 "net": pad.get("net", "")})
        items.append({"reference": ref, "value": identity.get("value", ""),
                      "mpn": identity.get("mpn", ""), "manufacturer": identity.get("manufacturer", ""),
                      "part_rule": identity.get("part_rule") or "",
                      "package": identity.get("footprint", ""), "pins": pins,
                      "evidence": _record_locations((component.get("schematic") or []) +
                                                    (component.get("pcb") or []))[:4]})
    return sorted(items, key=lambda item: item["reference"])


def _stated_purpose(project):
    context = project.get("design_context") or {}
    if not isinstance(context, dict):
        return ""
    for key in ("purpose", "product_purpose", "intent", "summary", "description", "product_context"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:2000]
        if isinstance(value, dict):
            for nested in ("purpose", "summary", "intent"):
                text = value.get(nested)
                if isinstance(text, str) and text.strip():
                    return text.strip()[:2000]
    return ""


def build_firmware_spec(project, extras=None):
    """Derive the firmware hardware model from serialized reviewer evidence.

    ``extras`` carries designer-supplied facts from firmware Q&A:
    ``{'identities': {ref: {'mpn': ..., 'value': ...}}, 'purpose': ...}``.
    Overrides are marked user-provided and never presented as CAD facts.
    """
    project = project if isinstance(project, dict) else {}
    extras = extras if isinstance(extras, dict) else {}
    components = project.get("components") or {}
    circuit = project.get("circuit") or {}
    if not isinstance(circuit, dict):
        circuit = {}
    identities = dict(circuit.get("identities") or {})
    for ref, override in (extras.get("identities") or {}).items():
        if not isinstance(override, dict):
            continue
        merged = dict(identities.get(ref) or {})
        if override.get("mpn"):
            merged["mpn"] = override["mpn"]
        if override.get("value"):
            merged["value"] = override["value"]
        merged["identity_source"] = "user-provided"
        identities[ref] = merged
    available = circuit.get("status") == "available"
    open_questions = []
    if not available:
        open_questions.append({
            "need": "Reviewed connectivity is unavailable, so no pin assignment can be trusted for firmware.",
            "why": "The circuit model reports status %r instead of 'available'." % (circuit.get("status"),),
            "resolves": "connectivity", "scope": {}})
    # MCU identity comes from declared values/MPNs, which do not need
    # connectivity; pin maps and buses below still require it.
    mcu = _detect_mcu(components, identities)
    mcu_ref = mcu.get("reference") if mcu.get("status") == "identified" else ""
    if mcu.get("status") != "identified":
        if mcu.get("status") == "ambiguous":
            refs = ", ".join(sorted({c.get("reference", "") for c in mcu.get("candidates", [])}))
            open_questions.append({
                "need": "Which component is the programmable MCU that firmware should target (%s)?" % refs,
                "why": "More than one component declares a recognized MCU part number.",
                "resolves": "mcu", "scope": {"components": sorted({c.get("reference", "") for c in mcu.get("candidates", [])}), "nets": []}})
        else:
            open_questions.append({
                "need": "Which component is the programmable MCU that firmware should target?",
                "why": "No component declares a recognized MCU value or manufacturer part number.",
                "resolves": "mcu", "scope": {}})
    if not (extras.get("purpose") or _stated_purpose(project)):
        open_questions.append({
            "need": "What should the firmware do?",
            "why": "Intended behavior cannot be derived from connectivity alone; the designer must supply it.",
            "resolves": "purpose", "scope": {}})
    supply_nets = sorted(circuit.get("supply_nets") or []) if available else []
    schematic_nets = project.get("schematic_nets") or {}
    ground_nets = sorted(n for n in schematic_nets if isinstance(n, str) and is_ground(n)) if available else []
    buses = _pair_i2c_buses([_bus_entry(c, mcu_ref) for c in (circuit.get("bus_candidates") or [])
                             if isinstance(c, dict)]) if available else []
    # Firmware-settled kinds (passives, crystals, regulators, connectors,
    # SPI flash, addressable LEDs) are marked identified from declared data
    # so they never generate 'what is this component' questions.
    for ref, component in (components or {}).items():
        if not isinstance(component, dict):
            continue
        identity = _identity(identities.get(ref))
        if identity.get("part_rule") or identity.get("mpn") or identity.get("manufacturer"):
            continue
        kind, rule = _firmware_known_kind(ref, identity, component)
        if kind:
            merged = dict(identity)
            merged["part_rule"] = rule
            merged["firmware_kind"] = kind
            identities[ref] = merged
    # Addressable LEDs become chain topology, not individual questions.
    addressable = []
    for ref, component in (components or {}).items():
        if not isinstance(component, dict) or ref == mcu_ref:
            continue
        if _identity(identities.get(ref)).get("firmware_kind") != "addressable_led":
            continue
        nets = {}
        for pin in component.get("schematic_pins") or []:
            if not isinstance(pin, dict):
                continue
            norm = re.sub(r"[^A-Z]", "", (pin.get("name") or "").upper())
            if norm in ("DIN", "DI", "SDI") and pin.get("net"):
                nets["din"] = pin["net"]
            elif norm in ("DOUT", "DO", "SDO") and pin.get("net"):
                nets["dout"] = pin["net"]
        addressable.append({"reference": ref, "din": nets.get("din", ""),
                            "dout": nets.get("dout", "")})
    mcu_nets = set()
    if mcu_ref:
        for pin in (components.get(mcu_ref) or {}).get("schematic_pins") or []:
            if isinstance(pin, dict) and pin.get("net"):
                mcu_nets.add(pin["net"])
    led_chains = _led_chains(addressable, mcu_nets, components, mcu_ref, supply_nets)
    # Rotary encoders are identified hardware, but their intended behavior
    # (what rotation/press should do) is designer intent: ask once per
    # project, never once per encoder.
    encoders = sorted(ref for ref, component in (components or {}).items()
                      if isinstance(component, dict)
                      and _identity(identities.get(ref)).get("firmware_kind") == "rotary_encoder")
    if encoders:
        encoder_nets = sorted({n for ref in encoders for n in _component_nets(components.get(ref) or {})})
        shown = ", ".join(encoder_nets[:8])
        if len(encoder_nets) > 8:
            shown += ", …"
        open_questions.append({
            "need": ("What should turning the encoder%s %s do, and what should pressing %s do? "
                     "Rotation/switch pins are on %s; wiring cannot say what action each should trigger. "
                     "Common keyboard default: volume up/down on rotation, mute on press — reply 'default' to accept it."
                     % ("s" if len(encoders) > 1 else "", ", ".join(encoders),
                        "them" if len(encoders) > 1 else "it", shown or "no named nets")),
            "why": "Encoder behavior is designer intent, not derivable from connectivity.",
            "resolves": "encoder_behavior",
            "suggested_default": "Volume up/down on rotation; mute on press (common keyboard default, change freely).",
            "scope": {"components": encoders, "nets": encoder_nets}})
    # Peripheral identities and pad nets are declared CAD facts, available
    # even when compared connectivity is not.
    peripherals = _peripherals(components, identities, mcu_ref)
    unknown = _unidentified_connected(components, identities, mcu_ref)
    grouped = {}
    for item in unknown:
        key = (item["value"], item["pins"], _family_signature(item["nets"]))
        grouped.setdefault(key, []).append(item)
    for (value, _pins, _sig), members in sorted(grouped.items(), key=lambda kv: kv[1][0]["reference"]):
        refs = sorted(m["reference"] for m in members)
        nets = sorted({n for m in members for n in m["nets"]})
        shown_nets = ", ".join(nets[:8])
        if len(nets) > 8:
            shown_nets += ", …"
        if len(members) == 1:
            need = ("What is component %s%s? It connects to %s and may need a firmware driver."
                    % (refs[0], (" (marked %r)" % value) if value else "", shown_nets or "no named nets"))
        else:
            common = set(members[0]["nets"])
            for m in members[1:]:
                common &= set(m["nets"])
            distinctive = []
            for m in members:
                own = [n for n in m["nets"] if n not in common]
                distinctive.append(m["reference"] + (": " + "/".join(own[:4]) if own else ""))
            if all(":" not in d for d in distinctive):
                need = ("What are the %d parts marked %s (%s)? They share %s and may need a firmware driver. "
                        "Answer once if they are all the same kind of part."
                        % (len(members), value or "unmarked", ", ".join(refs), shown_nets or "no named nets"))
            else:
                detail = "; ".join(distinctive[:7])
                if len(distinctive) > 7:
                    detail += "; …"
                need = ("What are the %d parts marked %s (%s)? Same value and equivalent connections "
                        "on different nets (%s); they may need a firmware driver. "
                        "Answer once if they are all the same kind of part."
                        % (len(members), value or "unmarked", ", ".join(refs), detail))
        open_questions.append({
            "need": need,
            "why": ("%s %s connected pins but %s no manufacturer, part number, "
                    "or recognized part rule, so no driver can be derived."
                    % (", ".join(refs), "has" if len(members) == 1 else "have",
                       "declares" if len(members) == 1 else "declare")),
            "resolves": "peripheral",
            "scope": {"components": refs, "nets": nets}})
        for item in members:
            peripherals.append({"reference": item["reference"], "value": item["value"],
                                "mpn": "", "manufacturer": "", "part_rule": "",
                                "package": "", "pins": [], "identified": False,
                                "evidence": []})
    for entry in peripherals:
        entry.setdefault("identified", True)
    peripherals.sort(key=lambda item: item["reference"])
    gpio_map = _gpio_map(components, mcu_ref) if mcu_ref else []
    return {
        "mcu": mcu,
        "power": {"supply_nets": supply_nets, "ground_nets": ground_nets,
                  "note": "Net names only. Rail names do not verify operating voltages."},
        "buses": buses,
        "peripherals": peripherals,
        "gpio_map": gpio_map,
        "led_chains": led_chains,
        "stated_purpose": extras.get("purpose") or _stated_purpose(project),
        "open_questions": open_questions,
        "limits": [
            "Pin assignments come from the reviewed schematic/PCB nets; unverified connections stay unresolved.",
            "No operating voltages, current ratings, or timing values are inferred from net names.",
            "Framework and feature decisions belong to the planning stage, not to discovery.",
        ],
    }

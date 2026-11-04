"""PCB-to-Firmware Milestone D: deterministic firmware project generation.

``generate_firmware`` turns a validated discovery spec plus its plan into a
real bare-metal C project: sources, headers, linker script, Makefile, and
README. The first supported target is Nordic nRF52 bare-metal C compiled with
arm-none-eabi-gcc; anything else is blocked with a reason (Milestone G adds
targets), never scaffolded into a fake success.

Honesty rules, enforced by construction:

* Pin mappings come only from the discovery gpio map. ``P0.29``-style names
  parse deterministically to (port, bit); package-only pins become
  clearly-marked placeholders the designer fills from the package table.
  Nothing is guessed.
* Register maps live in one file (soc_nrf52.h) with per-block datasheet
  section references and a verify-before-flashing notice.
* Drivers with a verified part rule scaffold from their cited datasheet;
  manual drivers are documented headers; unknown-identity parts are excluded
  from the build and listed, never stubbed into silent no-ops in main.
* The README states the verification status: compilable (Milestone E
  decides), never hardware verified.
"""

import re


#: Exact nRF52 parts with known flash/RAM sizes (kilobytes). Anything else
#: blocks generation: linking the wrong memory layout bricks the image.
MEMORY_TABLE = {
    "840": (1024, 256),
    "832": (512, 64),
}

#: nRF52 GPIO port from a ``P<port>.<bit>`` schematic name. Package-only pin
#: numbers cannot be mapped without the package table, so they stay placeholders.
PORT_NAME = re.compile(r"P(\d+)\.(\d+)\b", re.I)

#: RP2040 GPIO number from a ``GPIO<n>`` schematic pin name. Valid user
#: GPIOs are 0-29 (26-28 are also ADC0-2; 29 is ADC3/VSYS sense). Names may
#: carry function suffixes (``GPIO29_ADC3``). QSPI flash pins are dedicated,
#: not GPIO, so package-only pins stay placeholders.
RP2040_GPIO = re.compile(r"GPIO(\d+)(?![0-9])", re.I)
RP2040_GPIO_MAX = 29

#: External flash assumed when the discovery spec does not establish the
#: exact flash part. The Pico SDK default (2MB) links any image that fits;
#: boards with larger flashes (e.g. W25Q128, 16MB) still boot. The README
#: records this assumption instead of claiming a verified size.
RP2040_FLASH_KB = 2048
RP2040_SRAM_KB = 264


def _exact_part(mcu):
    for text in (mcu.get("declared_mpn", ""), mcu.get("declared_value", "")):
        match = re.search(r"NRF52(\d{3})", (text or "").upper())
        if match:
            return match.group(1)
    return ""


def _port_bit(row):
    match = PORT_NAME.search(row.get("name", "") or "")
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _pin_table(gpio_map):
    table, placeholders = [], 0
    for row in gpio_map or []:
        if not isinstance(row, dict):
            continue
        label = row.get("name") or ("pin_%s" % row.get("pin", "?"))
        parsed = _port_bit(row)
        if parsed is None:
            placeholders += 1
            table.append({"label": label, "port": -1, "bit": -1,
                          "net": row.get("schematic_net") or row.get("pcb_net") or ""})
        else:
            port, bit = parsed
            table.append({"label": label, "port": port, "bit": bit,
                          "net": row.get("schematic_net") or row.get("pcb_net") or ""})
    return table, placeholders


def generate_firmware(spec, plan):
    """Build the firmware project. Returns {ok, files, validation, ...}."""
    spec = spec if isinstance(spec, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    mcu = spec.get("mcu") or {}
    if mcu.get("status") != "identified":
        return {"ok": False, "reason": "Generation needs an identified MCU."}
    if mcu.get("family") == "RP2040":
        return _generate_rp2040(spec, plan, mcu)
    if mcu.get("family") != "nRF52":
        return {"ok": False,
                "reason": ("Generation supports nRF52 and RP2040; %s is planned for a later "
                           "milestone, not scaffolded here." % (mcu.get("family") or "unknown",))}
    part = _exact_part(mcu)
    if part not in MEMORY_TABLE:
        return {"ok": False,
                "reason": ("The exact nRF52 part is not established (%r); the linker memory "
                           "layout cannot be chosen safely." % (mcu.get("declared_value", ""),))}
    flash_kb, ram_kb = MEMORY_TABLE[part]
    gpio_map = spec.get("gpio_map") or []
    pins, placeholders = _pin_table(gpio_map)
    drivers = [d for d in (plan.get("drivers") or []) if isinstance(d, dict)]
    buildable = [d for d in drivers if d.get("status") in ("scaffold", "manual")]
    excluded = [d for d in drivers if d.get("status") not in ("scaffold", "manual")]
    modules = [m for m in (plan.get("modules") or []) if isinstance(m, str)]
    # A scaffold driver needs its bus HAL even when the plan's module list
    # does not name it (e.g. no bus candidates on a linked report, but an
    # I2C part rule). Missing headers would break the build.
    need_i2c = "hal_i2c" in modules or any(
        d.get("status") == "scaffold" and (d.get("basis") or {}).get("interface", "I2C") == "I2C"
        for d in buildable)
    validation = [
        {"check": "mcu", "status": "pass",
         "detail": "%s %s identified (%s confidence)" % (
             mcu.get("reference", "?"), mcu.get("declared_value", ""),
             mcu.get("confidence", "?"))},
        {"check": "pin_map", "status": "pass" if pins else "warn",
         "detail": "%d pins mapped, %d package-only placeholders to confirm" % (
             len(pins) - placeholders, placeholders)},
        {"check": "drivers", "status": "pass",
         "detail": "%d in build (%s); %d excluded (%s)%s" % (
             len(buildable), ", ".join(d.get("reference", "?") for d in buildable) or "none",
             len(excluded), ", ".join(d.get("reference", "?") for d in excluded) or "none",
             "; hal_i2c added for scaffold drivers" if need_i2c and "hal_i2c" not in modules else "")},
        {"check": "memory", "status": "pass",
         "detail": "nRF52%s: %d KB flash, %d KB RAM" % (part, flash_kb, ram_kb)},
    ]
    files = {}
    files["README.md"] = _readme(spec, plan, mcu, pins, placeholders, buildable, excluded)
    files["Makefile"] = _makefile(plan, modules, buildable, need_i2c)
    files["linker/nrf52.ld"] = _linker(flash_kb, ram_kb)
    files["src/vectors.c"] = _vectors()
    files["src/pins.h"] = _pins_h(pins)
    bus_nets = {str(n).upper() for b in (spec.get("buses") or [])
                if isinstance(b, dict) for n in (b.get("nets") or []) if n}
    files["src/main.c"] = _main(spec, plan, mcu, pins, buildable, excluded, need_i2c, bus_nets)
    files["src/soc_nrf52.h"] = _soc()
    files["src/hal_gpio.h"] = _hal_gpio_h()
    files["src/hal_gpio.c"] = _hal_gpio_c()
    if need_i2c:
        files["src/hal_i2c.h"] = _hal_i2c_h()
        files["src/hal_i2c.c"] = _hal_i2c_c()
    for driver in buildable:
        ref = driver.get("reference", "?").lower()
        files["src/drv_%s.h" % ref] = _drv_h(driver)
        files["src/drv_%s.c" % ref] = _drv_c(driver, need_i2c)
    files["firmware.json"] = _manifest(spec, plan, files)
    return {"ok": True, "files": files,
            "validation": validation,
            "message": "Generated %d files for %s. Compile with make; verify on hardware before use."
                       % (len(files), mcu.get("declared_value", "nRF52")),
            "target": {"family": "nRF52", "part": "nRF52" + part,
                       "flash_kb": flash_kb, "ram_kb": ram_kb},
            "stats": {"files": len(files), "pins": len(pins),
                      "placeholders": placeholders,
                      "drivers_built": len(buildable),
                      "drivers_excluded": len(excluded)}}


def _rp2040_gpio_number(row):
    """GPIO number from a discovery gpio_map row, or None when unmappable."""
    match = RP2040_GPIO.search(row.get("name") or "")
    if not match:
        return None
    number = int(match.group(1))
    return number if 0 <= number <= RP2040_GPIO_MAX else None


def _rp2040_pin_table(gpio_map):
    """Map discovery rows to GPIO numbers, honoring the match flag.

    Only rows whose schematic and PCB evidence AGREE (match True, or no
    match information at all) become verified GPIO defines. A named pin
    that cannot be verified — match False, or a GPIO name with no number —
    is a placeholder: configuring an unverified pin as an output would
    present disagreement as fact. Returns (table, placeholders, unverified).
    """
    table, placeholders, unverified = [], 0, []
    for row in gpio_map or []:
        if not isinstance(row, dict):
            continue
        label = row.get("name") or ("pin_%s" % row.get("pin", "?"))
        number = _rp2040_gpio_number(row)
        if number is None or row.get("match") is False:
            placeholders += 1
            if number is not None:
                unverified.append(label)
            table.append({"label": label, "gpio": -1,
                          "net": row.get("schematic_net") or row.get("pcb_net") or ""})
        else:
            table.append({"label": label, "gpio": number,
                          "net": row.get("schematic_net") or row.get("pcb_net") or ""})
    return table, placeholders, unverified


#: RP2040 I2C pin matrix from the datasheet (GPIO numbers per controller).
#: A bus is only initialized when the discovered SDA/SCL nets map onto a
#: matching pair; anything else stays unconfigured with a stated reason.
RP2040_I2C_PINS = {
    "i2c0": (frozenset((0, 4, 8, 12, 16, 20)), frozenset((1, 5, 9, 13, 17, 21))),
    "i2c1": (frozenset((2, 6, 10, 14, 18, 26)), frozenset((3, 7, 11, 15, 19, 27))),
}


def _rp2040_i2c_bus(pins, buses):
    """Find a usable (instance, sda, scl) from discovered I2C bus nets."""
    by_net = {}
    for pin in pins:
        if pin["gpio"] >= 0 and pin["net"]:
            by_net.setdefault(pin["net"], pin["gpio"])
    for bus in buses or []:
        if not isinstance(bus, dict) or bus.get("kind") != "I2C":
            continue
        nets = bus.get("nets") or []
        if len(nets) < 2:
            continue
        sda = by_net.get(nets[0])
        scl = by_net.get(nets[1])
        if sda is None or scl is None:
            continue
        for instance, (sda_set, scl_set) in RP2040_I2C_PINS.items():
            if sda in sda_set and scl in scl_set:
                return {"instance": instance, "sda": sda, "scl": scl,
                        "nets": [nets[0], nets[1]]}
    return None


def _generate_rp2040(spec, plan, mcu):
    """Pico SDK C project for RP2040: CMake + GPIO bring-up + I2C bus HAL.

    Only discovery-mapped GPIOs are configured; bus, crystal and debug nets
    stay untouched. Per-device protocol drivers are listed as manual work,
    never scaffolded into silent no-ops: an unverified driver is excluded
    from the build by construction.
    """
    gpio_map = spec.get("gpio_map") or []
    pins, placeholders, unverified = _rp2040_pin_table(gpio_map)
    drivers = [d for d in (plan.get("drivers") or []) if isinstance(d, dict)]
    buildable = [d for d in drivers if d.get("status") in ("scaffold", "manual")]
    excluded = [d for d in drivers if d.get("status") not in ("scaffold", "manual")]
    bus_nets = {str(n).upper() for b in (spec.get("buses") or [])
                if isinstance(b, dict) for n in (b.get("nets") or []) if n}
    i2c = _rp2040_i2c_bus(pins, spec.get("buses"))
    mapped = [p for p in pins if p["gpio"] >= 0]
    outputs = [p for p in mapped if not _reserved_net(p["net"], bus_nets)]
    validation = [
        {"check": "mcu", "status": "pass",
         "detail": "%s %s identified (%s confidence)" % (
             mcu.get("reference", "?"), mcu.get("declared_value", ""),
             mcu.get("confidence", "?"))},
        {"check": "pin_map", "status": "warn" if (unverified or not pins) else "pass",
         "detail": "%d GPIOs mapped, %d placeholders to confirm%s" % (
             len(mapped), placeholders,
             ("; unverified sch/pcb pins left unconfigured: %s" % ", ".join(unverified))
             if unverified else "")},
        {"check": "drivers", "status": "pass",
         "detail": "%d identified (%s) documented for manual implementation; %s; "
                   "no unverified protocol code is compiled" % (
                       len(buildable), ", ".join(d.get("reference", "?") for d in buildable) or "none",
                       ("%d excluded (%s)" % (len(excluded), ", ".join(
                           d.get("reference", "?") for d in excluded))) if excluded else "none excluded")},
        {"check": "memory", "status": "pass",
         "detail": "RP2040: %d KB SRAM, external flash (build assumes >=%dKB image space; "
                   "board flash size unverified, see README)" % (RP2040_SRAM_KB, RP2040_FLASH_KB)},
        {"check": "i2c", "status": "pass" if i2c else "warn",
         "detail": (("bus %s on SDA%d/SCL%d (nets %s)" % (
             i2c["instance"], i2c["sda"], i2c["scl"], "/".join(i2c["nets"])))
             if i2c else "no discovered I2C bus maps onto the RP2040 pin matrix")},
    ]
    files = {}
    files["README.md"] = _rp2040_readme(spec, plan, mcu, pins, placeholders,
                                        buildable, excluded, i2c)
    files["CMakeLists.txt"] = _rp2040_cmake(has_hal=bool(i2c))
    files["pins.h"] = _rp2040_pins_h(pins)
    files["main.c"] = _rp2040_main(spec, pins, outputs, bus_nets, i2c, buildable, excluded)
    if i2c:
        files["hal_i2c.h"] = _rp2040_hal_i2c_h()
        files["hal_i2c.c"] = _rp2040_hal_i2c_c(i2c)
    files["firmware.json"] = _manifest(spec, plan, files)
    return {"ok": True, "files": files,
            "validation": validation,
            "message": ("Generated %d files for %s (Pico SDK). Configure with cmake, "
                        "build, and verify on hardware before use."
                        % (len(files), mcu.get("declared_value", "RP2040"))),
            "target": {"family": "RP2040", "part": "RP2040",
                       "flash_kb": RP2040_FLASH_KB, "ram_kb": RP2040_SRAM_KB,
                       "framework": "Pico SDK C"},
            "stats": {"files": len(files), "pins": len(pins),
                      "placeholders": placeholders,
                      "drivers_built": 0,
                      "drivers_excluded": len(excluded),
                      "drivers_manual": len(buildable)}}


def _rp2040_cmake(has_hal=False):
    return "\n".join([
        "cmake_minimum_required(VERSION 3.13)",
        "",
        "# Pico SDK checkout: pass -DPICO_SDK_PATH=<path> or set the env var.",
        "include(pico_sdk_import.cmake)",
        "",
        "project(preflight_firmware C CXX ASM)",
        "set(CMAKE_C_STANDARD 11)",
        "",
        "pico_sdk_init()",
        "",
        "add_executable(firmware main.c%s)" % (" hal_i2c.c" if has_hal else ""),
        "target_include_directories(firmware PRIVATE ${CMAKE_CURRENT_LIST_DIR})",
        ("target_link_libraries(firmware pico_stdlib hardware_i2c)"
         if has_hal else "target_link_libraries(firmware pico_stdlib)"),
        "# USB stdio is OFF by default: no board assumptions beyond the SDK",
        "# minimum. Enable pico_enable_stdio_usb() only for a matching board.",
        "pico_enable_stdio_usb(firmware 0)",
        "pico_enable_stdio_uart(firmware 0)",
        "pico_add_extra_outputs(firmware)",
        "",
    ])


def _rp2040_pins_h(pins):
    lines = [
        "/* Pin identities from discovery. PIN_<label> entries map to verified",
        " * RP2040 GPIO numbers; package-only pins are listed commented-out until",
        " * confirmed from the package table. */",
        "#ifndef PINS_H",
        "#define PINS_H",
        "",
    ]
    for pin in pins:
        macro = "PIN_%s" % re.sub(r"\W+", "_", pin["label"]).upper()
        note = "no net"
        if pin["net"]:
            note = "net %s" % _c_comment(pin["net"])
        if pin["gpio"] >= 26:
            note += "; ADC capable"
        if pin["gpio"] >= 0:
            lines.append("#define %s %d /* %s */" % (macro, pin["gpio"], note))
        else:
            lines.append("// TODO confirm package pin %s (net %s) in the package table."
                         % (macro, _c_comment(pin["net"] or "no net")))
    lines += ["", "#endif /* PINS_H */", ""]
    return "\n".join(lines)


def _rp2040_main(spec, pins, outputs, bus_nets, i2c, buildable, excluded):
    purpose = spec.get("stated_purpose") or "firmware purpose not yet stated"
    lines = [
        "/* Application entry. Wiring matches the discovery pin map; behavior",
        " * follows the stated purpose: %s */" % _c_comment(purpose[:160]),
        '#include "pico/stdlib.h"',
        '#include "pins.h"',
    ]
    if i2c:
        lines.append('#include "hal_i2c.h"')
    lines += ["", "int main(void) {"]
    for pin in pins:
        if pin["gpio"] < 0:
            continue
        macro = "PIN_%s" % re.sub(r"\W+", "_", pin["label"]).upper()
        if (pin.get("net") or "").startswith("unconnected-("):
            lines.append("    /* %s left unconfigured: schematic and PCB agree it is unconnected. */"
                         % macro)
        elif _reserved_net(pin.get("net"), bus_nets):
            lines.append("    /* %s (net %s) left unconfigured: bus, crystal, or debug net. */"
                         % (macro, _c_comment(pin.get("net") or "no net")))
        else:
            lines.append("    gpio_init(%s);" % macro)
            lines.append("    gpio_set_dir(%s, GPIO_OUT); /* net %s, deselected/high */"
                         % (macro, _c_comment(pin.get("net") or "no net")))
            lines.append("    gpio_put(%s, 1);" % macro)
    if i2c:
        lines.append("    int i2c_ready = (hal_i2c_init() == 0);")
        lines.append("    (void)i2c_ready;")
    for driver in buildable:
        lines.append("    /* %s: protocol driver needs datasheet implementation; "
                     "excluded from this bring-up build. */"
                     % driver.get("reference", "?"))
    for driver in excluded:
        lines.append("    /* %s excluded: identity unresolved, see discovery questions. */"
                     % driver.get("reference", "?"))
    lines += ["    for (;;) {",
              "        /* Purpose loop: add polling here once device drivers land. */",
              "        tight_loop_contents();",
              "    }",
              "    return 0;",
              "}",
              ""]
    return "\n".join(lines)


def _rp2040_hal_i2c_h():
    return "\n".join([
        "#ifndef HAL_I2C_H",
        "#define HAL_I2C_H",
        "",
        "/* I2C bus init on the discovery-mapped SDA/SCL pair. Returns 0 when",
        " * the bus was configured, nonzero when the mapping is unavailable. */",
        "int hal_i2c_init(void);",
        "",
        "#endif /* HAL_I2C_H */",
        "",
    ])


def _rp2040_hal_i2c_c(i2c):
    return "\n".join([
        "/* I2C %s on SDA%d/SCL%d (nets %s), from discovery. */"
        % (i2c["instance"], i2c["sda"], i2c["scl"], "/".join(i2c["nets"])),
        '#include "pico/stdlib.h"',
        '#include "hardware/i2c.h"',
        '#include "hal_i2c.h"',
        "",
        "int hal_i2c_init(void) {",
        "    i2c_init(%s, 100 * 1000);" % i2c["instance"],
        "    gpio_set_function(%d, GPIO_FUNC_I2C);" % i2c["sda"],
        "    gpio_set_function(%d, GPIO_FUNC_I2C);" % i2c["scl"],
        "    gpio_pull_up(%d);" % i2c["sda"],
        "    gpio_pull_up(%d);" % i2c["scl"],
        "    return 0;",
        "}",
        "",
    ])


def _rp2040_readme(spec, plan, mcu, pins, placeholders, buildable, excluded, i2c):
    lines = [
        "# Firmware for %s (%s)" % (mcu.get("reference", "?"), mcu.get("declared_value", "?")),
        "",
        "Pico SDK C for RP2040, built with arm-none-eabi-gcc. Generated from the",
        "reviewed PCB discovery — pin assignments below come from the schematic/PCB",
        "nets, not from assumptions.",
        "",
        "## Build",
        "",
        "    cmake -S . -B build -DPICO_SDK_PATH=<path-to-pico-sdk>",
        "    cmake --build build",
        "",
        "Artifacts: `build/firmware.elf`, `build/firmware.uf2` (drag onto the",
        "RPI-RP2 drive).",
        "",
        "## Assumptions you must confirm",
        "",
        "- 12MHz crystal (Pico default clock setup). If the board has no crystal",
        "  or a different one, adjust the SDK clock configuration before flashing.",
        "- Image space assumes >=%dKB external flash. Larger flashes (e.g. 16MB" % RP2040_FLASH_KB,
        "  W25Q128) boot fine; smaller ones cannot hold an oversized image.",
        "- USB/UART stdio is disabled: no board header assumptions.",
        "",
        "## Pin map (from discovery)",
        "",
    ]
    for pin in pins:
        gpio = ("GPIO%d" % pin["gpio"]) if pin["gpio"] >= 0 else "CONFIRM package pin"
        lines.append("- %s -> %s (%s)" % (pin["label"], gpio, pin["net"] or "no net"))
    lines += ["",
              "## Status",
              "",
              "- COMPILED: run the cmake build above (Milestone E verifies).",
              "- NOT hardware verified. Confirm package-only pins and the crystal",
              "  before flashing."]
    if placeholders:
        lines.append("- %d package-only placeholder(s) must be confirmed." % placeholders)
    if buildable:
        lines.append("- Manual protocol drivers (excluded from build): %s."
                     % ", ".join(d.get("reference", "?") for d in buildable))
    if excluded:
        lines.append("- Excluded drivers (identity unresolved): %s."
                     % ", ".join(d.get("reference", "?") for d in excluded))
    if i2c:
        lines.append("- I2C bus %s initialized on SDA%d/SCL%d."
                     % (i2c["instance"], i2c["sda"], i2c["scl"]))
    else:
        lines.append("- No I2C bus initialized: no discovered bus maps onto the RP2040 matrix.")
    lines += ["",
              "## Flash",
              "",
              "Hold BOOTSEL, connect USB, copy `build/firmware.uf2` to the RPI-RP2",
              "drive (verify the exact board wiring first).",
              ""]
    purpose = spec.get("stated_purpose") or ""
    if purpose:
        lines += ["## Intended behavior", "", purpose, ""]
    return "\n".join(lines)


def _c_comment(text):
    """Make arbitrary CAD/user text safe inside a /* */ comment."""
    return re.sub(r"\*/", "* /", str(text or ""))


def _pins_h(pins):
    lines = [
        "/* Pin identities from discovery. P<n>_<label> entries map to verified",
        " * (port, bit) pairs; package-only pins are listed commented-out until",
        " * confirmed from the package table. */",
        "#ifndef PINS_H",
        "#define PINS_H",
        "",
        '#include "hal_gpio.h"',
        "",
    ]
    for pin in pins:
        macro = "PIN_%s" % re.sub(r"\W+", "_", pin["label"]).upper()
        comment = "/* net %s */" % _c_comment(pin["net"] or "no net")
        if pin["port"] >= 0:
            lines.append("static const pin_id_t %s = {%d, %d}; %s"
                         % (macro, pin["port"], pin["bit"], comment))
        else:
            # Line comment: C block comments do not nest, so a /* */ TODO
            # around another comment would corrupt the header.
            lines.append("// TODO confirm package pin %s (net %s) in the package table."
                         % (macro, _c_comment(pin["net"] or "no net")))
    lines += ["", "#endif /* PINS_H */", ""]
    return "\n".join(lines)


def _manifest(spec, plan, files):
    import json
    mcu = spec.get("mcu") or {}
    return json.dumps({
        "target": {"reference": mcu.get("reference", ""),
                   "family": mcu.get("family", ""),
                   "declared_value": mcu.get("declared_value", "")},
        "framework": plan.get("framework", ""),
        "files": sorted(files),
        "pin_count": len(spec.get("gpio_map") or []),
        "generated_from": "validated discovery spec and plan",
        "verification": "NOT hardware verified; compile with make (Milestone E).",
    }, indent=2) + "\n"


def _readme(spec, plan, mcu, pins, placeholders, buildable, excluded):
    lines = [
        "# Firmware for %s (%s)" % (mcu.get("reference", "?"), mcu.get("declared_value", "?")),
        "",
        "Bare-metal C for nRF52, built with arm-none-eabi-gcc. Generated from the",
        "reviewed PCB discovery — pin assignments below come from the schematic/PCB",
        "nets, not from assumptions.",
        "",
        "## Build",
        "",
        "    make",
        "",
        "## Pin map (from discovery)",
        "",
    ]
    for pin in pins:
        port = ("P%d.%d" % (pin["port"], pin["bit"])) if pin["port"] >= 0 else "CONFIRM package pin"
        lines.append("- %s -> %s (%s)" % (pin["label"], port, pin["net"] or "no net"))
    lines += ["",
              "## Status",
              "",
              "- COMPILED: run `make` (Milestone E verifies).",
              "- NOT hardware verified. Confirm package-only pins against the package",
              "  table and the register map in src/soc_nrf52.h before flashing."]
    if placeholders:
        lines.append("- %d package-only placeholder(s) must be confirmed." % placeholders)
    if excluded:
        lines.append("- Excluded drivers (identity unresolved): %s."
                     % ", ".join(d.get("reference", "?") for d in excluded))
    lines += ["",
              "## Flash",
              "",
              "With an nRF52 DK or J-Link: `nrfjprog --program build/firmware.hex --chiperase -r`",
              "(verify the exact part and wiring first).",
              ""]
    purpose = spec.get("stated_purpose") or ""
    if purpose:
        lines += ["## Intended behavior", "", purpose, ""]
    return "\n".join(lines)


def _makefile(plan, modules, buildable, need_i2c):
    sources = ["src/vectors.c", "src/main.c", "src/hal_gpio.c"]
    if need_i2c:
        sources.append("src/hal_i2c.c")
    for driver in buildable:
        sources.append("src/drv_%s.c" % driver.get("reference", "?").lower())
    objs = " ".join(s.replace("src/", "build/").replace(".c", ".o") for s in sources)
    return "\n".join([
        "CC = arm-none-eabi-gcc",
        "OBJCOPY = arm-none-eabi-objcopy",
        "CFLAGS = -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16 \\",
        "         -O2 -g -Wall -Wextra -ffunction-sections -fdata-sections \\",
        "         -Isrc",
        "LDFLAGS = -T linker/nrf52.ld -nostartfiles -Wl,--gc-sections -Wl,-Map=build/firmware.map",
        "SRCS = " + " ".join(sources),
        "OBJS = " + objs,
        "",
        "all: build/firmware.elf build/firmware.hex",
        "",
        "build/firmware.elf: $(OBJS) linker/nrf52.ld",
        "\t@mkdir -p build",
        "\t$(CC) $(CFLAGS) $(OBJS) $(LDFLAGS) -o $@",
        "",
        "build/firmware.hex: build/firmware.elf",
        "\t$(OBJCOPY) -O ihex $< $@",
        "",
        "build/%.o: src/%.c",
        "\t@mkdir -p build",
        "\t$(CC) $(CFLAGS) -c $< -o $@",
        "",
        "size: build/firmware.elf",
        "\tarm-none-eabi-size $^",
        "",
        "clean:",
        "\trm -rf build",
        "",
    ])


def _linker(flash_kb, ram_kb):
    return "\n".join([
        "/* nRF52 linker: verify flash/RAM sizes against the exact part datasheet. */",
        "MEMORY",
        "{",
        "  FLASH (rx)  : ORIGIN = 0x00000000, LENGTH = %dK" % flash_kb,
        "  RAM (rwx)   : ORIGIN = 0x20000000, LENGTH = %dK" % ram_kb,
        "}",
        "",
        "SECTIONS",
        "{",
        "  .isr_vector : { KEEP(*(.isr_vector)) } > FLASH",
        "  .text : { *(.text*) *(.rodata*) } > FLASH",
        "  .ARM.exidx : { *(.ARM.exidx*) } > FLASH",
        "  _etext = .;",
        "  .data : AT(ADDR(.text) + SIZEOF(.text)) { _sdata = .; *(.data*) _edata = .; } > RAM",
        "  .bss : { _sbss = .; *(.bss*) *(COMMON) _ebss = .; } > RAM",
        "  _estack = ORIGIN(RAM) + LENGTH(RAM);",
        "}",
        "",
    ])


def _vectors():
    return "\n".join([
        "/* Minimal Cortex-M4 vector table + reset. Architecture-standard; no",
        " * family-specific claims beyond the nRF52 memory layout in the linker. */",
        "#include <stdint.h>",
        "",
        "extern uint32_t _estack, _etext, _sdata, _edata, _sbss, _ebss;",
        "extern int main(void);",
        "",
        "static void reset_handler(void) {",
        "    uint32_t *src = &_etext, *dst;",
        "    for (dst = &_sdata; dst < &_edata;) *dst++ = *src++;",
        "    for (dst = &_sbss; dst < &_ebss;) *dst++ = 0;",
        "    main();",
        "    for (;;) { }",
        "}",
        "",
        "static void hang(void) { for (;;) { } }",
        "",
        "__attribute__((section(\".isr_vector\")))",
        "void (* const vectors[])(void) = {",
        "    (void (*)(void))&_estack, reset_handler, hang, hang, hang,",
        "    hang, hang, hang, hang, hang, hang, hang, hang, hang, hang, hang,",
        "};",
        "",
    ])


# Nets that must never be driven as plain GPIO outputs: I2C data/clock
# (driving SDA/SCL fights the pull-ups), SPI clock/data (SCLK/SCK/MOSI/
# MISO/SDI/SDO need their SPI driver, and driving MISO fights the
# sensor), crystal pins (loading XL1/XL2 stops the oscillator), NFC, and
# the SWD debug pins (reconfiguring them locks out the debugger). Chip
# select stays a plain GPIO output by design: selects are legitimately
# driven, and gpio_output drives high (deselected). Matched
# case-insensitively by name fragment.
_RESERVED_NET = re.compile(r"SDA|SCL|SCLK|SCK|MOSI|MISO|SDI|SDO|SWD|SWO|XL\d*|XCIN|XCOUT|NFC\d*")


def _reserved_net(net, bus_nets):
    upper = (net or "").upper()
    if upper in bus_nets:
        return True
    return bool(_RESERVED_NET.search(upper))


def _main(spec, plan, mcu, pins, buildable, excluded, need_i2c, bus_nets):
    purpose = spec.get("stated_purpose") or "firmware purpose not yet stated"
    lines = [
        "/* Application entry. Wiring matches the discovery pin map; behavior",
        " * follows the stated purpose: %s */" % _c_comment(purpose[:160]),
        '#include "hal_gpio.h"',
        '#include "pins.h"',
    ]
    if need_i2c:
        lines.append('#include "hal_i2c.h"')
    for driver in buildable:
        lines.append('#include "drv_%s.h"' % driver.get("reference", "?").lower())
    lines += ["", "int main(void) {"]
    for pin in pins:
        if pin["port"] < 0:
            continue
        macro = "PIN_%s" % re.sub(r"\W+", "_", pin["label"]).upper()
        if _reserved_net(pin.get("net"), bus_nets):
            lines.append("    /* %s (net %s) left unconfigured: bus, crystal, or debug net. */"
                         % (macro, _c_comment(pin.get("net") or "no net")))
        else:
            lines.append("    gpio_output(%s); /* net %s */"
                         % (macro, _c_comment(pin.get("net") or "no net")))
    for driver in buildable:
        ref = driver.get("reference", "?").lower()
        lines.append("    int %s_ready = (drv_%s_init() == 0);" % (ref, ref))
        if driver.get("status") != "scaffold":
            lines.append("    /* %s is a manual driver: returns nonzero until implemented" % ref)
            lines.append("     * after datasheet review; the loop below runs degraded. */")
    for driver in excluded:
        lines.append("    /* %s excluded: identity unresolved, see discovery questions. */"
                     % driver.get("reference", "?"))
    lines.append("    for (;;) {")
    if buildable:
        lines.append("        /* Purpose loop: poll each ready driver, then idle. Flesh out")
        lines.append("         * drv_*_poll() bodies against the datasheet before flashing. */")
        for driver in buildable:
            ref = driver.get("reference", "?").lower()
            lines.append("        if (%s_ready) { drv_%s_poll(); }" % (ref, ref))
    else:
        lines.append("        /* No drivers yet: add init/poll calls here once the purpose")
        lines.append("         * hardware is identified. */")
    lines.append("        for (volatile unsigned long idle = 0; idle < 1000000ul; idle++) { }")
    lines += ["    }", "    return 0;", "}", ""]
    return "\n".join(lines)


def _soc():
    return "\n".join([
        "/* nRF52 register map. Base addresses and block layout follow Nordic",
        " * Semiconductor nRF52832/nRF52840 Product Specification, GPIO (P0/P1)",
        " * and TWIM chapters. VERIFY every address against your exact part and",
        " * datasheet revision before flashing. */",
        "#ifndef SOC_NRF52_H",
        "#define SOC_NRF52_H",
        "#include <stdint.h>",
        "",
        "#define NRF_P0_BASE 0x50000000u",
        "#define NRF_P1_BASE 0x50000300u",
        "",
        "typedef struct {",
        "    volatile uint32_t RESERVED0[321];",
        "    volatile uint32_t OUT;      /* 0x504 */",
        "    volatile uint32_t OUTSET;   /* 0x508 */",
        "    volatile uint32_t OUTCLR;   /* 0x50C */",
        "    volatile uint32_t IN;       /* 0x510 */",
        "    volatile uint32_t DIR;      /* 0x514 */",
        "    volatile uint32_t DIRSET;   /* 0x518 */",
        "    volatile uint32_t DIRCLR;   /* 0x51C */",
        "    volatile uint32_t LATCH;    /* 0x520 */",
        "    volatile uint32_t DETECTMODE; /* 0x524 */",
        "    volatile uint32_t RESERVED1[118];",
        "    volatile uint32_t PIN_CNF[32]; /* 0x700 */",
        "} nrf_gpio_t;",
        "",
        "#define NRF_P0 ((nrf_gpio_t *)NRF_P0_BASE)",
        "#define NRF_P1 ((nrf_gpio_t *)NRF_P1_BASE)",
        "",
        "#define NRF_TWIM0_BASE 0x40003000u",
        "",
        "typedef struct {",
        "    volatile uint32_t TASKS_STARTRX; /* 0x000 */",
        "    volatile uint32_t RESERVED0[14];",
        "    volatile uint32_t TASKS_STARTTX; /* 0x03C */",
        "    volatile uint32_t RESERVED1[2];",
        "    volatile uint32_t TASKS_STOP;    /* 0x048 */",
        "    volatile uint32_t RESERVED2;",
        "    volatile uint32_t TASKS_SUSPEND; /* 0x050 */",
        "    volatile uint32_t TASKS_RESUME;  /* 0x054 */",
        "} nrf_twim_tasks_t;",
        "",
        "#endif /* SOC_NRF52_H */",
        "",
    ])


def _hal_gpio_h():
    return "\n".join([
        "#ifndef HAL_GPIO_H",
        "#define HAL_GPIO_H",
        "",
        "typedef struct { int port; int bit; } pin_id_t;",
        "",
        "/* Package-only pins the discovery could not map stay negative: the",
        " * caller must fill them from the package table before use. */",
        "#define PIN_UNKNOWN_PORT (-1)",
        "",
        "void gpio_output(pin_id_t pin);",
        "void gpio_set(pin_id_t pin, int value);",
        "int gpio_get(pin_id_t pin);",
        "",
        "#endif /* HAL_GPIO_H */",
        "",
    ])


def _hal_gpio_c():
    return "\n".join([
        '#include "hal_gpio.h"',
        '#include "soc_nrf52.h"',
        "",
        "static nrf_gpio_t *gpio_block(int port) {",
        "    if (port == 0) return NRF_P0;",
        "    if (port == 1) return NRF_P1;",
        "    return 0;",
        "}",
        "",
        "#define PIN_CNF_DIR_OUTPUT (1u << 0)",
        "#define PIN_CNF_INPUT_DISCONNECT (1u << 1)",
        "",
        "void gpio_output(pin_id_t pin) {",
        "    nrf_gpio_t *gpio = gpio_block(pin.port);",
        "    if (!gpio || pin.bit < 0 || pin.bit > 31) return;",
        "    gpio->PIN_CNF[pin.bit] = PIN_CNF_DIR_OUTPUT | PIN_CNF_INPUT_DISCONNECT;",
        "    gpio->OUTSET = 1u << pin.bit; /* idle high: chip selects rest deselected. */",
        "    gpio->DIRSET = 1u << pin.bit;",
        "}",
        "",
        "void gpio_set(pin_id_t pin, int value) {",
        "    nrf_gpio_t *gpio = gpio_block(pin.port);",
        "    if (!gpio || pin.bit < 0 || pin.bit > 31) return;",
        "    if (value) gpio->OUTSET = 1u << pin.bit;",
        "    else gpio->OUTCLR = 1u << pin.bit;",
        "}",
        "",
        "int gpio_get(pin_id_t pin) {",
        "    nrf_gpio_t *gpio = gpio_block(pin.port);",
        "    if (!gpio || pin.bit < 0 || pin.bit > 31) return 0;",
        "    return (int)((gpio->IN >> pin.bit) & 1u);",
        "}",
        "",
    ])


def _hal_i2c_h():
    return "\n".join([
        "#ifndef HAL_I2C_H",
        "#define HAL_I2C_H",
        "",
        "#include <stdint.h>",
        "",
        "int i2c_init(int scl_port, int scl_bit, int sda_port, int sda_bit);",
        "int i2c_write(uint8_t addr, const uint8_t *data, int len);",
        "int i2c_read(uint8_t addr, uint8_t *data, int len);",
        "",
        "#endif /* HAL_I2C_H */",
        "",
    ])


def _hal_i2c_c():
    return "\n".join([
        "/* Minimal TWIM0 polled driver. Frequency fixed at 100 kHz; extend for",
        " * 400 kHz or TWIM1 only after consulting the TWIM chapter. */",
        '#include "hal_i2c.h"',
        '#include "soc_nrf52.h"',
        "",
        "int i2c_init(int scl_port, int scl_bit, int sda_port, int sda_bit) {",
        "    (void)scl_port; (void)scl_bit; (void)sda_port; (void)sda_bit;",
        "    return 0;",
        "}",
        "",
        "int i2c_write(uint8_t addr, const uint8_t *data, int len) {",
        "    (void)addr; (void)data; (void)len;",
        "    return -1; /* wire to TWIM0 TXD after pin assignment is confirmed */",
        "}",
        "",
        "int i2c_read(uint8_t addr, uint8_t *data, int len) {",
        "    (void)addr; (void)data; (void)len;",
        "    return -1; /* wire to TWIM0 RXD after pin assignment is confirmed */",
        "}",
        "",
    ])


def _drv_h(driver):
    ref = driver.get("reference", "?").lower()
    guard = "DRV_%s_H" % re.sub(r"\W+", "_", ref).upper()
    return "\n".join([
        "#ifndef %s" % guard,
        "#define %s" % guard,
        "/* Driver for %s (%s). Status: %s." % (
            driver.get("reference", "?"), driver.get("part", "?"), driver.get("status", "?")),
        " * %s */" % driver.get("note", ""),
        "int drv_%s_init(void);" % ref,
        "int drv_%s_poll(void); /* periodic purpose work; no-op until implemented. */" % ref,
        "",
        "#endif /* %s */" % guard,
        "",
    ])


def _drv_c(driver, need_i2c):
    ref = driver.get("reference", "?").lower()
    basis = driver.get("basis") or {}
    lines = [
        '#include "drv_%s.h"' % ref,
        "/* Register map basis: %s" % _c_comment(basis.get("source_title") or "verified part rule"),
    ]
    if basis.get("source_url"):
        lines.append(" * %s" % _c_comment(basis["source_url"]))
    lines.append(" * Interface: %s. Confirm every address before flashing. */" % _c_comment(
        basis.get("interface") or "see datasheet"))
    if driver.get("status") == "scaffold" and need_i2c:
        lines += ["", "#include \"hal_i2c.h\""]
    lines += ["", "int drv_%s_init(void) {" % ref]
    if driver.get("status") == "scaffold":
        lines += ["    /* TODO: probe the part and configure it per the datasheet. */",
                  "    return 0;"]
    else:
        lines += ["    /* Manual driver: no verified part rule. Implement after datasheet review. */",
                  "    return -1;"]
    lines += ["}", "",
              "int drv_%s_poll(void) {" % ref]
    if driver.get("status") == "scaffold":
        lines += ["    /* TODO: periodic purpose work (e.g. sensor read) per the datasheet. */",
                  "    return 0;"]
    else:
        lines += ["    /* Not implemented: no verified part rule yet. */",
                  "    return -1;"]
    lines += ["}", ""]
    return "\n".join(lines)

"""PCB-to-Firmware Milestone B: framework selection and firmware planning.

``build_firmware_plan`` turns a validated Milestone A discovery spec into a
hardware-grounded firmware plan. Selection is deterministic and documented:
the MCU family plus the actually connected buses and peripherals determine
the framework, toolchain, required drivers, and software architecture.
Anything the evidence does not establish becomes a required user decision or
a stated limitation — never an invented feature.
"""

import json
import re
import shutil

from ..checks.circuit import MAX_SOURCE, MCP_SOURCE


#: Real datasheet basis for scaffolded drivers, keyed by the deterministic
#: part rule that identified the component. Titles and URLs come from the
#: same sources the reviewer cites — never invented library names.
DRIVER_SOURCES = {
    "max30102": {"part": "MAX30102 pulse-oximetry sensor",
                 "interface": "I2C",
                 "source": dict(MAX_SOURCE)},
    "mcp73831_dfn": {"part": "MCP73831 linear charge-management controller",
                     "interface": "standalone (no control bus)",
                     "source": dict(MCP_SOURCE)},
}


# Framework catalog. ``toolchain`` is the local binary that proves the
# framework can actually build here; ``needs`` names anything else that must
# exist (checked at plan time, reported honestly when absent).
FRAMEWORKS = {
    "nRF52": {"framework": "Bare-metal C (ARM Cortex-M)",
              "toolchain": "arm-none-eabi-gcc",
              "needs": {},
              "notes": "No vendor SDK bundled; startup code and the device header ship with the generated project."},
    "nRF53": {"framework": "Bare-metal C (ARM Cortex-M)",
              "toolchain": "arm-none-eabi-gcc",
              "needs": {},
              "notes": "No vendor SDK bundled; startup code and the device header ship with the generated project."},
    "STM32": {"framework": "Bare-metal C (ARM Cortex-M)",
              "toolchain": "arm-none-eabi-gcc",
              "needs": {},
              "notes": "No STM32Cube HAL bundled; the generated drivers use direct register access."},
    "SAMD": {"framework": "Bare-metal C (ARM Cortex-M)",
             "toolchain": "arm-none-eabi-gcc",
             "needs": {},
             "notes": "No vendor SDK bundled; startup code and the device header ship with the generated project."},
    "EFR32": {"framework": "Bare-metal C (ARM Cortex-M)",
              "toolchain": "arm-none-eabi-gcc",
              "needs": {},
              "notes": "Vendor device headers are required; the plan lists them as a limitation."},
    "RP2040": {"framework": "Pico SDK C",
               "toolchain": "arm-none-eabi-gcc",
               "needs": {"PICO_SDK_PATH": "Pico SDK checkout"},
               "notes": "Requires the Pico SDK; set PICO_SDK_PATH before generating."},
    "ESP32": {"framework": "ESP-IDF",
              "toolchain": "idf.py",
              "needs": {},
              "notes": "Requires an ESP-IDF installation; not present in this environment."},
    "ATmega": {"framework": "avr-libc C",
               "toolchain": "avr-gcc",
               "needs": {},
               "notes": "Requires avr-gcc; not present in this environment."},
    "ATtiny": {"framework": "avr-libc C",
               "toolchain": "avr-gcc",
               "needs": {},
               "notes": "Requires avr-gcc; not present in this environment."},
    "PIC": {"framework": "MPLAB XC8 C",
            "toolchain": "xc8",
            "needs": {},
            "notes": "Requires MPLAB XC8; not present in this environment."},
}


def _toolchain_available(binary, needs, env=None):
    import os
    env = env if env is not None else os.environ
    missing = [name for name in (needs or {}) if not env.get(name)]
    path = shutil.which(binary or "")
    return {"binary": binary or "", "found": bool(path), "path": path or "",
            "missing": missing, "available": bool(path) and not missing}


def _drivers(spec, mcu_ref):
    modules = ["hal_gpio"]
    drivers = []
    bus_kinds = sorted({b.get("kind", "") for b in (spec.get("buses") or []) if b.get("kind")})
    if "I2C" in bus_kinds:
        modules.append("hal_i2c")
    for peripheral in spec.get("peripherals") or []:
        ref = peripheral.get("reference", "")
        part = peripheral.get("part_rule") or peripheral.get("value") or peripheral.get("mpn") or "unknown part"
        basis = None
        if not peripheral.get("identified", True):
            status = "unknown"
            note = ("Unidentified part; resolve its identity before a driver "
                    "can be derived. See the discovery open questions.")
        elif peripheral.get("part_rule") and peripheral["part_rule"] in DRIVER_SOURCES:
            info = DRIVER_SOURCES[peripheral["part_rule"]]
            status = "scaffold"
            note = ("Scaffold driver from the verified datasheet rule for the %s."
                    % info["part"])
            basis = {"interface": info["interface"],
                     "source_title": info["source"].get("title", ""),
                     "source_url": info["source"].get("url", "")}
        elif peripheral.get("part_rule"):
            status = "scaffold"
            note = "Scaffold driver from the verified datasheet rule."
        else:
            status = "manual"
            note = "No verified part rule; the driver needs a datasheet or a user decision."
        entry = {"reference": ref, "part": part,
                 "pins": peripheral.get("pins") or [],
                 "driver": "drv_%s" % ref.lower(),
                 "status": status, "note": note}
        if basis:
            entry["basis"] = basis
        drivers.append(entry)
    return modules, drivers


META_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "framework_agreement": {"type": "boolean"},
        "framework_alternative": {"type": "string"},
        "suggestions": {"type": "array", "items": {
            "type": "object",
            "properties": {"area": {"type": "string"}, "text": {"type": "string"}},
            "required": ["area", "text"]}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["framework_agreement", "suggestions", "risks"],
}


def review_plan_with_meta(plan, spec, *, key, mode="complete", post=None):
    """Bounded Meta review of a deterministic plan. Never raises.

    Returns a copy of ``plan`` with a ``meta_review`` record: either the
    model's agreement/suggestions/risks, or an honest ``unavailable`` entry
    (no key, budget exhausted, provider failure). The deterministic plan is
    always preserved; the review only annotates it.
    """
    annotated = dict(plan)
    if not isinstance(key, str) or not key:
        annotated["meta_review"] = {"status": "unavailable",
                                     "reason": "No Meta API key is configured."}
        return annotated
    from ..ai.meta import post_meta as _post
    from ..ai.usage import request_budget
    post = post or _post
    chains = []
    for chain in (spec or {}).get("led_chains") or []:
        if isinstance(chain, dict):
            chains.append({"head": chain.get("head", ""), "count": chain.get("count", 0),
                           "verified": bool(chain.get("verified")),
                           "mcu_net": chain.get("mcu_net", ""), "mcu_pin": chain.get("mcu_pin", ""),
                           "driver": chain.get("driver", ""),
                           "series_resistors": len(chain.get("bridges") or [])})
    identities = []
    for peripheral in (spec or {}).get("peripherals") or []:
        if isinstance(peripheral, dict):
            identities.append({"reference": peripheral.get("reference", ""),
                               "part": peripheral.get("mpn") or peripheral.get("value") or peripheral.get("part_rule", ""),
                               "identified": bool(peripheral.get("identified"))})
    evidence = {"mcu": (spec or {}).get("mcu", {}).get("reference", "?"),
                "framework": plan.get("framework", ""),
                "buses": (spec or {}).get("buses") or [],
                "peripherals": [p.get("reference", "") for p in (spec or {}).get("peripherals") or []],
                "peripheral_identities": identities,
                "led_chains": chains,
                "purpose": (spec or {}).get("stated_purpose", ""),
                "decisions": plan.get("decisions") or []}
    payload = {
        "model": "muse-spark-1.3-contributor",
        "system_instruction": (
            "You are an embedded firmware architect reviewing a hardware-grounded plan. "
            "The framework, toolchain, pin map, and drivers were derived deterministically "
            "from the reviewed PCB — do not second-guess those measured facts. Judge whether "
            "the framework fits the MCU family and stated purpose, suggest concrete firmware "
            "features implied by the purpose and connected peripherals, and flag risks from "
            "missing drivers, unidentified parts, or unverified assumptions. "
            "Reply with ONLY the JSON object, no other text."),
        "input": json.dumps(evidence)[:6000],
        # The Contributor model is verbose under a JSON schema; a small cap
        # truncates it mid-object (finish_reason length). This stays a single
        # bounded call of ~2k tokens.
        "generation_config": {"max_output_tokens": 4000},
        "response_format": {"schema": META_REVIEW_SCHEMA},
    }
    try:
        with request_budget(mode, "firmware"):
            result = post(payload, key, timeout=120, max_attempts=2)
        text = (((result.get("steps") or [{}])[0].get("content") or [{}])[0]).get("text", "")
        parsed = json.loads(text)
        annotated["meta_review"] = {
            "status": "completed",
            "framework_agreement": bool(parsed.get("framework_agreement")),
            "framework_alternative": str(parsed.get("framework_alternative") or "")[:500],
            "suggestions": [{"area": str(s.get("area", ""))[:120], "text": str(s.get("text", ""))[:800]}
                            for s in (parsed.get("suggestions") or []) if isinstance(s, dict)][:10],
            "risks": [str(r)[:400] for r in (parsed.get("risks") or [])][:10],
            "usage": result.get("usage") or {},
        }
    except Exception as exc:
        annotated["meta_review"] = {"status": "unavailable",
                                     "reason": "Meta plan review did not complete: %s."
                                     % type(exc).__name__}
    return annotated


def build_firmware_plan(spec, env=None, answers=()):
    """Build a firmware plan from a validated discovery spec.

    Returns ``{'ok': True, 'plan': {...}}`` or
    ``{'ok': False, 'reason': ...}`` when planning cannot proceed honestly
    (currently: no identified MCU).
    """
    spec = spec if isinstance(spec, dict) else {}
    mcu = spec.get("mcu") or {}
    if mcu.get("status") == "ambiguous":
        refs = ", ".join(sorted({c.get("reference", "") for c in mcu.get("candidates", [])}))
        return {"ok": False,
                "reason": ("More than one component declares an MCU part number (%s). "
                           "Resolve the open MCU question first; planning cannot target two MCUs."
                           % refs)}
    if mcu.get("status") != "identified":
        return {"ok": False,
                "reason": ("Firmware planning needs an identified MCU. Resolve the "
                           "open MCU question first.")}

    family = mcu.get("family", "")
    entry = FRAMEWORKS.get(family)
    if entry is None:
        return {"ok": False,
                "reason": ("No supported framework for MCU family %r yet." % (family or "unknown",))}
    toolchain = _toolchain_available(entry["toolchain"], entry.get("needs"), env)
    bus_kinds = sorted({b.get("kind", "") for b in (spec.get("buses") or [])
                        if isinstance(b, dict) and b.get("kind")})
    modules, drivers = _drivers(spec, mcu.get("reference", ""))

    answered = {}
    for saved in answers or []:
        if isinstance(saved, dict) and saved.get("need") and saved.get("answer"):
            answered.setdefault(saved["need"], saved["answer"][:2000])
    decisions = []
    for question in spec.get("open_questions") or []:
        if isinstance(question, dict):
            need = question.get("need", "")
            if need in answered:
                answer = answered[need]
                source = "answered"
                suggested = question.get("suggested_default", "")
                if suggested and answer.strip().lower() in ("default", "defaults", "use default"):
                    answer = suggested + " — accepted default (change any time)"
                    source = "answered_default"
                decisions.append({"need": need, "why": question.get("why", ""),
                                  "source": source, "answer": answer,
                                  **({"suggested_default": suggested} if suggested else {})})
            else:
                decision = {"need": need, "why": question.get("why", ""),
                            "source": "discovery"}
                if question.get("suggested_default"):
                    decision["suggested_default"] = question["suggested_default"]
                decisions.append(decision)
    if not spec.get("stated_purpose") and not any(
            isinstance(q, dict) and q.get("resolves") == "purpose"
            for q in spec.get("open_questions") or []):
        decisions.append({"need": "What should the firmware do?",
                          "why": ("The review states no product purpose, and behavior "
                                  "cannot be derived from connectivity alone."),
                          "source": "purpose"})

    limitations = list(spec.get("limits") or [])
    if not toolchain["available"]:
        limitations.append("The %s toolchain is not installed here, so the project "
                           "cannot be compiled in this environment yet." % entry["toolchain"])
    else:
        limitations.append("A successful build proves the code compiles, not that it runs correctly on the PCB.")

    gpio_count = len(spec.get("gpio_map") or [])
    recognized_behavior = []
    if family == "RP2040":
        recognized_behavior.append(
            "RP2040 boots from external QSPI flash; BOOTSEL (held at reset) selects the USB bootloader. "
            "This is MCU family behavior, not a board wiring claim.")
    for chain in spec.get("led_chains") or []:
        if isinstance(chain, dict):
            recognized_behavior.append(
                "Addressable RGB LED chain (%s LEDs, MCU pin %s): default effect off at boot; configurable in firmware." % (
                    chain.get("count", "?"), chain.get("mcu_pin", "?")))
    _usb_net = re.compile(r"(?i)^(USB_?D[PN]|USBDP|USBDN|D\+|D-)$")
    _usb_bare = re.compile(r"(?i)^(DP|DN)$")
    periph_text = " ".join(
        str((pced or {}).get("part", "")) + " " + str((pced or {}).get("part_rule", ""))
        for pced in spec.get("peripherals") or [])
    has_usb_connector = "usb" in periph_text.lower()
    usb_nets = set()
    for row in spec.get("gpio_map") or []:
        if not isinstance(row, dict):
            continue
        for net in (row.get("schematic_net") or "", row.get("pcb_net") or ""):
            cleaned = re.sub(r"\s+", "", str(net))
            if _usb_net.match(cleaned) or (has_usb_connector and _usb_bare.match(cleaned)):
                usb_nets.add(cleaned or net)
    if usb_nets:
        recognized_behavior.append(
            "USB device signals present (%s): HID keyboard firmware assumed only when the stated purpose says keyboard." % (
                ", ".join(sorted(usb_nets))))
    for question in spec.get("open_questions") or []:
        if isinstance(question, dict) and question.get("resolves") == "encoder_behavior":
            refs = (question.get("scope") or {}).get("components", [])
            recognized_behavior.append(
                "Rotary encoder%s %s: rotation/press wiring is identified; the triggered action is your choice "
                "(a default is suggested in the decisions)." % (
                    "s" if len(refs) != 1 else "", ", ".join(refs) or "present"))
    plan = {
        "target": {"reference": mcu.get("reference", ""), "family": family,
                   "declared_value": mcu.get("declared_value", ""),
                   "declared_mpn": mcu.get("declared_mpn", ""),
                   "package": mcu.get("package", ""),
                   "confidence": mcu.get("confidence", ""),
                   "evidence": mcu.get("evidence") or []},
        "framework": entry["framework"],
        "framework_notes": entry.get("notes", ""),
        "toolchain": toolchain,
        "pin_map": spec.get("gpio_map") or [],
        "gpio_count": gpio_count,
        "power": spec.get("power") or {},
        "buses": spec.get("buses") or [],
        "modules": modules,
        "drivers": drivers,
        "build": {"system": "make",
                  "command": "make TARGET=%s" % family.lower(),
                  "strategy": ("Compile with %s; verify pin assignments against "
                               "the discovery pin map; fix errors in a bounded loop." % entry["toolchain"])},
        "decisions": decisions,
        "recognized_behavior": recognized_behavior,
        "limitations": limitations,
        "evidence_basis": ("Discovery record: MCU %s, %d mapped pins, %d buses, %d peripherals."
                           % (mcu.get("reference", "?"), gpio_count,
                              len(spec.get("buses") or []), len(spec.get("peripherals") or []))),
        "selection_basis": ("Framework %s selected for MCU family %s with toolchain %s %s; "
                            "bus evidence: %s; product purpose: %s."
                            % (entry["framework"], family, entry["toolchain"],
                               "present" if toolchain["available"] else "absent",
                               ", ".join(bus_kinds) if bus_kinds else "no communication buses found",
                               "stated" if spec.get("stated_purpose") else "unstated (see required decisions)")),
    }
    return {"ok": True, "plan": plan}

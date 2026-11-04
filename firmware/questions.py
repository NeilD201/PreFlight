"""PCB-to-Firmware Milestone C: route discovery questions through the Central
Question Creator. No second question system.

Every firmware open question passes through ``question_creator.decide()``:

* Prior review answers, documented purpose, and saved documents are consulted
  first, so nothing already answered is asked again (try_resolve inside the
  gate; identity/purpose questions additionally accept only already-recorded
  facts — a pin list never counts as establishing what a part is).
* Designer-supplied facts (which MCU, which parts, intended behavior) route
  with the creator's own requirements-entry hint and become user questions.
* Connectivity gaps classify as technical and delegate to investigation.
* Anything else stays UNKNOWN: preserved, never invented, never looped on.

Answers route back via ``route_answer`` and feed identity/purpose backfill
for the next discovery pass.
"""

from ..ai import question_creator as qc

FIRMWARE_AGENT = "firmware"

#: Open-question kinds only the designer can supply: what is on their board
#: and what it should do. Routed with the creator's requirements-entry hint,
#: which marks facts the designer must supply.
DESIGNER_SUPPLIED = ("mcu", "peripheral", "purpose", "encoder_behavior")


def _hint_for(question):
    if (question or {}).get("resolves") in DESIGNER_SUPPLIED:
        return "requirements_entry"
    return None


def build_question_index(spec, prior_answers=None, documented=None):
    """Evidence index for the gate: recorded facts plus packet connectivity.

    Identity and purpose questions must only resolve from recorded facts, so
    callers use ``recorded_index`` (below) for those and this full index for
    connectivity questions, where pin/net facts are the actual answer.
    """
    spec = spec if isinstance(spec, dict) else {}
    # Normalized like the creator's own evidence index so previously
    # recorded answers match regardless of case or spacing.
    answers = {" ".join(str(k).split()).casefold(): v
               for k, v in (prior_answers or {}).items()}
    documented = list(documented or [])
    purpose = spec.get("stated_purpose") or ""
    if purpose and not any(source == "purpose" for source, _ in documented):
        documented.append(("purpose", purpose))
    components, pins, nets = {}, {}, set()
    mcu_ref = (spec.get("mcu") or {}).get("reference", "")
    if mcu_ref:
        mc_pins = []
        for row in spec.get("gpio_map") or []:
            if not isinstance(row, dict):
                continue
            net = row.get("schematic_net") or row.get("pcb_net") or ""
            mc_pins.append({"number": row.get("pin", ""), "net": net})
            if net:
                nets.add(net)
        components[mcu_ref] = {"reference": mcu_ref, "pins": mc_pins}
    for peripheral in spec.get("peripherals") or []:
        if not isinstance(peripheral, dict):
            continue
        ref = peripheral.get("reference", "")
        if not ref:
            continue
        ppins = []
        for pin in peripheral.get("pins") or []:
            if not isinstance(pin, dict):
                continue
            ppins.append({"number": pin.get("pin", ""), "net": pin.get("net", "")})
            if pin.get("net"):
                nets.add(pin["net"])
        components[ref] = {"reference": ref, "pins": ppins}
    for bus in spec.get("buses") or []:
        for net in (bus or {}).get("nets") or []:
            if net:
                nets.add(net)
    return {"answers": answers, "documented": documented, "documents": [],
            "components": components, "pins": pins, "nets": nets,
            "calc_missing": set()}


def recorded_index(spec, prior_answers=None, documented=None):
    """Index without packet connectivity for identity/purpose questions."""
    index = build_question_index(spec, prior_answers, documented)
    index["components"] = {}
    index["pins"] = {}
    index["nets"] = set()
    return index


def route_questions(spec, prior_answers=None, documented=None):
    """Run every discovery open question through the creator gate."""
    spec = spec if isinstance(spec, dict) else {}
    tracker = qc.DedupTracker()
    routed = []
    for question in spec.get("open_questions") or []:
        if not isinstance(question, dict) or not question.get("need"):
            continue
        need = question["need"]
        scope = question.get("scope") or {}
        resolves = question.get("resolves", "")
        index = (recorded_index(spec, prior_answers, documented)
                 if resolves in DESIGNER_SUPPLIED
                 else build_question_index(spec, prior_answers, documented))
        decision = qc.decide(need, origin_agent=FIRMWARE_AGENT, scope=scope,
                             index=index, tracker=tracker,
                             context_hint=_hint_for(question))
        entry = {"need": need, "why": question.get("why", ""),
                 "resolves": resolves, "scope": decision.get("scope", {}),
                 "decision": decision.get("decision"),
                 "decision_id": decision.get("id", ""),
                 "reason": decision.get("reason", "")}
        if decision.get("decision") == qc.ASK_USER:
            posed = qc.render_question(decision)
            entry.update({"status": "user", "question": posed["question"],
                          "hint": posed["hint"],
                          "originating_agent": decision.get("originating_agent", ""),
                          "asked": decision.get("question", "")})
            entry["asked_decision"] = {k: decision.get(k) for k in
                                       ("id", "need", "origin_agent", "originating_agent",
                                        "scope", "classification", "depth", "chain",
                                        "decision", "ask_user", "question", "reason")}
        elif decision.get("decision") == qc.DELEGATE:
            if decision.get("resolution"):
                entry.update({"status": "resolved",
                              "fact": decision.get("resolution", ""),
                              "provenance": decision.get("required_evidence", "")})
            else:
                entry.update({"status": "delegated",
                              "target_agent": decision.get("target_agent", ""),
                              "required_evidence": decision.get("required_evidence", "")})
        else:
            entry.update({"status": "unresolved",
                          "unresolved_information": decision.get("unresolved_information", "")})
        routed.append(entry)
    return routed


def extras_from_answers(answers):
    """Identity and purpose backfill for the next discovery pass."""
    identities, purpose = {}, ""
    for answer in answers or []:
        if not isinstance(answer, dict) or not answer.get("answer"):
            continue
        if answer.get("resolves") in ("mcu", "peripheral"):
            refs = ((answer.get("scope") or {}).get("components") or [])
            mpn = (answer.get("mpn") or "").strip()
            if refs and mpn:
                # Family questions are answered once for all members.
                for ref in refs:
                    identities[ref] = {"mpn": mpn[:120], "source": "user-provided"}
        elif answer.get("resolves") == "purpose":
            purpose = answer["answer"][:2000]
    extras = {}
    if identities:
        extras["identities"] = identities
    if purpose:
        extras["purpose"] = purpose
    return extras

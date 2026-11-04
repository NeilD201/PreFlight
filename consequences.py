"""Bounded impact explanations derived from the finding, not its error count."""


def consequence_for(group, data, action=None):
    codes = {data['issues'][int(fid[1:]) - 1]['code']
             for fid in group.get('issue_ids', [])
             if fid.startswith('F') and fid[1:].isdigit()
             and 0 < int(fid[1:]) <= len(data.get('issues', []))}
    rule = group.get('rule', '')
    category = group.get('category')

    def result(text, certainty='possible'):
        return {'text': text, 'certainty': certainty,
                'context_note': 'Your explanation is saved, but it does not confirm that this is safe.'
                if action and action.get('disposition') == 'intentional' else ''}

    # These messages describe the scope of the finding, not the entire board.
    if 'KICAD_ERC_MULTIPLE_NET_NAMES' in codes:
        return result('Leaving both names does not itself change the wiring. It can make the schematic harder to follow; this naming warning alone shows no electrical failure.', 'no_fault_shown')
    if 'SENSITIVE_INPUT_ROUTE_REVIEW' in codes or (group.get('signal_path_id') or '').startswith('ecg-'):
        return result('These routing measurements alone do not show that the ECG signal will be noisy or that the traces need changing. Signal quality has not been established by this check.', 'unknown')
    if category == 'coverage':
        return result('This part of the design stays unchecked. A problem could remain unnoticed; the missing check itself does not mean the board is faulty.', 'unknown')
    if category == 'expected':
        if rule == 'max30102':
            return result('These pads are documented as unused. Leaving them disconnected follows the part’s requirements; no wiring fix is indicated for these pads.', 'no_fault_shown')
        return result('This warning was excluded in KiCad. Excluding it does not change the circuit or establish that it is safe; its impact has not been determined here.', 'unknown')
    if category == 'cosmetic':
        return result('Overlapping or clipped text usually only makes the board harder to read. If a polarity or pin-1 mark becomes unreadable, someone could fit a part the wrong way round.')
    if 'KICAD_DRC_MIRRORED_TEXT_ON_FRONT_LAYER' in codes:
        return result('The printed text will appear backwards. That does not change the electrical connections, but an unreadable assembly mark could cause confusion when fitting parts.', 'no_fault_shown')
    if rule == 'topology:input_output_joined':
        return result('The shared wire bypasses this part’s intended power-control function. The connected parts may receive the incoming supply directly instead of the intended controlled output.')
    if rule == 'topology:input_return_joined':
        return result('The power input and ground are the same connection, so this part cannot receive its intended supply across those pins. Powering that connection could also short the source, depending on the rest of the circuit.')
    if rule == 'topology:output_return_joined':
        return result('The output is tied to ground. Parts expecting power from that output would not receive their intended supply; what happens to the source depends on its protection.')
    if rule == 'schematic:supply_ground':
        return result('The chip’s power and ground pins share one wire, so it cannot receive its intended supply across those pins. Connecting a supply to that wire could short it; the source’s protection determines the outcome.')
    if rule in {'schematic:vdd_capacitor', 'schematic:vbat_capacitor', 'schematic:vled_capacitor'}:
        return result('If the required capacitor is really missing or too small, the chip may not behave reliably when its power demand changes. A suitable capacitor outside the checked connections may already cover this requirement.')
    if rule == 'schematic:charge_resistor':
        return result('If PROG is left disconnected, charging stays disabled. If a resistor is fitted but its value is wrong, the charge current may not match the battery’s requirements. This check has not confirmed the actual charge-setting arrangement.')
    if rule == 'schematic:sda_pullup':
        return result('Without a working pull-up, the data wire cannot reliably return to a high signal level, so communication with this sensor may fail. A suitable internal or external pull-up may already provide it.')
    if rule == 'mcp73831_dfn':
        return result('If this is the specified DFN charger, leaving its bottom pad without the required ground connection leaves the package connection incorrect. Charging reliability and heat handling are not established; this check does not calculate overheating.')
    if 'RF_FEED_NO_TERMINAL' in codes or (group.get('signal_path_id') or '').startswith('rf-'):
        return result('If the radio path really has no antenna, wireless range could be very poor or the connection could fail. An antenna this checker did not recognize could already be present.')
    if codes & {'KICAD_DRC_SHORTING_ITEMS', 'PCB_NET_MERGES_SCHEMATIC_NETS', 'PAD_NET_CONFLICT'}:
        return result('Connections that should be separate may interfere with each other. The affected circuit could stop working; if a power line is involved, excess current or damage is possible. The exact outcome depends on which connections are joined.')
    if codes & {'SCHEMATIC_NET_SPLIT_ON_PCB', 'PCB_PAD_WITHOUT_NET', 'PIN_MISSING_FROM_PCB',
                'KICAD_DRC_UNCONNECTED_ITEMS', 'SCHEMATIC_COMPONENT_MISSING_FROM_PCB'}:
        return result('If this connection or part is required, the affected circuit may have no power or may fail to pass its signal. That function could stop working even if the rest of the board works.')
    if codes & {'NO_CONNECT_PIN_CONNECTED', 'NO_CONNECT_ON_CONNECTED_NET'}:
        return result('A pin marked as unused is connected. If the part requires that pin to stay disconnected, the connection could cause incorrect operation. The pin’s actual function determines the risk.')
    if 'VALUE_MISMATCH' in codes:
        return result('The board and schematic specify different values. If the wrong value is assembled, it could change a voltage, current, timing or signal level; the affected part determines which.')
    if codes & {'KICAD_DRC_ANNULAR_WIDTH', 'KICAD_DRC_DRILL_OUT_OF_RANGE', 'KICAD_DRC_VIA_DIAMETER'}:
        return result('If these vias are smaller than your manufacturer supports, the order may be rejected or connections between copper layers may be unreliable. If the chosen service supports them, this may only need a rule update.')
    if 'KICAD_DRC_CLEARANCE' in codes:
        return result('If the gap is below your manufacturer’s supported spacing, the order may be rejected or nearby copper could be joined during fabrication. A gap below the saved KiCad limit alone does not prove a short.')
    if 'KICAD_DRC_SOLDER_MASK_BRIDGE' in codes:
        return result('Solder could bridge adjacent pads during assembly and join connections that should be separate. Some small packages intentionally use shared openings, so the opening alone does not prove an assembly fault.')
    if 'KICAD_ERC_POWER_PIN_NOT_DRIVEN' in codes:
        return result('If power arrives through the intended connector, leaving this warning does not remove that power. If there really is no supply path, the affected parts will not turn on. ERC has not distinguished those cases.', 'unknown')
    if 'KICAD_ERC_PIN_NOT_CONNECTED' in codes:
        return result('If the pin is required, its feature may not work or an input may be left undefined. If it is allowed to stay unused, leaving it disconnected may be correct. The operating mode still needs to be established.', 'unknown')
    if 'KICAD_ERC_PIN_TO_PIN' in codes:
        checks = (action or {}).get('pin_checks', [])
        if (action or {}).get('disposition') == 'no_change' or checks and all(c.get('decision') == 'keep' for c in checks):
            return result('The reviewed pin functions support these connections. This pin-type warning does not call for a wiring change in the reviewed operating mode.', 'no_fault_shown')
        if category == 'documentation':
            return result('The symbol’s pin description does not change the physical wiring. If the power connections are correct, the board may work with this warning still present. This check has not verified those power connections.', 'unknown')
        return result('An address or mode-selection pin tied to a supply can be correct. If a pin is instead configured as an output against that supply, it could draw excess current or fail to work. This warning alone does not tell us which case applies.', 'unknown')
    if any('LIB_' in code or code == 'FOOTPRINT_MISMATCH' for code in codes):
        return result('If the difference changes a pin number or pad shape incorrectly, a part could be wired wrong or fail to fit. If it is an intentional library edit, nothing may need changing. The difference alone does not establish a fault.', 'unknown')
    if 'PCB_PAD_MISSING_FROM_SYMBOL' in codes:
        return result('If the extra pad needs an electrical connection, leaving it out could prevent correct operation or leave a required package connection missing. A documented unused or mechanical pad may be fine.', 'unknown')
    if category == 'assembly':
        return result('If the assembly files are wrong, the assembler could fit the wrong part, miss a part, or place it incorrectly. Confirm the mismatch before using those files to order.')
    if group.get('basis') == 'AI schematic review':
        # These findings have been checked against both saved pin/net evidence
        # and a quoted manufacturer source. Explain the likely functional
        # effect without turning a conditional design concern into a claim of
        # certain failure.
        check_type = group.get('check_type')
        summary = group.get('summary', '')
        why = group.get('why', '')
        source_text = ' '.join(c.get('quote', '') for c in group.get('citations', [])).lower()
        provisional = bool(group.get('missing_context')) or 'automatically matched' in why.lower()
        qualification = (' The part-number match is provisional, so confirm the exact fitted variant.'
                         if provisional else '')

        if (check_type == 'supply' and 'voltage sense' in source_text
                and 'positive battery terminal' in source_text
                and any(token in summary.lower() for token in ('+3.3v', '+5v', 'vcc'))):
            return result(('If the cited fuel-gauge variant is the one fitted, its voltage-sense pin '
                           'is on the regulated rail instead of the battery terminal. The gauge may '
                           'then base battery-voltage and low-battery readings on the wrong voltage, '
                           'so those readings could be misleading.') + qualification, 'possible')
        if (check_type == 'mode' and 'unconnected' in summary.lower()
                and ('connect to gnd if not used' in source_text or 'connect to ground if not used' in source_text)):
            return result(('If this input is meant to be unused, the datasheet says to tie it to '
                           'ground; leaving it floating means its state is not defined by the shown '
                           'wiring. Noise could trigger the input and restart the fuel-gauge estimate, '
                           'making battery readings unreliable until it settles.') + qualification,
                          'possible')
        if check_type == 'interface':
            return result(('If the shown pin connections do not match the selected interface, the '
                           'device may not communicate with its controller or may respond on the '
                           'wrong bus/address. The exact outcome depends on the selected mode.') + qualification,
                          'possible')
        if check_type == 'input_signal':
            return result(('If the signal is connected contrary to the cited input requirement, the '
                           'device may read the wrong signal or give inaccurate measurements. The '
                           'evidence does not establish damage or the size of any measurement error.') + qualification,
                          'possible')
        if check_type == 'external_parts':
            return result(('If the required external part is missing or connected incorrectly, the '
                           'related function may be unstable or may not meet its specified behavior. '
                           'The saved connections do not show the size of that effect.') + qualification,
                          'possible')
        if check_type == 'charging':
            return result(('If the charger setting or battery connection is wrong, charging may be '
                           'disabled or may use a current that does not suit the cell. The cell rating '
                           'and actual charge path must be confirmed before predicting the result.') + qualification,
                          'possible')
        if check_type == 'supply':
            return result(('If the supply connection does not meet the cited requirement, the affected '
                           'part may not power up or its measured values may be wrong. The evidence '
                           'does not establish damage.') + qualification, 'possible')
        return result(('The cited requirement and saved pin connections do not agree. If that '
                       'connection is required for the selected operating mode, the related feature '
                       'may not work as intended; the evidence does not establish damage or a '
                       'guaranteed failure.') + qualification, 'possible')
    return result('This check has not established what would happen if you leave it unchanged. The recorded warning is not enough to predict a failure.', 'unknown')


def add_consequences(plan, data):
    groups = {g['id']: g for g in data.get('review', {}).get('groups', [])}
    for action in plan.get('actions', []):
        if action['group_id'] in groups:
            action['consequence'] = consequence_for(groups[action['group_id']], data, action)
    return plan

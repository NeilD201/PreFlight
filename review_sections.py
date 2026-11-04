"""Organize findings by engineering question without upgrading raw ERC to a defect."""
from .footprint_context import footprint_rule_context

SECTIONS = (
    ('connections', 'Connections', 'Do the parts connect in a way that makes this device work?'),
    ('placement', 'Placement & PCB', 'Part placement, copper spacing, holes and assembly.'),
    ('traces', 'Traces & signals', 'Complete paths, trace resistance, sensitive inputs and the antenna feed.'),
    ('manufacturing', 'Manufacturer', 'Process limits, required order options and fabrication acceptance.'),
)

CONTINUITY_CODES = {
    'SCHEMATIC_NET_SPLIT_ON_PCB', 'PCB_NET_MERGES_SCHEMATIC_NETS', 'PAD_NET_CONFLICT',
    'PCB_PAD_WITHOUT_NET', 'PIN_MISSING_FROM_PCB', 'NO_CONNECT_PIN_CONNECTED',
    'NO_CONNECT_ON_CONNECTED_NET', 'KICAD_DRC_SHORTING_ITEMS', 'KICAD_DRC_UNCONNECTED_ITEMS',
}

# These warnings leave a connection question open. Keep them visible without
# claiming that ERC has proved a faulty circuit. Library/naming notes stay notes.
CONNECTION_REVIEW_CODES = {
    'KICAD_ERC_PIN_TO_PIN', 'KICAD_ERC_PIN_NOT_CONNECTED',
    'KICAD_ERC_NOCONNECT_CONNECTED', 'KICAD_ERC_NOCONNECT_NOT_CONNECTED',
}


def codes_for(group, data):
    issues = data.get('issues', [])
    return {issues[int(fid[1:]) - 1].get('code', '') for fid in group.get('issue_ids', [])
            if fid.startswith('F') and fid[1:].isdigit() and 0 < int(fid[1:]) <= len(issues)}


def section_for(group, action, data):
    codes = codes_for(group, data)
    category = group.get('category')
    if category in ('expected', 'coverage'):
        return 'notes'
    if codes & CONTINUITY_CODES or category == 'signal' or group.get('signal_path_id'):
        return 'traces'
    if action.get('manufacturer_check'):
        return 'manufacturing'
    if footprint_rule_context(group, data):
        return 'notes'
    if category == 'manufacturing':
        return 'manufacturing'
    # A supported circuit check can originate in an ERC warning. The raw warning
    # alone cannot establish that an address strap, GPIO or supply is wired wrong.
    if any(code.startswith('KICAD_ERC_') for code in codes):
        checks = action.get('pin_checks', [])
        if any(check.get('decision') in ('change', 'needs_context') for check in checks):
            return 'connections'
        if not checks and codes & CONNECTION_REVIEW_CODES and not group.get('no_change_basis') and action.get('disposition') != 'no_change':
            return 'connections'
        return 'notes'
    if group.get('schematic_check') or group.get('rule', '').startswith(('schematic:', 'topology:', 'ai_schematic:')):
        return 'connections'
    if category in ('documentation', 'cosmetic') or group.get('no_change_basis'):
        return 'notes'
    if category in ('manufacturing', 'assembly') or any(code.startswith('KICAD_DRC_') for code in codes):
        return 'placement'
    if codes & {'VALUE_MISMATCH', 'SCHEMATIC_COMPONENT_MISSING_FROM_PCB', 'PCB_COMPONENT_MISSING_FROM_SCHEMATIC'}:
        return 'placement'
    if category == 'documentation' or group.get('no_change_basis'):
        return 'notes'
    return 'connections' if category == 'electrical' else 'notes'


def organize_review(data):
    actions = {a['group_id']: a for a in data.get('action_plan', {}).get('actions', [])}
    assignments = {}
    pending_erc = []
    from .check_scope import build_scope
    scope=data.get('check_scope') or build_scope(data)
    radio=next(row for row in scope['checks'] if row['id']=='antenna')
    for group in data.get('review', {}).get('groups', []):
        action = actions.get(group['id'], {})
        section = section_for(group, action, data)
        radio_scope_note=None
        codes=codes_for(group,data)
        rf_unresolved='RF_FEED_NO_TERMINAL' in codes
        radio_unused=scope.get('declared_features',{}).get('radio') is False
        # Unknown intent is unfinished work, not a reason to hide a finding.
        # Only an explicit unused-radio declaration can defer endpoint advice.
        if rf_unresolved and not codes & CONTINUITY_CODES and radio_unused:
            section='notes'
            radio_scope_note=radio['reason']
        elif rf_unresolved:
            section='traces'
        pending_connection = (section == 'connections' and bool(codes_for(group, data) & CONNECTION_REVIEW_CODES)
                              and not action.get('pin_checks') and action.get('disposition') != 'no_change')
        if pending_connection:
            pending_erc.append(group['id'])
        checks = action.get('pin_checks', [])
        accepted = action.get('disposition') == 'no_change' or bool(checks) and all(p.get('decision') == 'keep' for p in checks)
        if rf_unresolved and not radio_unused:
            accepted=False
        attention = (section != 'notes' and not accepted and not action.get('reference_only')
                     and (group.get('priority') in ('fix', 'review') or action.get('manufacturer_check', {}).get('status') == 'outside'))
        if rf_unresolved and not radio_unused:attention=True
        status = ('accepted' if accepted else 'reference' if not attention else 'context' if pending_connection or 'MEMORY_CONNECTION_REVIEW' in codes_for(group,data) or any(
            p.get('decision') == 'needs_context' for p in checks) else 'change' if action.get('disposition') == 'change' else 'review')
        assignments[group['id']] = {
            'scope_note':radio_scope_note,
            'section': section,
            'attention': bool(attention),
            'status': status,
        }
    sections = []
    for sid, title, purpose in SECTIONS:
        members = [gid for gid, item in assignments.items() if item['section'] == sid]
        sections.append({'id': sid, 'title': title, 'purpose': purpose, 'group_ids': members,
                         'attention_count': sum(assignments[gid]['attention'] for gid in members)})
    checks = set(data.get('checks_performed', []))
    measurements = data.get('signal_paths', {}).get('board_measurements', {})
    return {'sections': sections, 'assignments': assignments, 'pending_erc_ids': pending_erc,
            'notes_count': sum(item['section'] == 'notes' for item in assignments.values()),
            'coverage': {
                'pin_comparison': data.get('connectivity', {}).get('status') == 'compared',
                'pins_compared': data.get('connectivity', {}).get('pins_compared', 0),
                'erc_ran': 'kicad_erc' in checks, 'drc_ran': 'kicad_drc' in checks,
                'ai_status': data.get('ai_review', {}).get('status', 'not_requested'),
                'ai_rejected': len(data.get('ai_review', {}).get('rejected_items', [])),
                'traced_paths': len(data.get('circuit', {}).get('routing_chains', {}).get('chains', [])),
                'dc_estimates': sum(n.get('dc_trace', {}).get('status') == 'estimated'
                                    for n in measurements.get('nets', [])),
                'placement_review': 'Saved DRC and footprint positions. Datasheet placement constraints are not fully checked.',
                'signal_review': 'No RF impedance, return-plane continuity, crosstalk, noise or temperature simulation has run.',
            }}

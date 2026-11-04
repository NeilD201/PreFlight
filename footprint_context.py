"""Interpret internal pad rules without declaring a footprint or process approved."""

import re


def footprint_rule_context(group, data):
    """Require every item to be a known pad on the same, unambiguous footprint.

    Positive gaps against the default netclass are reference information, not an
    instruction to change a land pattern. Explicit/custom rules and overlaps stay
    actionable. Mixed groups are retained rather than silently losing a track.
    """
    issues = data.get('issues', [])
    ids = group.get('issue_ids', [])
    if not ids:
        return None
    rows = []
    for fid in ids:
        if not re.fullmatch(r'F\d+', fid) or not 0 < int(fid[1:]) <= len(issues):
            return None
        issue = issues[int(fid[1:]) - 1]
        code = issue.get('code')
        if issue.get('excluded') or code not in ('KICAD_DRC_CLEARANCE', 'KICAD_DRC_SOLDER_MASK_BRIDGE'):
            return None
        items = issue.get('items', [])
        if len(items) != 2:
            return None
        pads = []
        for item in items:
            match = re.fullmatch(r'Pad (\S+) \[(.*?)\] of (\S+) on (.+)', item.get('description', ''))
            if not match:
                return None
            number, net, ref, layer = match.groups()
            records = data.get('components', {}).get(ref, {}).get('pcb', [])
            if len(records) != 1 or not records[0].get('footprint'):
                return None
            if not any(p.get('number') == number and p.get('net') == net for p in records[0].get('pads', [])):
                return None
            pads.append({'reference': ref, 'pad': number, 'net': net, 'layer': layer,
                         'footprint': records[0]['footprint']})
        if pads[0]['reference'] != pads[1]['reference'] or pads[0]['pad'] == pads[1]['pad']:
            return None
        row = {'finding_id': fid, 'code': code, 'pads': pads}
        if code == 'KICAD_DRC_CLEARANCE':
            match = re.fullmatch(r"Clearance violation \(netclass 'Default' clearance ([0-9.]+) mm; actual ([0-9.]+) mm\)", issue.get('message', ''))
            if not match:
                return None
            try:
                minimum, actual = map(float, match.groups())
            except ValueError:
                return None
            if not 0 < actual < minimum:
                return None
            row.update(gap_mm=actual, saved_default_mm=minimum)
        rows.append(row)
    refs = sorted({p['reference'] for row in rows for p in row['pads']})
    gaps = [row['gap_mm'] for row in rows if 'gap_mm' in row]
    if gaps:
        span = f'{min(gaps):g}' if min(gaps) == max(gaps) else f'{min(gaps):g}–{max(gaps):g}'
        observed = f"{', '.join(refs)}: the reported pad gaps are {span} mm, inside the footprint. The warning compares them with the saved default clearance."
        title = 'Pad spacing inside the footprint'
    else:
        observed = f"{', '.join(refs)}: the reported solder-mask openings join between pads of the same footprint. This does not report joined copper."
        title = 'Shared mask opening inside the footprint'
    return {'kind': 'internal_footprint_rule', 'reference_only': True, 'title': title,
            'summary': observed, 'findings': rows,
            'reason': observed + ' This warning alone does not justify moving or shrinking the pads. Conformance to the manufacturer land pattern is NOT VERIFIED: package drawings are figures in PDF datasheets and are not machine-extracted, so only measured footprint geometry (not a pass) is reported. The selected manufacturing process has not been verified either.',
            'limits': 'Same-footprint spacing is not automatically safe. Copper shorts, overlaps, routed-track spacing and explicit clearance rules are checked separately.',
            'source': 'https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html'}


def apply_footprint_context(plan, data):
    """Replace unsupported edit advice in both fresh and previously saved AI plans."""
    groups = {g['id']: g for g in data.get('review', {}).get('groups', [])}
    for action in plan.get('actions', []):
        context = footprint_rule_context(groups.get(action['group_id'], {}), data)
        if not context:
            continue
        action.update(reference_only=True, footprint_context=context,
                      disposition='investigate', reason=context['reason'], steps=[],
                      title=context['title'], basis='Footprint rule interpretation',
                      citations=[], pin_checks=[], evidence_ids=[action['group_id']])
        action['consequence'] = {'certainty': 'unknown', 'context_note': '',
            'text': 'No hardware failure is established by this warning. Leaving the footprint unchanged still requires a suitable land pattern and manufacturing process; those are not verified here.'}
    return plan

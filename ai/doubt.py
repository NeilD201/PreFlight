"""DOUBT blind pass: late coverage-gap review without prior conclusions.

Reads packet facts plus the examined-object sets (claim scopes and tool
references) but never claim statements, statuses or sureness scores, so
the pass cannot be steered by earlier conclusions. Unexamined active
parts and connectors become new claims that reenter verification;
remaining gaps stay in the event record with explicit overflow counts.
"""
VERSION = 1
MAX_CLAIMS = 10
MAX_LISTED = 200
CLAIM_PREFIXES = ('U', 'Q', 'J', 'P', 'Y')


def coverage_sets(ledger):
    """Examined objects from scopes and tool refs; conclusions never read."""
    refs, nets = set(), set()
    for claim in ledger['claims']:
        scope = claim.get('scope', {})
        refs.update(scope.get('components', ()))
        nets.update(scope.get('nets', ()))
    for event in ledger['events']:
        if event.get('kind') in ('tool_executed', 'tool_unavailable'):
            refs.update(event.get('data', {}).get('references', ()))
    return refs, nets


def find_gaps(packet, ledger):
    """Unexamined fitted parts and nets from packet facts and coverage sets."""
    examined_refs, examined_nets = coverage_sets(ledger)
    parts, nets = {}, set()
    for entry in packet.get('components', []):
        ref = entry.get('reference', '')
        if entry.get('dnp'):
            continue
        parts[ref] = str(entry.get('value', ''))
        for pin in entry.get('pins', []):
            if pin.get('net'):
                nets.add(pin['net'])
    unexamined = sorted(r for r in parts if r not in examined_refs)
    return {'parts': parts,
            'unexamined_claimable': [r for r in unexamined if r.startswith(CLAIM_PREFIXES)],
            'unexamined_other': [r for r in unexamined if not r.startswith(CLAIM_PREFIXES)],
            'unexamined_nets': sorted(n for n in nets if n not in examined_nets)}


def assess(packet, ledger, *, origin='doubt', limit=MAX_CLAIMS):
    """Record unexamined active parts as new claims; keep every gap on record."""
    from .ledger import add_claim, record_event
    gaps = find_gaps(packet, ledger)
    summary = {'claims': [], 'truncated': 0, 'gaps': gaps}
    for ref in gaps['unexamined_claimable']:
        if len(summary['claims']) >= limit:
            summary['truncated'] += 1
            continue
        value = gaps['parts'][ref]
        claim_id = add_claim(ledger, origin=origin,
                             statement=f"[DBT-001] Blind review: {ref} ({value or 'unmarked'}) "
                                       'was not examined by any investigation.',
                             scope={'components': [ref], 'nets': [], 'pins': []},
                             evidence_ids=[f'C:{ref}'],
                             open_questions=[f'Why was {ref} never examined, and what could it affect?'],
                             assumptions=['Standing: uncertain.',
                                          'Blind pass: no prior conclusion was read.'])
        summary['claims'].append(claim_id)
    listed = {key: gaps[key][:MAX_LISTED] for key in
              ('unexamined_claimable', 'unexamined_other', 'unexamined_nets')}
    overflow = {key: max(0, len(gaps[key]) - MAX_LISTED) for key in listed}
    detail = (f"{len(summary['claims'])} new concerns from {len(gaps['unexamined_claimable'])} "
              f"unexamined active parts; {len(gaps['unexamined_other'])} other parts and "
              f"{len(gaps['unexamined_nets'])} nets unexamined.")
    if summary['truncated'] or any(overflow.values()):
        detail += ' Recording budget reached; overflow stays listed as counts.'
        ledger['limitations'].append('Blind review hit its recording budget; rerun with a higher limit.')
    record_event(ledger, 'doubt_assessed', detail, actor=origin,
                 data={'claims': summary['claims'], 'truncated': summary['truncated'],
                       'gaps': listed, 'overflow': overflow})
    return summary

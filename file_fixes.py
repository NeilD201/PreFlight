"""Explicit, reversible edits derived from file evidence, never executable AI text."""
from collections import Counter
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .board_locations import board_locations
from .native import _stage, _run, find_kicad
from .fabrication import prepare_board
from .parsers.sexpr import parse, properties, TOKEN


class FixError(ValueError):
    pass


def digest(content):
    return hashlib.sha256(content).hexdigest()


def source_file(source, relative):
    source = Path(source).resolve()
    path = source / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise FixError('Invalid project file path.')
    if any(p.is_symlink() for p in (path, *path.parents) if p != source.parent):
        raise FixError('Linked files cannot be edited through symbolic links.')
    if not path.resolve().is_relative_to(source) or not path.is_file():
        raise FixError('The linked project file is missing.')
    return path


def verify_sources(source, hashes):
    for relative, expected in hashes.items():
        if digest(source_file(source, relative).read_bytes()) != expected:
            raise FixError('Your KiCad files changed. Save them, update the review, and prepare this fix again.')


def source_status(source, hashes):
    """Compare a saved run with its linked project files without editing them."""
    if not source or not hashes:
        return {'status': 'unavailable', 'changed': [], 'missing': []}
    changed, missing = [], []
    for relative, expected in hashes.items():
        try:
            actual = digest(source_file(source, relative).read_bytes())
        except (FixError, OSError):
            missing.append(relative)
            continue
        if actual != expected:
            changed.append(relative)
    return {'status': 'changed' if changed or missing else 'current',
            'changed': changed, 'missing': missing}


def selected_issues(project, group):
    return [project.issues[int(fid[1:])-1] for fid in group.get('issue_ids', [])
            if isinstance(fid, str) and fid.startswith('F') and fid[1:].isdigit()
            and 0 < int(fid[1:]) <= len(project.issues)]


def availability(project, group):
    """Cheap capability check; geometry and changed files are checked on prepare."""
    selected = selected_issues(project, group)
    if len(selected) == 1 and not selected[0].excluded:
        if selected[0].code in ('VALUE_MISMATCH', 'KICAD_DRC_UNCONNECTED_ITEMS'):
            return {'mode': 'automatic', 'message':
                'A narrow edit is supported. It must pass the staged KiCad checks and your approval before it can change a project file.'}
    codes = {issue.code for issue in selected}
    if 'RF_FEED_NO_TERMINAL' in codes:
        path_id = group.get('signal_path_id') or next(
            (i.details.get('signal_path_id') for i in selected if i.code == 'RF_FEED_NO_TERMINAL'), None)
        path = next((p for p in project.signal_paths.get('paths', [])
                     if path_id and p.get('id') == path_id and p.get('kind') == 'rf'), {})
        observations = []
        for net in path.get('nets', []):
            members = [f'{ref} pad {pad.number}' for ref, part in project.components.items()
                       for record in part.pcb for pad in record.pads if pad.net == net.get('net')]
            observations.append(f"{net['net']}: {', '.join(members) or 'no pad membership recorded'}. "
                                f"{net['copper_mm']} mm of drawn tracks; {net['vias']} vias. "
                                'Track totals do not establish a complete physical connection.')
        return {'mode': 'investigate', 'message':
            'The scan could not identify an antenna endpoint; that does not prove an antenna is missing. '
            'Investigate the RF path first. A file edit is offered only if a sourced design and board checks support it.',
            'assessment': {
                'status': 'investigate', 'title': 'RF path needs a design review',
                'observations': observations or ['This report has no matching RF path measurements. Update the review to measure the saved board.'],
                'blockers': [
                    'The endpoint detector cannot identify every copper radiator or off-board antenna connection.',
                    'A reference copper pattern can be evaluated against the saved board. Exact placement and routing are checked in a separate preview.',
                    'The RF path finding alone does not prove that an antenna is missing. Any proposed addition needs your review.'],
                'next_step': 'Investigate the RF path and sourced antenna options. No file is changed unless a candidate passes the board checks and you approve it.'}}
    return {'mode': 'investigate', 'message':
        'This finding needs a repair assessment before a file change. The assistant can examine the saved '
        'connections and datasheet evidence, then prepare supported placement, routing and footprint changes for approval.'}


_VALUE_REQUEST = re.compile(
    r"(?:change|set|update)\s+([A-Za-z][A-Za-z0-9_]*[0-9]+)\s+(?:from\s+.+?\s+)?(?:to|=|→|->)\s*([0-9.][^\s.,;]*)"
    r"|([A-Za-z][A-Za-z0-9_]*[0-9]+)\s*=\s*([0-9.][^\s.,;]*)",
    re.IGNORECASE)


def parse_value_request(request, refs):
    """Parse a single-component value assignment; None when not value-shaped.

    Only matches an explicit change verb (or `=`) naming a finding reference
    plus a number-led value token, so prose never becomes an edit.
    """
    if not isinstance(request, str):
        return None
    keep = {str(r) for r in refs or []}
    match = _VALUE_REQUEST.search(request)
    if not match:
        return None
    ref = match.group(1) or match.group(3)
    value = (match.group(2) or match.group(4) or '').strip()
    if ref not in keep or not value:
        return None
    return (ref, value)


_REF_ITEM = r"[A-Za-z][A-Za-z0-9_]*[0-9]+"
_REF_LIST = r"(?:{r}\s*(?:,|and\b)?\s*)+".format(r=_REF_ITEM)
_ASSIGN_STRUCT = re.compile(
    r"(?P<refs>" + _REF_LIST + r")\s+(?:from\s+.+?\s+)?(?:to|=|→|->)\s*(?P<value>[0-9.][^\s.,;]*)"
    r"|(?P<refs_eq>" + _REF_LIST + r")\s*=\s*(?P<value_eq>[0-9.][^\s.,;]*)",
    re.IGNORECASE)
_CHANGE_VERB = re.compile(r"\b(change|set|update)\b", re.IGNORECASE)


def _split_ref_list(text, keep):
    """Split a comma/and-separated reference list; None when not uniformly known."""
    parts = [p for p in re.split(r"\s*(?:,|\band\b)\s*", text.strip(), flags=re.IGNORECASE) if p.strip()]
    refs = []
    for part in parts:
        if not re.fullmatch(_REF_ITEM, part.strip(), re.IGNORECASE):
            return None
        hit = next((k for k in keep if k.lower() == part.strip().lower()), None)
        if hit is None or hit in refs:
            return None
        refs.append(hit)
    return refs or None


def parse_value_requests(request, refs):
    """Parse one shared value assignment for one or more finding references.

    Returns an ordered [(ref, value)] list, or None when the request is not a
    single shared value change over known references. Mixed values across
    references, unknown references, and value-shaped prose without a change
    verb fail closed (None) instead of guessing or silently editing a subset.
    """
    if not isinstance(request, str):
        return None
    keep = {str(r) for r in refs or []}
    if not keep:
        return None
    if not _CHANGE_VERB.search(request) and '=' not in request:
        return None
    found = []
    for match in _ASSIGN_STRUCT.finditer(request):
        refs_text = match.group('refs') or match.group('refs_eq')
        value = ((match.group('value') or match.group('value_eq')) or '').strip()
        parsed = _split_ref_list(refs_text, keep)
        if not parsed or not value:
            return None
        found.append((parsed, value))
    if not found:
        return None
    values = {value.lower() for _, value in found}
    if len(values) != 1:
        return None
    value = found[0][1]
    ordered = []
    for parsed, _ in found:
        ordered.extend(ref for ref in parsed if ref not in ordered)
    return [(ref, value) for ref in ordered]


def _replace_value_token(text, node):
    tokens = [t for t in TOKEN.finditer(text[node.start:node.end])
              if not t.group().isspace() and not t.group().startswith(';')]
    # ( property "Value" "old" ... ): replace only the value token.
    token = tokens[3]
    return tokens, token


def requested_value_candidate(project, ref, value, sch_text, pcb_text):
    """Deterministic new value for one unambiguous component, both files.

    Raises FixError for ambiguous records, non-R/C/L parts, unparseable
    values and no-op requests instead of guessing.
    """
    from .checks.components import _passive_value, values_match
    component = project.components.get(ref)
    if not component or len(component.pcb) != 1 or len(component.schematic) != 1:
        raise FixError(f'{ref} does not have one unambiguous schematic symbol and PCB footprint.')
    if _passive_value(ref, value) is None:
        raise FixError(f'{value!r} is not a valid value for {ref}; use a numeric R/C/L value such as 680, 10k or 100nF.')
    schem_rec, board_rec = component.schematic[0], component.pcb[0]
    before_sch, before_pcb = schem_rec.value.strip(), board_rec.value.strip()
    if values_match(ref, before_sch, value) and values_match(ref, before_pcb, value):
        raise FixError(f'{ref} is already {before_sch}; no change is needed.')
    root = parse(pcb_text, 'kicad_pcb')
    footprints = [fp for fp in root.all('footprint') if properties(fp).get('Reference') == ref]
    if len(footprints) != 1:
        raise FixError(f'{ref} was not found exactly once in the saved PCB.')
    props = [p for p in footprints[0].all('property') if p.values[:1] == ['Value']]
    if len(props) != 1 or props[0].values != ['Value', board_rec.value]:
        raise FixError('The saved PCB value does not match this review; update the review first.')
    _, token = _replace_value_token(pcb_text, props[0])
    new_pcb = pcb_text[:props[0].start+token.start()] + json.dumps(value, ensure_ascii=False) + pcb_text[props[0].start+token.end():]
    sch_root = parse(sch_text, 'kicad_sch')
    symbols = [s for s in sch_root.all('symbol') if properties(s).get('Reference') == ref]
    if len(symbols) != 1:
        raise FixError(f'{ref} was not found exactly once in the saved schematic.')
    sprops = [p for p in symbols[0].all('property') if p.values[:1] == ['Value']]
    if len(sprops) != 1 or sprops[0].values != ['Value', schem_rec.value]:
        raise FixError('The saved schematic value does not match this review; update the review first.')
    _, stoken = _replace_value_token(sch_text, sprops[0])
    new_sch = sch_text[:sprops[0].start+stoken.start()] + json.dumps(value, ensure_ascii=False) + sch_text[sprops[0].start+stoken.end():]
    before = before_sch if values_match(ref, before_sch, before_pcb) else f'schematic {before_sch} / PCB {before_pcb}'
    return new_sch, new_pcb, {
        'kind': 'requested_value',
        'summary': f'Change {ref} from {before} to {value}.',
        'basis': 'Your requested value, applied to the matching schematic symbol and PCB footprint.',
        'reference': ref, 'before': before, 'after': value,
        'checks': ['The component has one matching schematic symbol and PCB footprint.',
                   'Only the Value fields change; copper and pin assignments remain identical.']}


def requested_value_plan(project, group, request, source, folder, progress=lambda message: None):
    """Deterministic plan for a user-requested value change, or None.

    Returns None when the request is not a single shared value assignment
    so the caller falls through to the AI edit planner. Handles one ref or
    a ref list sharing one value; every ref is verified before anything is
    staged, so a partially applicable request raises FixError with no files
    written instead of silently editing a subset.
    Raises FixError when the request names a finding component but cannot
    be applied safely, so a big board never pays for a geometry plan it
    does not need.
    """
    refs = group.get('components') or []
    parsed = parse_value_requests(request, refs)
    if parsed is None:
        return None
    source = Path(source).resolve()
    hashes = dict(project.provenance.get('source_hashes', {}))
    relative = project.selected_files.get('pcb')
    schematic_rel = project.selected_files.get('schematic')
    if not relative or relative not in hashes or not schematic_rel or schematic_rel not in hashes:
        raise FixError('Update this project first so both source files can be verified.')
    verify_sources(source, hashes)
    sch_text = source_file(source, schematic_rel).read_text(encoding='utf-8-sig')
    pcb_text = source_file(source, relative).read_bytes().decode('utf-8-sig')
    summaries, check_lines, befores = [], [], []
    new_sch, new_pcb = sch_text, pcb_text
    for ref, value in parsed:
        progress(f'Applying {ref} = {value}…')
        new_sch, new_pcb, details = requested_value_candidate(project, ref, value, new_sch, new_pcb)
        summaries.append(details['summary'])
        befores.append(details['before'])
        check_lines.extend(details['checks'])
    # Fail closed: all replacements must re-parse and only Value lines move.
    parse(new_sch, 'kicad_sch')
    parse(new_pcb, 'kicad_pcb')
    sch_diff = ''.join(difflib.unified_diff(sch_text.splitlines(True), new_sch.splitlines(True),
                                            fromfile=schematic_rel + ' (before)', tofile=schematic_rel + ' (after)'))
    pcb_diff = ''.join(difflib.unified_diff(pcb_text.splitlines(True), new_pcb.splitlines(True),
                                            fromfile=relative + ' (before)', tofile=relative + ' (after)'))
    for line in (sch_diff + pcb_diff).splitlines():
        if line[:1] in ('-', '+') and line[:3] not in ('---', '+++') and 'Value' not in line:
            raise FixError('The value edit touched more than the Value fields; refusing the preview.')
    value = parsed[0][1]
    olds = dict.fromkeys(befores)
    summary = 'Change {} from {} to {}.'.format(
        ', '.join(ref for ref, _ in parsed), '; '.join(olds), value)
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'original.kicad_pcb').write_bytes(pcb_text.encode('utf-8'))
    (folder/'replacement.kicad_pcb').write_bytes(new_pcb.encode('utf-8'))
    (folder/'original.kicad_sch').write_bytes(sch_text.encode('utf-8'))
    (folder/'replacement.kicad_sch').write_bytes(new_sch.encode('utf-8'))
    diff = (sch_diff + pcb_diff)[:40000]
    plan = dict(status='ready', kind='requested_value', summary=summary,
                basis='Your requested value, applied to each matching schematic symbol and PCB footprint.',
                source=str(source), file=relative, file_sch=schematic_rel,
                hashes=hashes, original_sha256=digest(pcb_text.encode('utf-8')),
                replacement_sha256=digest(new_pcb.encode('utf-8')),
                original_sch_sha256=digest(sch_text.encode('utf-8')),
                replacement_sch_sha256=digest(new_sch.encode('utf-8')),
                diff=diff, diff_truncated=len(sch_diff + pcb_diff) > 40000,
                backup=str(folder/'original.kicad_pcb'), backup_sch=str(folder/'original.kicad_sch'))
    plan['checks'] = check_lines + ['Both replacements re-parse as valid KiCad files.',
                                    'The preview diff changes only Value lines.']
    return plan


def value_candidate(project, issue, text):
    component = project.components.get(issue.component)
    if not component or len(component.pcb) != 1 or len(component.schematic) != 1:
        raise FixError('This component does not have one unambiguous schematic symbol and PCB footprint.')
    schematic, board = component.schematic[0], component.pcb[0]
    if not schematic.value.strip() or schematic.footprint != board.footprint:
        raise FixError('Resolve the part/footprint identity before copying its value.')
    root = parse(text, 'kicad_pcb')
    footprints = [fp for fp in root.all('footprint') if properties(fp).get('Reference') == issue.component]
    if len(footprints) != 1:
        raise FixError('The PCB footprint could not be identified uniquely.')
    props = [p for p in footprints[0].all('property') if p.values[:1] == ['Value']]
    if len(props) != 1 or props[0].values != ['Value', board.value]:
        raise FixError('The saved PCB value does not match this review.')
    node = props[0]
    tokens = [t for t in TOKEN.finditer(text[node.start:node.end])
              if not t.group().isspace() and not t.group().startswith(';')]
    # ( property "Value" "old" ... ): replace only the value token.
    token = tokens[3]
    replacement = json.dumps(schematic.value, ensure_ascii=False)
    result = text[:node.start+token.start()] + replacement + text[node.start+token.end():]
    return result, {'kind': 'value', 'summary': f'{issue.component}: change the PCB value from {board.value} to {schematic.value}.',
                    'basis': 'Copy the value from the matching schematic symbol. You choose whether the schematic value is intended.',
                    'reference': issue.component, 'before': board.value, 'after': schematic.value,
                    'checks': ['The component has one matching schematic symbol and PCB footprint.',
                               'Only the PCB Value field changes; copper and pin assignments remain identical.']}


def connection_candidate(project, issue, content):
    """One short, straight, same-net pad-to-track connection; no routing guesses."""
    ids = [i.get('uuid') for i in issue.items]
    locations = board_locations(content)['item_locations']
    if len(ids) != 2 or any(i not in locations for i in ids):
        raise FixError('This connection does not have two exact saved PCB locations.')
    items = [locations[i] for i in ids]
    pads = [i for i in items if i['kind'] == 'pad']
    tracks = [i for i in items if i['kind'] == 'track']
    if len(pads) != 1 or len(tracks) != 1:
        raise FixError('Automatic routing currently supports one pad and one straight track endpoint.')
    pad, track = pads[0], tracks[0]
    net = pad['net']
    if not net or track['net'] != net or len(track['points']) != 2:
        raise FixError('The objects must be on the same named net and the track must be straight.')
    # Limit initial support to return nets: arbitrary signals may have RF, timing,
    # isolation or impedance constraints that a clean DRC does not establish.
    if net.upper() not in ('GND', 'GNDA', 'GNDD', 'AGND', 'DGND', 'VSS'):
        raise FixError('Signal and power routing needs electrical constraints. This version only proposes short ground connections.')
    component = project.components.get(pad.get('reference'))
    pins = [p for p in (component.schematic_pins if component else []) if p.number == pad['pin']]
    if len(pins) != 1 or pins[0].net != net or pins[0].no_connect:
        raise FixError('The schematic does not confirm this pad’s ground connection.')
    if pad.get('shape') not in ('rect', 'circle', 'roundrect', 'oval'):
        raise FixError('This pad shape needs manual routing in this version.')
    layer = track['layers'][0]
    if layer not in pad['layers']:
        raise FixError('These objects do not share a copper layer.')
    end = pad['point']
    start = min(track['points'], key=lambda p: math.dist(p, end))
    length = math.dist(start, end)
    if not .001 < length <= 1.5:
        raise FixError('The gap needs more than a short local connection. Route it in KiCad.')
    width = track['width_mm']
    if not 0 < width <= 1.5:
        raise FixError('The existing track width is outside this tool’s supported range.')
    text = content.decode('utf-8-sig')
    root = parse(text, 'kicad_pcb')
    source_track = next((n for n in root.all('segment') if (n.get('uuid') or n.get('tstamp')) == track['uuid']), None)
    if source_track is None or source_track.first('locked') or 'locked' in source_track.values:
        raise FixError('The source track is locked or cannot be identified.')
    footprint = next((fp for fp in root.all('footprint') if properties(fp).get('Reference') == pad['reference']), None)
    if footprint is None or footprint.first('locked') or 'locked' in footprint.values:
        raise FixError('The footprint is locked or cannot be identified.')
    net_node = source_track.first('net')
    if not net_node or len(net_node.values) != 1 or not net_node.values[0].isdigit():
        raise FixError('This PCB net format is not supported for automatic edits.')
    new_id = str(uuid.uuid4())
    line = (f'\n\t(segment (start {start[0]:.6f} {start[1]:.6f}) (end {end[0]:.6f} {end[1]:.6f}) '
            f'(width {width:g}) (layer {json.dumps(layer)}) (net {net_node.values[0]}) (uuid "{new_id}"))\n')
    candidate = text[:root.end-1] + line + text[root.end-1:]
    return candidate, {'kind': 'ground_connection', 'summary': f'Connect {pad["label"]} to the nearby {net} track on {layer}.',
        'basis': 'KiCad reports a missing connection. Both objects and the matching schematic pin belong to the same ground net.',
        'reference': pad['reference'], 'pad': pad['pin'], 'net': net, 'layer': layer,
        'length_mm': round(length, 4), 'width_mm': width, 'start': start, 'end': end,
        'target_ids': sorted(ids), 'new_uuid': new_id, 'checks': []}


def findings(report):
    if not isinstance(report, dict) or not all(isinstance(report.get(k), list)
            for k in ('violations', 'unconnected_items')):
        raise FixError('KiCad did not return a complete board-check report.')
    rows = []
    for key in ('violations', 'unconnected_items', 'schematic_parity'):
        for item in report.get(key, []):
            if item.get('excluded') or item.get('severity') in ('ignore', 'exclusion'):
                continue
            rows.append((item.get('type', key), item.get('severity', ''),
                         tuple(sorted(i.get('uuid', '') for i in item.get('items', [])))))
    return Counter(rows)


def validate_connection(before, after, relative, binary, target_ids):
    results = []
    for directory in (before, after):
        board = directory / relative
        prepared = prepare_board(binary, board, directory)
        if prepared.get('status') != 'prepared':
            raise FixError('KiCad could not refill the test copy. No edit is available to apply.')
        output = directory / 'fix-drc.json'
        _run(binary, ['pcb', 'drc', '--format', 'json', '--units', 'mm', '--severity-all',
                     '--all-track-errors', '--output', str(output), str(board)], directory, no_fonts=True)
        results.append(findings(json.loads(output.read_text())))
    old, new = results
    target = lambda k: k[0] == 'unconnected_items' and k[2] == tuple(target_ids)
    if not any(target(k) for k in old):
        raise FixError('KiCad no longer reports this exact gap. Update the review instead.')
    if any(target(k) for k in new) or sum(v for k,v in new.items() if k[0]=='unconnected_items') >= sum(v for k,v in old.items() if k[0]=='unconnected_items'):
        raise FixError('The proposed connection did not remove the missing-connection finding.')
    if new - old:
        raise FixError('KiCad found new rule violations with this proposed route. No edit will be applied; route this connection in KiCad.')
    return ['KiCad refilled copper and ran DRC on both copies.',
            'The exact missing-connection finding disappeared and the unconnected count decreased.',
            'No new non-excluded DRC findings were introduced. Existing unrelated findings remain.']


def prepare(project, group, source, folder, *, kicad_cli=None, progress=lambda message: None):
    option = availability(project, group)
    if option['mode'] != 'automatic':
        raise FixError(option['message'])
    if not source or not Path(source).is_dir():
        raise FixError('Connect the local KiCad folder to apply edits. Uploaded files and ZIPs are copies.')
    source = Path(source).resolve()
    relative = project.selected_files.get('pcb')
    hashes = dict(project.provenance.get('source_hashes', {}))
    if not relative or relative not in hashes or project.selected_files.get('schematic') not in hashes:
        raise FixError('Update this project first so both source files can be verified.')
    verify_sources(source, hashes)
    selected = selected_issues(project, group)
    if len(selected) != 1 or selected[0].excluded:
        raise FixError('This finding needs multiple or uncertain edits. Automatic editing is not available for it yet.')
    issue = selected[0]
    content = source_file(source, relative).read_bytes()
    if issue.code == 'VALUE_MISMATCH':
        candidate, plan = value_candidate(project, issue, content.decode('utf-8-sig'))
    elif issue.code == 'KICAD_DRC_UNCONNECTED_ITEMS':
        candidate, plan = connection_candidate(project, issue, content)
    else:
        raise FixError('This edit type is not supported. Open Fix this issue on the finding to see the available repair options. No files were changed.')
    parse(candidate, 'kicad_pcb')
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'original.kicad_pcb').write_bytes(content)
    if plan['kind'] == 'ground_connection':
        binary = find_kicad(kicad_cli)
        if not binary:
            raise FixError('KiCad CLI is required to test a copper edit.')
        before, after = folder/'before', folder/'after'
        before.mkdir();after.mkdir()
        _stage(source, project, before)
        # The staged sources must be the same bytes the review and proposal used.
        for path in before.rglob('*'):
            if path.is_file():
                name = path.relative_to(before).as_posix()
                actual = digest(path.read_bytes())
                if name in hashes and hashes[name] != actual:
                    raise FixError('Source files changed while preparing this fix. Update the review.')
                hashes[name] = actual
        shutil.copytree(before, after, dirs_exist_ok=True)
        (after/relative).write_text(candidate, encoding='utf-8')
        progress('Testing the connection on two temporary PCB copies…')
        plan['checks'] = validate_connection(before, after, relative, binary, plan['target_ids'])
        replacement = (after/relative).read_bytes()
        plan['checks'].append('The edited file also includes KiCad’s copper refill and saved-file formatting.')
    else:
        replacement = candidate.encode('utf-8')
    verify_sources(source, hashes)
    (folder/'replacement.kicad_pcb').write_bytes(replacement)
    diff = ''.join(difflib.unified_diff(content.decode('utf-8-sig').splitlines(True),
                    replacement.decode('utf-8-sig').splitlines(True), fromfile=relative+' (before)', tofile=relative+' (after)'))
    (folder/'changes.diff').write_text(diff)
    plan.update(status='ready', source=str(source), file=relative, hashes=hashes,
                original_sha256=digest(content), replacement_sha256=digest(replacement),
                diff=diff[:40000], diff_truncated=len(diff)>40000, backup=str(folder/'original.kicad_pcb'))
    return plan


def _apply_requested_value(plan, folder, *, undo=False):
    """Write or restore both staged files, verifying every digest first."""
    required = 'applied' if undo else 'ready'
    if plan.get('status') != required:
        raise FixError('This fix is no longer available for that action.')
    if not undo:
        verify_sources(plan['source'], plan['hashes'])
    pairs = [('replacement.kicad_pcb', plan['replacement_sha256'], plan['original_sha256'], plan['file']),
             ('replacement.kicad_sch', plan['replacement_sch_sha256'], plan['original_sch_sha256'], plan['file_sch'])]
    if undo:
        pairs = [('original.kicad_pcb', plan['original_sha256'], plan['replacement_sha256'], plan['file']),
                 ('original.kicad_sch', plan['original_sch_sha256'], plan['replacement_sch_sha256'], plan['file_sch'])]
    staged = {}
    for staged_name, wanted, current, relative in pairs:
        content = (folder/staged_name).read_bytes()
        if digest(content) != wanted:
            raise FixError('The prepared file or backup changed. Prepare a new fix.')
        path = source_file(plan['source'], relative)
        if path.with_name('~'+path.name+'.lck').exists() or path.with_suffix(path.suffix+'.lck').exists():
            raise FixError('Close this board in KiCad before applying or undoing an edit.')
        if digest(path.read_bytes()) != current:
            raise FixError('The project file changed since the preview. Prepare a new fix.')
        staged[relative] = (path, content, current)
    for relative, (path, content, current) in staged.items():
        replace_checked(path, content, current)
    plan['status'] = 'undone' if undo else 'applied'
    return {'status': plan['status'],
            'files': [str(source_file(plan['source'], plan['file'])), str(source_file(plan['source'], plan['file_sch']))],
            'backup': plan['backup'], 'backup_sch': plan['backup_sch'],
            'message': 'Original schematic and PCB restored. Reopen them in KiCad and update the review.' if undo else
                       'Schematic and PCB updated. Reopen them in KiCad, then update the review to check the saved version.'}


def replace_checked(path, content, expected):
    if path.is_symlink() or digest(path.read_bytes()) != expected:
        raise FixError('The file changed since the preview. No edits were written.')
    fd, temporary = tempfile.mkstemp(prefix='.preflight-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content);output.flush();os.fsync(output.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        if digest(path.read_bytes()) != expected:
            raise FixError('The file changed before saving. No edits were written.')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def apply(plan, folder, *, undo=False):
    if plan.get('kind') == 'edit_batch':
        from .board_edits import apply as apply_batch
        return apply_batch(plan, folder, undo=undo)
    if plan.get('kind') == 'requested_value':
        return _apply_requested_value(plan, folder, undo=undo)
    required = 'applied' if undo else 'ready'
    if plan.get('status') != required:
        raise FixError('This fix is no longer available for that action.')
    path = source_file(plan['source'], plan['file'])
    if path.with_name('~'+path.name+'.lck').exists() or path.with_suffix(path.suffix+'.lck').exists():
        raise FixError('Close this board in KiCad before applying or undoing an edit.')
    if not undo:
        verify_sources(plan['source'], plan['hashes'])
    file, wanted, current = ('original.kicad_pcb', plan['original_sha256'], plan['replacement_sha256']) if undo else (
        'replacement.kicad_pcb', plan['replacement_sha256'], plan['original_sha256'])
    content = (folder/file).read_bytes()
    if digest(content) != wanted or digest((folder/'original.kicad_pcb').read_bytes()) != plan['original_sha256']:
        raise FixError('The prepared file or backup changed. Prepare a new fix.')
    replace_checked(path, content, current)
    plan['status'] = 'undone' if undo else 'applied'
    return {'status': plan['status'], 'file': str(path), 'backup': plan['backup'],
            'message': 'Original PCB restored. Reopen it in KiCad and update the review.' if undo else
                       'PCB file updated. Reopen it in KiCad, then update the review to check the saved version.'}

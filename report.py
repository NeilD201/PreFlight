"""Readable component report. JSON output retains the complete source records."""

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from html import escape
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote

from .models import Project
from .checks.components import reference_key
from .review import CATEGORIES, issue_id
from .consequences import consequence_for
from .ai.reviewer import AIError, evidence_packet, INSTRUCTIONS, DESIGN_INSTRUCTIONS


def print_raw_report(project: Project) -> None:
    print(f"Project: {project.archive} — {len(project.files)} files")
    for item in project.files:
        print(f"  {item.kind:22} {item.path} [{item.status}]")
    if project.selected_files:
        print("\nSelected sources:")
        for kind, file in project.selected_files.items():
            print(f"  {kind}: {file}")
    print("\nComponents:")
    for kind in ("schematic", "pcb"):
        if kind not in project.selected_files:
            print(f"  {kind}: not selected")
            continue
        file = next(item for item in project.files if item.path == project.selected_files[kind])
        if file.status == "parse_failed":
            print(f"  {kind}: parsing failed")
            continue
        records = [record for component in project.components.values() for record in getattr(component, kind)]
        references = {record.reference for record in records if record.reference}
        label = "symbol units" if kind == "schematic" else "footprints"
        print(f"  {kind}: {len(references)} references, {len(records)} {label}")
    print(f"  Power/virtual symbols excluded: {project.non_physical_symbols}")
    if project.stage == "component_comparison":
        matched = sum(bool(c.schematic and c.pcb) for c in project.components.values())
        print(f"  References present in both: {matched}")
    if project.connectivity:
        print("\nConnectivity:")
        for key, value in project.connectivity.items():
            print(f"  {key.replace('_', ' ')}: {value}")
    print("\nChecks performed:")
    if not project.checks_performed:
        print("  None — see findings below.")
    for check in project.checks_performed:
        print(f"  {check.replace('_', ' ')}")
    counts = Counter(issue.severity for issue in project.issues)
    print(f"\nFindings: {counts['error']} errors, {counts['warning']} warnings, {counts['info']} information notices")
    checker_issues = [i for i in project.issues if i.source == "checker"]
    for issue in checker_issues[:20]:
        print(f"[{issue.severity}] {issue.code}: {issue.message}")
        for evidence in issue.evidence:
            print(f"  Source: {evidence}")
        if issue.suggested_fix:
            print(f"  Next: {issue.suggested_fix}")
    if len(checker_issues) > 20:
        print(f"  {len(checker_issues) - 20} more checker findings are in the full report.")
    for engine in ("kicad_erc", "kicad_drc"):
        items = [i for i in project.issues if i.source == engine]
        if not items:
            continue
        print(f"\n{engine.replace('_', ' ').upper()}: {len(items)} findings")
        groups = defaultdict(list)
        for item in items:
            groups[(item.severity, item.code)].append(item)
        for (severity, code), findings in sorted(groups.items()):
            print(f"  [{severity}] {code}: {len(findings)}")
            print(f"    Example: {findings[0].message}")


def print_report(project: Project, *, raw: bool = False) -> None:
    if raw:
        print_raw_report(project)
        return
    review = project.review
    print(f"\nPCB PREFLIGHT — {project.archive}")
    print(review.get('headline', 'Analysis incomplete.'))
    conn = project.connectivity
    print(f"{len(project.components)} components | {conn.get('pins_compared', 0)} pins compared | "
          f"{conn.get('schematic_nets', 0)} schematic nets")
    print("\nWHAT NEEDS ATTENTION")
    attention = [g for g in review.get('groups', []) if g['priority'] in ('fix', 'review')]
    for group in attention[:6]:
        print(f"\n[{group['id']}] {group['title']} ({CATEGORIES[group['category']]})")
        print(f"  {group['summary']}")
        print(f"  Next: {group['next_step']}")
    if not attention:
        print('  No action groups from the checks that ran. Review coverage before drawing conclusions.')
    if len(attention) > 6:
        print(f"\n  {len(attention) - 6} more review groups and full explanations are in the HTML report.")
    notes = [g for g in review.get('groups', []) if g['priority'] == 'note']
    if notes:
        print('\nOTHER FINDINGS')
        for g in notes:
            print(f"  [{g['id']}] {g['title']} — {g['raw_count']} underlying findings")
    counts = review.get('raw_counts', {})
    print(f"\nRaw rule counts: {counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings, {counts.get('info', 0)} notices.")
    print('These are rule findings, not a count of failed parts. Use --raw for the original listing.')
    ai = project.ai_review
    print(f"\nAI REVIEW: {ai.get('status', 'not_requested').replace('_', ' ')}")
    if ai.get('status') == 'completed':
        result = ai['result']
        print(f"  {result['summary']}")
        audit = ai.get('circuit_audit')
        if audit:
            print(f"  Circuit analysis: {audit['reviewed_count']}/{audit['target_count']} active parts, {audit['check_count']} comparisons, {audit['concern_count']} concerns.")
            for item in audit.get('unreviewed', []):
                print(f"  {item['reference']} not reviewed: {item['message']}")
        if result['answer']:
            print(f"\n  {result['answer']}")
        for finding in result.get('findings', []):
            print(f"  - {finding['title']} [AI interpretation, {finding['confidence']} confidence]")
            print(f"    {finding['interpretation']} Evidence: {', '.join(finding['evidence_ids'])}")
            print(f"    Next: {finding['next_step']}")
    else:
        print('  ' + ai.get('message', 'Use --ai to request a Gemini review (free tier available), or --ask "your question".'))
        if ai.get('status') == 'failed':
            print('  Run python3 main.py --test-ai to test the API without rerunning KiCad.')


def inspect_component(project: Project, reference: str) -> str:
    if reference not in project.components:
        raise ValueError(f"Component {reference!r} was not found. Use the exact reference from the report.")
    component = project.components[reference]
    lines = [f"Component {reference}"]
    for kind in ("schematic", "pcb"):
        for record in getattr(component, kind):
            lines.append(f"  {kind}: {record.value} | {record.footprint} | {record.location}")
    for pin in sorted(component.schematic_pins, key=lambda p: reference_key(p.number)):
        pads = [p for record in component.pcb for p in record.pads if p.number == pin.number]
        actual = ', '.join(sorted({p.net or '(no net)' for p in pads})) or '(pad missing)'
        state = ' [no-connect]' if pin.no_connect else ''
        lines.append(f"  Pin {pin.number} {pin.name}{state}: schematic {pin.net or '(no net)'} | PCB {actual}")
    if not component.schematic_pins:
        lines.append("  Schematic pin connectivity is unavailable; see the report's coverage notices.")
    return '\n'.join(lines)


def _component_html(project: Project) -> str:
    parts = []
    for ref, component in project.components.items():
        records = component.schematic or component.pcb
        value = records[0].value if records else ""
        footprint = records[0].footprint if records else ""
        rows = []
        pins = {p.number: p for p in component.schematic_pins}
        pads = defaultdict(list)
        for record in component.pcb:
            for pad in record.pads:
                if pad.number:
                    pads[pad.number].append(pad)
        for number in sorted(set(pins) | set(pads), key=reference_key):
            pin = pins.get(number)
            actual = ', '.join(sorted({p.net or '(no net)' for p in pads[number]})) or '(pad missing)'
            name = pin.name if pin else ''
            expected = pin.net if pin else '(no exported pin)'
            state = 'No-connect' if pin and pin.no_connect else (pin.electrical_type if pin else 'Review pad intent')
            rows.append('<tr>' + ''.join(f'<td>{escape(str(v))}</td>' for v in (number, name, expected, actual, state)) + '</tr>')
        sources = ''.join(f'<li>{escape(kind)} · {escape(r.location)} · {escape(r.value)} · {escape(r.footprint)}</li>'
                          for kind in ('schematic', 'pcb') for r in getattr(component, kind))
        placements = ''.join(f'<p>PCB: {r.x_mm}, {r.y_mm} mm · rotation {r.rotation}° · {escape(r.layer)}</p>' for r in component.pcb)
        identity = project.circuit.get('identities', {}).get(ref, {})
        identity_html = ''.join(f'<p>{label}: {escape(identity.get(key) or "Not specified")}</p>'
                                for key, label in [('mpn', 'Manufacturer part number'), ('manufacturer', 'Manufacturer'), ('lcsc', 'LCSC part')])
        parts.append(f'<details id="component-{escape(ref, quote=True)}" class="component searchable"><summary><strong>{escape(ref)}</strong> '
            f'<span>{escape(value)}</span><small>{escape(footprint)}</small></summary><div class="detail">'
            f'<ul>{sources}</ul>{placements}{identity_html}<div class="table-scroll"><table><thead><tr>'
            '<th>Pin</th><th>Function</th><th>Schematic net</th><th>PCB net</th><th>Pin type</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></div></details>')
    return ''.join(parts)


def _issues_html(project: Project) -> str:
    groups = defaultdict(list)
    for index, issue in enumerate(project.issues):
        groups[(issue.severity, issue.source, issue.code)].append((index, issue))
    parts = []
    order = {"error": 0, "warning": 1, "info": 2}
    for (severity, source, code), issues in sorted(groups.items(), key=lambda v: (order[v[0][0]], v[0][1], v[0][2])):
        cards = []
        for index, issue in issues:
            evidence = ''.join(f'<li>{escape(item)}</li>' for item in issue.evidence)
            fix = f'<p class="fix">Next: {escape(issue.suggested_fix)}</p>' if issue.suggested_fix else ''
            cards.append(f'<article id="{issue_id(index)}" class="finding" data-severity="{escape(severity)}">'
                         f'<small>{issue_id(index)}</small><p>{escape(issue.message)}</p><ul>{evidence}</ul>{fix}</article>')
        parts.append(f'<details class="issue-group"><summary><span class="badge {escape(severity)}">{escape(severity)}</span>'
                     f'<strong>{escape(code)}</strong><span class="group-count">{len(issues)}</span>'
                     f'<small>{escape(source)}</small></summary>{"".join(cards)}</details>')
    return ''.join(parts)


def _source_links(sources: list[dict]) -> str:
    # Only trusted rule-catalog sources become external links; project fields remain plain text.
    return ' '.join(f'<a href="{escape(s["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{escape(s["title"])}</a>'
                    for s in sources if s.get('url', '').startswith('https://'))


def _review_html(project: Project) -> str:
    parts = []
    data = asdict(project)
    actions = {a['group_id']: a for a in project.action_plan.get('actions', [])}
    if project.action_plan.get('device'):
        device = project.action_plan['device']
        parts.append('<section class="review-card"><h3>What this device is meant to do</h3><p>' + escape(device['description']) + '</p><small>' + escape(device['basis']) + '</small></section>')
    for g in project.review.get('groups', []):
        action = actions.get(g['id'])
        impact = consequence_for(g, data, action)
        consequence = '<h4>If you leave it as it is</h4><p>' + escape(impact['text']) + '</p>'
        if impact['context_note']:
            consequence += '<p>' + escape(impact['context_note']) + '</p>'
        plan = ('<h4>Proposed steps</h4><ol>' + ''.join('<li>' + escape(s) + '</li>' for s in action['steps']) + '</ol>'
                + '<p>' + escape(action.get('context_response', '')) + '</p>'
                + '<p>' + escape(action.get('intent_note', '')) + '</p>') if action else ''
        evidence = ''.join(f'<li>{escape(e)}</li>' for e in g['evidence'])
        links = ' '.join(f'<a href="#{fid}" data-jump="findings">{fid}</a>' for fid in g['issue_ids'])
        next_markup = '' if action else '<p class="next"><strong>Next step</strong> ' + escape(g['next_step']) + '</p>'
        parts.append(f'<article class="review-card searchable" id="{g["id"]}" data-category="{g["category"]}" data-priority="{g["priority"]}">'
            f'<div class="card-meta"><span class="badge {g["priority"]}">{escape(CATEGORIES[g["category"]])}</span>'
            f'<span>{g["id"]} · {escape(g["basis"])}</span></div><h3>{escape(g["title"])}</h3>'
            f'<p>{escape(g["summary"])}</p><p class="meaning">{escape(action["reason"] if action else g["why"])}</p>'
            f'{consequence}{next_markup}{plan}'
            f'<details class="evidence"><summary>Evidence · {g["raw_count"]} underlying findings</summary>'
            f'<ul>{evidence}</ul><p>{_source_links(g["sources"])}</p><p class="evidence-links">{links}</p></details></article>')
    return ''.join(parts)


def _ai_html(project: Project) -> str:
    ai = project.ai_review
    if ai.get('status') != 'completed':
        diagnostic = ('<p>Test the API without rerunning KiCad: <code>python3 main.py --test-ai</code>. '
                      'Use the same <code>--ai-provider</code> and <code>--ai-model</code> options as this run.</p>'
                      if ai.get('status') == 'failed' else '')
        setup = ('<p>Run <code>python3 main.py --ai</code> and paste a Meta API key at the hidden prompt, '
                 'or set <code>LLAMA_API_KEY</code>. Meta usage is billed to your Meta account.</p>'
                 '<p>Only Meta Muse Spark is supported. '
                 '<a href="https://www.llama.com/docs/model-cards-and-metrics/">Model card</a></p>')
        return ('<div class="ai-status"><h3>AI review has not run</h3>'
                f'<p>{escape(ai.get("message", "Local interpretations above are generated by rules."))}</p>' + diagnostic + setup +
                '<p>The AI receives component identities, pin/net assignments, findings and sourced rules. '
                'The full archive is not sent. <a href="ai-input.json">Inspect the evidence packet</a>.</p></div>')
    result = ai['result']
    cards = []
    for finding in result.get('findings', []):
        citations = []
        for eid in finding['evidence_ids']:
            if eid.startswith('R'):
                target, panel = eid, 'review'
            elif eid.startswith('C:'):
                target, panel = 'component-' + eid[2:], 'components'
            else:
                target, panel = 'net-' + eid[2:], 'nets'
            citations.append(f'<a data-jump="{panel}" href="#{quote(target, safe="")}">{escape(eid)}</a>')
        cards.append('<article class="review-card searchable"><div class="card-meta"><span class="badge">AI interpretation</span>'
            f'<span>{escape(finding["confidence"])} confidence · {escape(finding["impact"])}</span></div>'
            f'<h3>{escape(finding["title"])}</h3><p>{escape(finding["interpretation"])}</p>'
            f'<p class="next"><strong>Next step</strong> {escape(finding["next_step"])}</p><p>{" · ".join(citations)}</p></article>')
    for action in result.get('actions', []):
        steps = ''.join(f'<li>{escape(step)}</li>' for step in action['steps'])
        from .datasheets import excerpts
        documents = {d['evidence_id']: d for d in excerpts(project)}
        citations = ''.join('<blockquote>' + escape(c['quote']) + '</blockquote>' + _source_links([
            {'url':documents[c['evidence_id']]['url'], 'title':f"PDF page {documents[c['evidence_id']]['page']}"}])
            for c in action['citations'] if c['evidence_id'] in documents)
        cards.append(f'<article class="review-card"><h3>{escape(action["group_id"])} · {escape(action["title"])}</h3>'
                     f'<p>{escape(action["disposition"])} · AI interpretation</p><p>{escape(action["reason"])}</p>'
                     f'<ol>{steps}</ol><p>{escape(action["context_response"])}</p>{citations}</article>')
    if result.get('device_description'):
        cards.insert(0, '<h3>Device interpretation</h3><p>' + escape(result['device_description']) + '</p>')
    questions = ''.join(f'<li>{escape(q)}</li>' for q in result['questions'])
    limits = ''.join(f'<li>{escape(q)}</li>' for q in result['limitations'])
    answer = f'<div class="ai-answer"><h3>{escape(ai.get("question", ""))}</h3><p>{escape(result["answer"])}</p></div>' if result['answer'] else ''
    return (f'<div class="ai-status"><p class="eyebrow">AI INTERPRETATION · {escape(ai.get("provider", ""))} · {escape(ai["model"])}</p>'
            f'<p>{escape(result["summary"])}</p><p class="hint">Evidence IDs were validated. Conclusions have not been independently verified; no live datasheet search or simulation was performed.</p></div>'
            + answer + ''.join(cards) + f'<h3>Open questions</h3><ul>{questions}</ul><h3>Limits</h3><ul>{limits}</ul>')


def _circuit_html(project: Project) -> str:
    circuit = project.circuit
    if circuit.get('status') != 'available':
        return '<p>Circuit context requires a successful KiCad netlist and supported schematic/PCB pair.</p>'
    rows = ''.join('<tr class="searchable">' + ''.join(f'<td>{escape(str(c[k]))}</td>' for k in ('reference', 'value', 'rail', 'ground')) + '</tr>'
                   for c in circuit['bypass_capacitors'])
    buses = []
    for bus in circuit['bus_candidates']:
        pullups = ', '.join(f'{p["reference"]} ({p["value"]}) to {p["rail"]}' for p in bus['pullups']) or 'None found'
        buses.append(f'<article class="review-card searchable"><h3>{escape(bus["net"])} · I²C candidate</h3>'
                     f'<p>External pull-ups: {escape(pullups)}</p><p>{escape(bus["interpretation"])}</p></article>')
    return (f'<h3>Supply-to-ground capacitor connections</h3><p class="hint">{len(circuit["bypass_capacitors"])} capacitors found. Connectivity alone does not verify required values, placement or decoupling quality.</p>'
            '<div class="table-scroll"><table><thead><tr><th>Reference</th><th>Value</th><th>Supply net</th><th>Ground</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div><h3>Bus connections</h3>' + ''.join(buses))


def html_report(project: Project) -> str:
    template_dir = Path(__file__).parent / 'templates'
    template = (template_dir / 'report.html').read_text()
    counts = Counter(i.severity for i in project.issues)
    net_rows = []
    for kind, nets in (("Schematic", project.schematic_nets), ("PCB", project.pcb_nets)):
        for name, members in nets.items():
            anchor = f' id="net-{escape(name, quote=True)}"' if kind == 'Schematic' else ''
            net_rows.append(f'<tr class="searchable"{anchor}><td>{kind}</td><td>{escape(name)}</td><td>{len(members)}</td>'
                            f'<td>{escape(", ".join(members))}</td></tr>')
    checks = ''.join(f'<li>{escape(c.replace("_", " "))}</li>' for c in project.checks_performed)
    coverage = ', '.join(f'{key.replace("_", " ")}: {value}' for key, value in project.connectivity.items()) or 'Connectivity not performed'
    substitutions = {
        'title': escape(project.archive), 'date': escape(datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')),
        'errors': str(counts['error']), 'warnings': str(counts['warning']), 'information': str(counts['info']),
        'components_count': str(len(project.components)), 'engine': escape(project.engine.get('version', 'Not used')),
        'issues': _issues_html(project), 'components': _component_html(project), 'nets': ''.join(net_rows),
        'review': _review_html(project), 'ai': _ai_html(project), 'circuit': _circuit_html(project),
        'headline': escape(project.review.get('headline', 'Analysis incomplete.')),
        'fix_count': str(project.review.get('fix_count', 0)), 'review_count': str(project.review.get('review_count', 0)),
        'ai_status': escape(project.ai_review.get('status', 'not_requested').replace('_', ' ')),
        'checks': checks, 'coverage': escape(coverage),
        'styles': (template_dir / 'report.css').read_text(), 'script': (template_dir / 'report.js').read_text(),
    }
    # One pass prevents user-controlled placeholder-looking text from being expanded.
    return re.sub(r'\{\{([a-z_]+)\}\}', lambda m: substitutions[m[1]], template)


def save_report(project: Project, output_root: str | Path, board_visual=None, schematic_visual=None) -> Path:
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[^A-Za-z0-9_-]+', '-', Path(project.archive).stem).strip('-')[:50] or 'project'
    prefix = f'{name}-{datetime.now().strftime("%Y%m%d-%H%M%S")}-'
    staging = Path(tempfile.mkdtemp(prefix='.pending-'+prefix, dir=root))
    try:
        from .assembly_export import assembly_files, assembly_bundle
        from .release_review import before_ordering
        data = asdict(project)
        assets = getattr(project, '_fabrication_assets', {})
        for filename, hash_key in [('fabrication-draft.zip','package_sha256'),('analyzed-board.kicad_pcb','analyzed_pcb_sha256')]:
            if filename in assets:
                import hashlib
                if hashlib.sha256(assets[filename]).hexdigest() != project.fabrication.get(hash_key):
                    raise ValueError('Fabrication snapshot checksum does not match its report.')
                (staging / filename).write_bytes(assets[filename])
        if project.fabrication.get('status') == 'draft' and len(assets) != 2:
            raise ValueError('Fabrication snapshot is missing. Run a full Update to regenerate it.')
        (staging / 'before-ordering.json').write_text(json.dumps(before_ordering(data),indent=2),encoding='utf-8')
        for filename, content in assembly_files(data).items():
            (staging / filename).write_bytes(content)
        if project.assembly_export:
            (staging / 'assembly-draft.zip').write_bytes(assembly_bundle(data))
        if board_visual:
            visual_meta, visual_assets = board_visual
            (staging / 'board-visual.json').write_text(json.dumps(visual_meta), encoding='utf-8')
            for filename, content in visual_assets.items():
                if filename not in ('board-front.svg', 'board-back.svg'):
                    raise ValueError('Unexpected board preview filename.')
                (staging / filename).write_bytes(content)
        if schematic_visual:
            metadata, assets = schematic_visual
            (staging/'schematic-visual.json').write_text(json.dumps(metadata),encoding='utf-8')
            for filename, content in assets.items():
                if filename != 'schematic.svg':raise ValueError('Unexpected schematic preview filename.')
                (staging/filename).write_bytes(content)
        (staging / 'report.json').write_text(json.dumps(asdict(project), indent=2), encoding='utf-8')
        (staging / 'report.html').write_text(html_report(project), encoding='utf-8')
        for kind, raw in project.native_reports.items():
            if kind in ('erc', 'drc', 'expanded-erc', 'expanded-drc'):
                (staging / f'kicad-{kind}.json').write_text(json.dumps(raw, indent=2), encoding='utf-8')
        (staging / 'ai-review.json').write_text(json.dumps(project.ai_review, indent=2), encoding='utf-8')
        try:
            packet = evidence_packet(project, project.ai_review.get('question', ''))
            (staging / 'ai-input.json').write_text(json.dumps(packet, indent=2), encoding='utf-8')
            instructions = DESIGN_INSTRUCTIONS if project.action_plan else INSTRUCTIONS
            (staging / 'ai-prompt.txt').write_text(instructions + '\n\nPROJECT EVIDENCE\n' + json.dumps(packet, indent=2), encoding='utf-8')
        except AIError as exc:
            (staging / 'ai-input.json').write_text(json.dumps({'status': 'unavailable', 'reason': str(exc)}), encoding='utf-8')
        destination = root / staging.name.removeprefix('.pending-')
        if destination.exists():
            raise OSError('A report destination already exists. Run again to save to a new folder.')
        staging.rename(destination)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)

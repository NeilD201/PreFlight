"""Review state, revision comparisons and portable review exports."""

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import zipfile


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def keyed_groups(data):
    result = []
    for group in data.get('review',{}).get('groups',[]):
        issues = [data['issues'][int(fid[1:])-1] for fid in group.get('issue_ids',[])
                  if fid.startswith('F') and fid[1:].isdigit() and 0<int(fid[1:])<=len(data.get('issues',[]))]
        identity = [group.get('rule'), group.get('category'), group.get('title'),
                    sorted(group.get('components',[])), sorted({i['code'] for i in issues})]
        facts = [{k:i.get(k) for k in ('code','severity','message','component','details','items','excluded')} for i in issues]
        signature = digest([group.get('summary'),group.get('why'),group.get('next_step'),sorted(facts,key=digest)])
        result.append({**group,'key':digest(identity)[:32],'signature':signature})
    return result


def readiness(data, annotations=None):
    from .footprint_context import footprint_rule_context
    annotations = annotations or {}
    groups = keyed_groups(data)
    actions_by_id={a['group_id']:a for a in data.get('action_plan',{}).get('actions',[])}
    attention = [g for g in groups if g['priority'] in ('fix','review') and not footprint_rule_context(g, data)
                 and actions_by_id.get(g['id'],{}).get('disposition')!='no_change']
    def status(g):
        item=annotations.get(g['key'],{})
        return item.get('status','open') if item.get('signature')==g['signature'] else 'open'
    pending = [g for g in attention if status(g)=='open']
    assembly = data.get('assembly',{})
    parts = [p for p in assembly.get('parts',[]) if p['population']=='fitted']
    declared = sum(p['status']=='part_number_declared' for p in parts)
    generated = data.get('assembly_export',{}).get('counts',{})
    assigned = generated.get('assigned',declared)
    sheets = data.get('datasheets',{})
    checks = set(data.get('checks_performed',[]))
    coverage = [
        {'label':'Schematic ↔ PCB connections','status':'checked' if data.get('connectivity',{}).get('status')=='compared' else 'missing',
         'detail':f'{data.get("connectivity",{}).get("pins_compared",0)} pin assignments compared'},
        {'label':'KiCad electrical rules','status':'checked' if 'kicad_erc' in checks else 'missing','detail':'Uses saved schematic rules'},
        {'label':'KiCad board rules','status':'checked' if 'kicad_drc' in checks else 'missing',
         'detail':'Checked after copper refill on a temporary copy' if data.get('board_checks',{}).get('drc_basis')=='refilled_copy' else 'Saved copper fills only; run Update for automatic refill'},
        {'label':'BOM / placement / PCB','status':'checked' if assembly.get('status')=='compared' else 'partial' if assembly.get('status')=='partial' else 'missing',
         'detail':f'{assembly.get("bom_count",0)} BOM references · {assembly.get("cpl_count",0)} placement references'},
        {'label':'Part identities','status':'assigned' if parts and assigned==len(parts) else 'partial' if assigned else 'missing',
         'detail':f'{assigned}/{len(parts)} fitted parts have an assigned or declared identity; {generated.get("provisional",0)} automatic candidates need review'},
        {'label':'LCSC datasheets','status':'partial' if sheets.get('ready') else 'missing',
         'detail':f'{sheets.get("ready",0)} parts with readable PDFs; selected text excerpts are available for review'},
        {'label':'Manufacturer dimensions','status':'partial' if data.get('manufacturing',{}).get('checks') else 'missing',
         'detail':data.get('manufacturing',{}).get('summary') or 'Select a manufacturer; see Before ordering for the remaining verification'},
    ]
    actions = []
    if pending: actions.append(f'Review {len(pending)} open finding groups and record the decision for each.')
    if generated: actions.append('Review the generated BOM selections and verify the CPL in the assembler preview before ordering.')
    elif assembly.get('status')!='compared': actions.append('Include the intended BOM and placement CSVs and resolve their import gaps before assembly ordering.')
    if parts and assigned<len(parts): actions.append(f'Assign or reconcile part numbers for {len(parts)-assigned} fitted components.')
    if data.get('connectivity',{}).get('status')!='compared': actions.append('Resolve source selection or KiCad export errors to compare connections.')
    actions.append('Confirm the board process and critical component requirements with the manufacturer datasheets and assembler.')
    return {'open':len(pending),'reviewed':sum(status(g)=='reviewed' for g in attention),
            'accepted_risk':sum(status(g)=='accepted_risk' for g in attention),
            'coverage':coverage,'next_actions':actions,'part_numbers_declared':declared,'fitted_parts':len(parts),
            'headline':f'{len(pending)} review groups still need a decision.' if pending else 'Review decisions recorded. Verification gaps remain.',
            'note':'Review decisions record engineering judgment. They do not change the original findings or certify the design.'}


def compare_runs(before, after):
    # Source selection and check coverage must stay visible when findings disappear.
    old_checks, new_checks = set(before.get('checks_performed',[])),set(after.get('checks_performed',[]))
    lost = sorted(old_checks-new_checks)
    old = {g['key']:g for g in keyed_groups(before)}
    new = {g['key']:g for g in keyed_groups(after)}
    def identity(data, group):
        codes = sorted({data['issues'][int(fid[1:])-1]['code'] for fid in group.get('issue_ids', [])
                        if fid.startswith('F') and fid[1:].isdigit() and 0 < int(fid[1:]) <= len(data.get('issues', []))})
        return digest([group.get('rule'), group.get('category'), sorted(group.get('components', [])), codes])
    # Wording and numbered report IDs can change without the underlying issue changing.
    old_ids = Counter(identity(before, g) for g in old.values())
    new_ids = Counter(identity(after, g) for g in new.values())
    lookup = {identity(before, g):k for k,g in old.items()}
    for key, group in list(new.items()):
        ident = identity(after, group)
        matched = lookup.get(ident)
        if key not in old and matched not in new and old_ids[ident] == new_ids[ident] == 1:
            new[matched] = new.pop(key)
    def brief(g): return {k:g.get(k) for k in ('id','title','category','priority','raw_count','key')}
    groups = {'new':[brief(new[k]) for k in sorted(new.keys()-old.keys(),key=lambda k:new[k]['id'])],
              'no_longer_reported':[brief(old[k]) for k in sorted(old.keys()-new.keys(),key=lambda k:old[k]['id'])],
              'changed':[{'before':brief(old[k]),'after':brief(new[k])} for k in sorted(old.keys()&new.keys(),key=lambda k:new[k]['id']) if old[k]['signature']!=new[k]['signature']],
              'unchanged':sum(old[k]['signature']==new[k]['signature'] for k in old.keys()&new.keys())}
    def component_signature(c):
        return {source:[{key:r.get(key) for key in ('value','footprint','dnp','x_mm','y_mm','rotation','layer','mpn','lcsc')}
                        for r in c.get(source,[])] for source in ('schematic','pcb','bom','cpl')}
    a,b=before.get('components',{}),after.get('components',{})
    components={'added':sorted(b.keys()-a.keys()),'removed':sorted(a.keys()-b.keys()),
                'changed':[{'reference':ref,'before':component_signature(a[ref]),'after':component_signature(b[ref])}
                           for ref in sorted(a.keys()&b.keys()) if component_signature(a[ref])!=component_signature(b[ref])]}
    a,b=before.get('schematic_nets',{}),after.get('schematic_nets',{})
    nets=[{'name':name,'before':a.get(name,[]),'after':b.get(name,[])} for name in sorted(a.keys()|b.keys())
          if set(a.get(name,[]))!=set(b.get(name,[]))]
    # A vanished warning is only cleared when the replacement evidence is usable.
    blockers = []
    if lost: blockers.append('Some checks did not run again.')
    identity = before.get('provenance', {}).get('schematic_uuid')
    if not identity or identity != after.get('provenance', {}).get('schematic_uuid'):
        blockers.append('The checker could not confirm this is the same schematic.')
    for kind in before.get('selected_files', {}):
        if before['selected_files'][kind] and not after.get('selected_files', {}).get(kind):
            blockers.append(f'The updated project is missing its {kind} file.')
    if before.get('connectivity', {}).get('status') == 'compared' and after.get('connectivity', {}).get('status') != 'compared':
        blockers.append('The updated connections could not be checked.')
    previous_gaps = {k for k,g in old.items() if g.get('category') == 'coverage'}
    if any(g.get('category') == 'coverage' and (k not in previous_gaps or g['signature'] != old[k]['signature']) for k,g in new.items()):
        blockers.append('The updated run has a new check or file-reading problem.')
    old_hashes = before.get('provenance', {}).get('source_hashes', {})
    new_hashes = after.get('provenance', {}).get('source_hashes', {})
    files_changed = bool(old_hashes and new_hashes and old_hashes != new_hashes)
    if not files_changed: blockers.append('No changed source files were confirmed.')
    checklist = []
    for key, group in old.items():
        if group.get('priority') not in ('fix', 'review') or group.get('category') == 'coverage': continue
        if key in new:
            status = 'remaining'
            reason = 'This still needs attention.'
            if group['signature'] != new[key]['signature']:
                reason = 'The details changed. Check the updated advice.'
            current = new[key]['id']
        else:
            missing = [ref for ref in group.get('components', []) if ref not in after.get('components', {})]
            previous_codes = {before['issues'][int(fid[1:])-1]['code'] for fid in group.get('issue_ids', [])
                              if fid.startswith('F') and fid[1:].isdigit() and 0 < int(fid[1:]) <= len(before.get('issues', []))}
            related = any(issue.get('code') in previous_codes and
                          (not group.get('components') or not issue.get('component') or issue['component'] in group['components'])
                          for issue in after.get('issues', []))
            reasons = blockers + (['The affected part was removed or renamed: ' + ', '.join(missing)] if missing else [])
            if group.get('basis') == 'AI schematic review':
                reasons.append('AI no longer raised this concern. Check the wiring and requirement before marking it fixed.')
            elif group.get('schematic_check') and not any(
                    check.get('rule') == group.get('rule') and check.get('reference') in group.get('components', [])
                    and check.get('status') in ('checked', 'connection_found')
                    for check in after.get('circuit', {}).get('schematic_checks', [])):
                reasons.append('This specific datasheet check could not confirm the updated connection.')
            if related: reasons.append('A related warning is still present, possibly under a different group. Review it before calling this fixed.')
            status = 'unverified' if reasons else 'cleared'
            reason = ' '.join(reasons) if reasons else 'The files changed and this issue was not found when the checks ran again.'
            current = None
        checklist.append({**brief(group), 'status':status, 'reason':reason, 'current_id':current})
    progress = {'items':checklist, 'cleared':sum(i['status']=='cleared' for i in checklist),
                'remaining':sum(i['status']=='remaining' for i in checklist),
                'unverified':sum(i['status']=='unverified' for i in checklist),
                'new':sum(g.get('priority') in ('fix','review') and g.get('category')!='coverage' for g in groups['new']),
                'files_changed':files_changed, 'blockers':blockers}
    return {'progress':progress,'groups':groups,'components':components,'nets':nets,'lost_checks':lost,
            'coverage_comparable':not lost,
            'warning':'Checks present in the baseline did not run in the current report. Missing findings must not be treated as fixes.' if lost else
                      'No longer reported means absent from this run; it is not independent confirmation of a repair.',
            'before_hashes':before.get('provenance',{}).get('source_hashes',{}),
            'after_hashes':after.get('provenance',{}).get('source_hashes',{})}


def safe_csv(headers, rows):
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    def safe(value):
        text=str(value if value is not None else '')
        return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')) else text
    writer.writerow(headers)
    writer.writerows([[safe(value) for value in row] for row in rows])
    return stream.getvalue().encode('utf-8-sig')


def review_bundle(data, annotations, events, report_html, board_files=None):
    """Review evidence plus any generated draft assembly outputs."""
    from .assembly_export import assembly_files
    from .release_review import before_ordering
    state=readiness(data,annotations);groups=keyed_groups(data)
    files={'report.json':json.dumps(data,indent=2).encode(), 'report.html':report_html,
           'review-decisions.json':json.dumps({'decisions':annotations,'history':events},indent=2).encode()}
    files.update(assembly_files(data))
    files['before-ordering.json'] = json.dumps(before_ordering(data),indent=2).encode()
    for name, content in (board_files or {}).items():
        if name not in ('board-front.svg','board-back.svg','board-visual.json','schematic.svg','schematic-visual.json'):
            raise ValueError('Unexpected board preview filename.')
        files[name] = content
    if data.get('action_plan'):
        files['change-plan.json']=json.dumps(data['action_plan'],indent=2).encode()
        files['design-context.json']=json.dumps(data.get('design_context',{}),indent=2).encode()
    files['findings-review.csv']=safe_csv(['Group','Priority','Category','Title','Decision','Note','Next step'],
        [[g['id'],g['priority'],g['category'],g['title'],annotations.get(g['key'],{}).get('status','open'),
          annotations.get(g['key'],{}).get('note',''),g.get('next_step','')] for g in groups])
    files['parts-review.csv']=safe_csv(['Reference','Value','Footprint','MPN','Manufacturer','LCSC','Population','Identity status'],
        [[p.get(k,'') for k in ('reference','value','footprint','mpn','manufacturer','lcsc','population','status')]
         for p in data.get('assembly',{}).get('parts',[])])
    files['READ-ME.txt']=(f'PCB Preflight review package — {data["archive"]}\n\n{state["headline"]}\n{state["note"]}\n\n'
        +'\n'.join('- '+a for a in state['next_actions'])
        +'\n\nReview CSVs are worksheets. When present, bom-draft.csv and cpl-draft.csv are assembly drafts; see assembly-notes.txt and sourcing-review.csv before ordering. Untrusted text beginning with formula characters is escaped for spreadsheet safety.\n'
        +'Keys and original KiCad design files are not included. Report evidence and user notes may contain project information.\n').encode()
    files['manifest.json']=json.dumps({'format_version':1,'checker_version':'0.6.0','created':datetime.now(timezone.utc).isoformat(),
        'source_hashes':data.get('provenance',{}).get('source_hashes',{}),
        'files':{name:hashlib.sha256(content).hexdigest() for name,content in files.items()}},indent=2).encode()
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,content in files.items(): archive.writestr(name,content)
    return output.getvalue()

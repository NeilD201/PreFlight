"""Design intent and actionable review plans; original rule findings stay intact."""
from dataclasses import asdict
from copy import deepcopy
import re

from .datasheets import part_signature
from .workflow import digest, keyed_groups
from .consequences import add_consequences
from .footprint_context import apply_footprint_context
from .advice import apply_advice
from .product_context import merge_answers, project_questions


def context_key(project):
    """Per-project design-context identity. The archive path keeps revisions of
    one board on one row while keeping different boards apart: sharing a row
    would leak one project's questions and answers into another's review."""
    return digest([project.provenance.get('project_name', project.archive),
                   project.provenance.get('schematic_uuid') or project.selected_files,
                   project.archive])


def empty_context():
    return {'revision': 0, 'description': '', 'operating_context': {}, 'intentions': {}, 'part_choices': {}}


def edit_context(project, current, data):
    result = {**empty_context(), **current, 'intentions': dict(current.get('intentions', {})),
              'part_choices': dict(current.get('part_choices', {}))}
    if 'product_context' in data:
        previous=project_questions(project,current.get('product_context',{}),context=current)['answers']
        result['product_context'] = merge_answers(previous,data['product_context'])
        questionnaire=project_questions(project,result['product_context'],context=result)
        from .user_profile import guided_questions
        guided=guided_questions(project,questionnaire['answers'],context=result)
        questionnaire['answers']=guided['answers']
        questionnaire['all_questions']+=guided['all_questions']
        result['product_context']=questionnaire['answers']
        result['product_questions']={**current.get('product_questions',{}),
            **{q['id']:q['question'] for q in questionnaire['all_questions']}}
    if 'description' in data:
        value = data['description']
        if not isinstance(value, str) or len(value) > 6000 or '\x00' in value:
            raise ValueError('Keep the device description under 6,000 characters.')
        result['description'] = value.strip()
    if 'operating_context' in data:
        values = data['operating_context']
        if not isinstance(values, dict) or set(values) - {'interfaces', 'power', 'radio', 'fabrication'}:
            raise ValueError('Use the supported operating context fields.')
        result['operating_context'] = dict(current.get('operating_context', {}))
        for key, value in values.items():
            if not isinstance(value, str) or len(value) > 1500 or '\x00' in value:
                raise ValueError('Keep each operating context field under 1,500 characters.')
            result['operating_context'][key] = value.strip()
    if 'intention' in data:
        item = data['intention']
        groups = {g['key']: g for g in keyed_groups(asdict(project))}
        if not isinstance(item, dict) or item.get('key') not in groups:
            raise ValueError('Choose a finding from this run.')
        note = item.get('note')
        if not isinstance(note, str) or len(note) > 3000 or '\x00' in note:
            raise ValueError('Keep the explanation under 3,000 characters.')
        if note.strip():
            group = groups[item['key']]
            result['intentions'][item['key']] = {'note': note.strip(), 'signature': group['signature'], 'title': group['title']}
        else:
            result['intentions'].pop(item['key'], None)
    if 'part_choice' in data:
        item = data['part_choice']
        if not isinstance(item, dict) or item.get('reference') not in project.components:
            raise ValueError('Choose a component from this run.')
        code = item.get('code', '')
        if not isinstance(code, str) or (code and not re.fullmatch(r'C[1-9][0-9]{1,11}', code)):
            raise ValueError('Enter an LCSC code such as C150772.')
        refs = item.get('references', [item['reference']])
        if not isinstance(refs,list) or not 1 <= len(refs) <= 200 or any(not isinstance(ref,str) or ref not in project.components for ref in refs):
            raise ValueError('Choose up to 200 components from this run.')
        for ref in refs:
            if code:
                result['part_choices'][ref] = {'code': code, 'signature': part_signature(project.components[ref])}
            else:
                result['part_choices'].pop(ref, None)
    if len(result['intentions']) > 500 or len(result['part_choices']) > 500:
        raise ValueError('This project has reached the 500-entry context limit.')
    return result


SUBSTANTIVE_CONTEXT_KEYS = ('product_context', 'description', 'operating_context',
                               'intentions', 'part_choices')


def _normalized_context(context):
    """Overlay empty defaults so a missing key and an empty value compare equal.

    Real store rows carry explicit empty dicts (e.g. product_context:{})
    while a fresh run snapshot built from empty_context() omits them.
    Without normalization, {} vs missing reads as a substantive change and
    blocks the refresh.
    """
    base = {**empty_context(), **(context or {})}
    for key in ('product_context', 'operating_context', 'intentions', 'part_choices',
                'product_questions'):
        if base.get(key) is None:
            base[key] = {}
    if base.get('description') is None:
        base['description'] = ''
    return base


def refresh_context_from_store(project, store):
    """Adopt the stored design context when the drift is non-substantive.

    Automatic question-text maintenance (answer-ID upgrades, retained
    question wording) bumps the store revision without any user input. A
    fresh report must not call itself stale for that. Genuine mid-run user
    answers keep the run snapshot so the stale banner stays honest.
    Returns True when the in-memory context was refreshed.
    """
    current = store.design_context(context_key(project))
    if not current:
        return False
    used = project.design_context or {}
    if current.get('revision', 0) == used.get('revision', 0):
        return False
    normalized_current = _normalized_context(current)
    normalized_used = _normalized_context(used)
    if any(normalized_current.get(key) != normalized_used.get(key)
           for key in SUBSTANTIVE_CONTEXT_KEYS):
        return False
    project.design_context = current
    return True


def initial_description(project):
    signals = []
    for ref, component in project.components.items():
        rows = getattr(component, 'schematic', None) or getattr(component, 'pcb', None) or []
        value = str(getattr(rows[0], 'value', '')).upper() if rows else ''
        for pattern, role in [(r'NRF52', 'a wireless microcontroller'), (r'MAX30001', 'an ECG/biopotential front end'),
                              (r'MAX3010', 'an optical pulse sensor'), (r'MAX3020', 'a temperature sensor'),
                              (r'LSM6', 'a motion sensor'), (r'BMP3', 'a pressure sensor'),
                              (r'MCP7383|TP4056', 'a lithium-cell charger'), (r'DRV2605', 'a haptic driver')]:
            if re.search(pattern, value):
                signals.append({'reference': ref, 'role': role, 'value': str(getattr(rows[0], 'value', ''))})
                break
    roles = [s['role'] for s in signals]
    if any('sensor' in r or 'front end' in r for r in roles) and any('wireless' in r for r in roles):
        description = 'This appears to be a wireless sensing device, possibly a wearable. '
    else:
        description = 'The intended use is not yet confirmed. '
    if signals:
        description += 'The selected components include ' + ', '.join(f"{s['role']} ({s['reference']})" for s in signals) + '.'
    else:
        description += 'Describe the power source, inputs, outputs and operating modes below.'
    return {'description': description, 'evidence_ids': ['C:' + s['reference'] for s in signals],
            'basis': 'Initial interpretation from component names', 'assumptions': ['Firmware behavior, actual rail voltages and use conditions are not established by part names.']}


def steps_for(group, data):
    if group.get('no_change_basis'):
        return []
    if group.get('steps'):
        return group['steps']
    if group.get('rule')=='max30102' and group.get('category')=='expected':
        return ['Leave the listed unused pads unconnected. Their current net assignments match the documented NC pins.',
                'No wiring change is needed for this item. Check the package drawing when choosing the physical part.']
    refs = ', '.join(group.get('components', [])) or 'the items listed in the evidence'
    title, rule = group['title'].lower(), group.get('rule')
    if (group.get('signal_path_id') or '').startswith('rf-'):
        path=next((p for p in data.get('signal_paths',{}).get('paths',[]) if p['id']==group['signal_path_id']),{})
        endpoint=(path.get('nets') or [{}])[-1].get('net','the end of the radio connection')
        return [f'Tell the app what connects to {endpoint}: a printed antenna, an antenna connector, or an antenna outside the board. The app already followed the saved radio connection.',
                'Add that detail in the context box and update the review. The app can then assess the intended antenna connection; this warning alone does not justify changing the matching parts.']
    if (group.get('signal_path_id') or '').startswith('ecg-'):
        return [f'Follow the two highlighted lines from the body-contact connectors to {refs}.',
                'Check whether one line passes near the radio, a power circuit, or many other tracks. The different via counts are a reason to look, not proof of noise.',
                'If one line seems exposed to interference, move it or add the input filter recommended for your use of this chip. If the layout is intentional, explain why below.',
                'Check the signal on a real board. The project file cannot tell us how noisy it will be.']
    if rule == 'mcp73831_dfn':
        first_ref = group.get('components', [group.get('reference', '')])[0]
        records = data.get('components', {}).get(first_ref, {}).get('pcb', [])
        ground = next((pad.get('net') for record in records for pad in record.get('pads', [])
                       if pad.get('number') == '6' and pad.get('net')), '')
        ground_label = ground or 'the same ground connection as pin 6'
        return [f'In the schematic, connect {refs}’s bottom metal pad (pad 9) to ground. Pin 6 already uses that ground connection ({ground_label}); pad 9 should use it too. If the symbol has no pin 9, add one first.',
                f'Open the PCB in KiCad and use “Update PCB from Schematic” to bring over that change. Click {refs}’s large center pad. Its connection should say {ground_label}, and ground copper should touch it.',
                'Run KiCad’s board check and this preflight again. Pad 9 should show the ground connection and this warning should disappear.']
    if 'via dimensions' in title:
        return ['Choose one of the highlighted vias — a small hole that connects copper layers — and look at its hole and outer size.',
                'Ask your board manufacturer whether those sizes are supported by the service you will order.',
                'If the manufacturer says they are too small, enlarge the vias. If they are supported, change the KiCad limits to match that service.',
                'Run the board check again to see whether the via warning is gone.']
    if 'spacing' in title or 'clearance' in title:
        return [f'Look at the two copper pieces KiCad points to near {refs}. They are closer than the limit saved in this project.',
                'Check the actual gap against your board manufacturer’s minimum spacing.',
                'If the gap is too small for that service, move the copper apart. If the service allows it, update the KiCad rule for this area.',
                'Run the board check again and make sure the change did not break another connection.']
    if 'solder-mask' in title:
        return [f'Look at the exposed metal pads around {refs}. KiCad says the protective coating leaves a shared opening.',
                'Ask the company assembling the board whether that opening is okay for this part.',
                'If they need separate openings, change the pad’s mask settings. If they approve the shared opening, record that decision.',
                'Export the board files again and check the openings before ordering.']
    if 'shorting' in title:
        # Confirmed joined copper on different nets: name the shorted items
        # concretely so the fault stays an actionable edit, not homework.
        joined = []
        for fid in group.get('issue_ids', []):
            if fid.startswith('F') and fid[1:].isdigit() and 0 < int(fid[1:]) <= len(data.get('issues', [])):
                for item in data['issues'][int(fid[1:]) - 1].get('items', []):
                    description = str(item.get('description', '')).strip()
                    if description and description not in joined:
                        joined.append(description)
        if len(joined) >= 2:
            return [f'Separate {joined[0]} from {joined[1]}: KiCad reports them as joined copper on different nets. '
                    'Move one piece of copper or cut the bridge in the PCB editor.',
                    'Run KiCad’s board check and this preflight again. The short warning should disappear without breaking another connection.']
    if 'pin-type' in title or 'pin-to-pin' in title or 'bidirectional' in title:
        return ['Run the AI review to compare these recorded connections with the matching datasheet passages.',
                'Wait for a pin-by-pin result before changing a wire. This ERC warning alone does not establish a wiring fault.']
    if group.get('category') == 'assembly':
        return [f'Find {refs} in the parts list or placement file and compare it with the PCB.',
                'Decide which part and location are correct.',
                'Fix the source project or export settings, then make fresh assembly files.',
                'Run preflight again and check that the mismatch is gone.']
    if group.get('category') == 'cosmetic':
        return ['Check whether this printed mark helps someone place a part the right way round.',
                'If it matters, move it so it stays readable. Otherwise, you can record that you are leaving it as-is.']
    if group.get('category') in ('expected', 'coverage'):
        return ['Read what this check could not confirm.', 'Add the missing file or information if you need this check before ordering.']
    return [f'Find {refs} on the board or schematic and open the original warning.',
            'Check the linked part information and decide whether the warning points to a real problem in your design.',
            'If you change something, run KiCad’s checks and this preflight again. If it is intentional, explain why below.']


def plain_reason(group):
    if group.get('rule') == 'mcp73831_dfn':
        return 'The charger chip has a metal pad underneath it. The chip maker says that pad should connect to ground. In this board file, pad 9 is not assigned to ground. The charger might still run, but this needs checking before you order the board.'
    if (group.get('signal_path_id') or '').startswith('rf-'):
        return 'The radio signal reaches a few small parts, but this check cannot see an antenna or antenna connector at the end. Check where the radio signal is meant to leave the board.'
    if (group.get('signal_path_id') or '').startswith('ecg-'):
        return 'The two ECG input lines take different routes through the board. That may be fine, but it is worth checking for interference near the sensitive inputs.'
    return group['why']


def local_plan(project, data=None):
    if data is None:
        data = asdict(project)
    actions = []
    for group in keyed_groups(data):
        intent = project.design_context.get('intentions', {}).get(group['key'], {})
        current_intent = intent.get('signature') == group['signature'] and bool(intent.get('note'))
        response = ''
        if current_intent:
            response = 'Your explanation is recorded. Confirm it against the requirement below; it has not been independently verified.'
        elif intent:
            response = 'An earlier explanation exists, but the finding evidence changed. Confirm the explanation again for this revision.'
        actions.append({'group_id': group['id'], 'group_key': group['key'], 'title': group['title'],
                        'disposition': 'intentional' if current_intent else 'no_change' if group.get('no_change_basis') else 'change' if group['priority'] == 'fix' else 'investigate',
                        'reason': plain_reason(group), 'steps': steps_for(group, data), 'context_response': response,
                        'evidence_ids': [group['id']], 'citations': group.get('citations', []),
                        'pin_checks': [],
                        'basis': 'AI schematic review' if group.get('basis') == 'AI schematic review' else 'Local check guidance',
                        'intent_note': intent.get('note', ''), 'intent_current': current_intent})
    device = initial_description(project)
    if project.design_context.get('description'):
        device = {**device, 'description': project.design_context['description'], 'basis': 'Your device description'}
    from .manufacturing import apply_manufacturing
    return apply_advice(apply_manufacturing(apply_footprint_context(add_consequences({'device': device, 'actions': actions,
                             'context_revision': project.design_context.get('revision', 0), 'status': 'local'}, data), data), data), data)


def compose_plan(project):
    add_schematic_suggestions(project)
    plan = local_plan(project)
    ai = project.ai_review
    if ai.get('status') != 'completed' or 'actions' not in ai.get('result', {}):
        return plan
    result = ai['result']
    plan['device'] = {'description': result['device_description'], 'basis': 'AI interpretation of your design and context',
                      'evidence_ids': result['device_evidence_ids'], 'assumptions': result['assumptions']}
    by_id = {a['group_id']: a for a in result['actions']}
    for action in plan['actions']:
        if action['group_id'] in by_id:
            action.update(by_id[action['group_id']])
            action['basis'] = 'AI guidance from supplied evidence'
    plan['status'] = 'ai'
    data = asdict(project)
    from .manufacturing import apply_manufacturing
    plan = apply_advice(apply_manufacturing(apply_footprint_context(add_consequences(plan, data), data), data), data)
    return annotate_question_pose(plan, data)


def add_schematic_suggestions(project):
    """Expose validated AI concerns in the normal plan, map, chat and revision workflow."""
    ai=project.ai_review
    if ai.get('status')!='completed' or 'circuit_findings' not in ai.get('result',{}):return
    from .datasheets import excerpts
    from .workflow import digest
    docs={d['evidence_id']:d for d in excerpts(project)}
    groups=project.review.setdefault('groups',[])
    for finding in ai['result']['circuit_findings']:
        ref=finding['reference']
        observations=finding['observations']
        rule='ai_schematic:'+digest([ref,finding['check_type'],sorted((o['reference'],o['pin']) for o in observations)])[:24]
        if any(g.get('rule')==rule for g in groups):continue
        sources=[{'title':f"Datasheet for {ref}, page {docs[c['evidence_id']]['page']}",
                  'url':docs[c['evidence_id']]['url']} for c in finding['citations'] if c['evidence_id'] in docs]
        observed='; '.join(f"{o['reference']} pin {o['pin']}: {o['net'] or 'no net'}" for o in observations)
        reason=finding['requirement']+' '+finding['reason']
        if finding['use_case']:reason+=' For your use: '+finding['use_case']
        if finding['missing_context']:reason+=' Still needed: '+'; '.join(finding['missing_context'])
        groups.append({'id':f'R{1+max((int(g["id"][1:]) for g in groups),default=0):03d}',
            'rule':rule,'title':finding['title'],'summary':observed,'why':reason,'next_step':finding['steps'][0],
            'steps':finding['steps'],'components':[ref], 'issue_ids':[],'raw_count':0,'item_count':len(observations),
            'category':'electrical','priority':'review','confidence':'AI interpretation',
            'basis':'AI schematic review','evidence':finding['evidence_ids'],'sources':sources,
            'check_type':finding['check_type'],'missing_context':finding['missing_context'],
            'chain_targets':[{'reference':o['reference'],'pin':o['pin']} for o in observations],
            'schematic_check':True,'citations':[dict(c, **{k:docs[c['evidence_id']].get(k) for k in ('url','page','mpn')}) for c in finding['citations'] if c['evidence_id'] in docs]})
    from collections import Counter
    project.review['categories']=dict(Counter(g['category'] for g in groups))
    project.review['review_count']=sum(g['priority']=='review' for g in groups)
    if 'datasheet_schematic_ai_review' not in project.checks_performed:
        project.checks_performed.append('datasheet_schematic_ai_review')


def annotate_question_pose(plan, data):
    """Per-question beginner/expert pose for every displayed follow-up
    question. The interview gate already suppresses implementation-framed
    needs for beginners, but finding articles render pin_check
    missing_context (and steps built from it) verbatim — so a disguised
    implementation question could still reach a beginner as article text.
    This records the creator's own selection per question; the article
    display hides beginner-suppressed ones instead of rewording them.
    Additive only: unannotated readers keep prior behavior, and review
    group signatures (which hash only summary/why/next_step/facts) are
    unaffected, so saved per-group decisions carry over. Runs on live
    context at compose time and on every serve (fresh answers can flip a
    pose), costs no model calls, and never poses anything itself.
    Group-level missing_context is annotated the same way for API
    consumers; the baked group.why fallback text is intentionally left
    untouched so saved decision signatures stay valid."""
    from .ai import question_creator as qc
    data = data or {}
    context = data.get('design_context') or {}
    index = qc.build_evidence_index(data, context)
    trackers = {'ask_beginner': qc.DedupTracker(), 'ask_expert': qc.DedupTracker()}
    def pose(need, scope):
        out = {}
        for key, hint in (('ask_beginner', 'beginner_interview'), ('ask_expert', None)):
            decision = qc.decide(need, origin_agent='pin_check', scope=scope,
                                 index=index, tracker=trackers[key], context_hint=hint)
            out[key] = bool(decision.get('ask_user'))
            if key == 'ask_beginner':
                out['beginner_decision'] = decision.get('decision')
        return out
    def annotate(checks, ref_of):
        for check in checks or []:
            if not isinstance(check, dict):
                continue
            questions = [q for q in (check.get('missing_context') or [])
                         if isinstance(q, str) and q.strip()]
            if not questions:
                continue
            refs = [r for r in dict.fromkeys([ref_of(check)]) if r]
            scope = {'components': refs, 'nets': []}
            check['missing_context_pose'] = {q: pose(q, scope) for q in dict.fromkeys(questions)}
    for action in (plan.get('actions') or []):
        annotate(action.get('pin_checks'), lambda check: check.get('reference'))
    audit = ((data.get('ai_review') or {}).get('result') or {}).get('circuit_audit') or {}
    for part in (audit.get('parts') or []):
        annotate((part.get('checks') or []),
                 lambda check, ref=part.get('reference'): ref)
    for group in ((data.get('review') or {}).get('groups') or []):
        if not isinstance(group, dict):
            continue
        questions = [q for q in (group.get('missing_context') or [])
                     if isinstance(q, str) and q.strip()]
        if not questions:
            continue
        refs = [r for r in dict.fromkeys(group.get('components') or []) if r]
        scope = {'components': refs, 'nets': []}
        group['missing_context_pose'] = {q: pose(q, scope) for q in dict.fromkeys(questions)}
    return plan


def current_plan(project, data=None):
    """Refresh local guidance in saved runs without rewriting historical reports."""
    if data is None:
        data = asdict(project)
    fresh_plan = local_plan(project, data)
    plan = deepcopy(project.action_plan) if project.action_plan else fresh_plan
    local = {a['group_id']: a for a in fresh_plan['actions']}
    for action in plan.get('actions', []):
        fresh = local.get(action['group_id'])
        if fresh and action.get('basis') == 'Published manufacturer limits':
            replacement = deepcopy(fresh)
            action.clear()
            action.update(replacement)
        if fresh and action.get('basis') == 'Local check guidance':
            action['steps'] = fresh['steps']
    from .manufacturing import apply_manufacturing
    plan = apply_advice(apply_manufacturing(apply_footprint_context(add_consequences(plan, data), data), data), data)
    # Serve-time recompute: saved answers and experience may have changed
    # since compose, so pose is refreshed on the live context here (and on
    # the stored plan at compose time). plan is a deepcopy; data is the
    # serve-local snapshot.
    return annotate_question_pose(plan, data)

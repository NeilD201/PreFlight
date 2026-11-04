"""Turn explicit chat choices into project context; recheck before resolving findings."""
from copy import deepcopy
from dataclasses import asdict
import re

from .datasheets import part_signature
from .design_review import context_key, edit_context
from .workflow import digest, keyed_groups

INSTRUCTIONS = '''The chat_workflow contains saved project choices and pending decisions.
You can propose a part choice or an answer to one of its listed questions using proposals.
Each proposal has kind (part or answer), target (reference or question ID), and value.
Only propose a specific choice when you recommend it and want the user to accept it.
For part choices use an exact part number in allowed_parts; do not change a footprint name.
For answers use a listed question ID. State one short choice and why; do not invent intent.
Do not propose a choice already recorded. A confirmed part choice is NOT confirmation of
firmware behavior, physical wiring, package fit or electrical safety. Keep these separate.
The app saves explicit acceptance of a previously offered choice and rechecks affected issues.
Do not claim a choice was saved, an issue resolved or a file edited in your answer; the app
will append the actual operation result. Explain any remaining user action clearly. A question
about a choice, hypothetical statement or uncertain reply is not acceptance. Return answer
and proposals (an empty list when none). Never request that the user inspect supplied files.
'''


def allowed_parts(project):
    from .design_identity import design_identity
    result={}
    for ref in project.components:
        identity=design_identity(project,ref)
        values=[d['value'] for d in identity['declarations'] if d['field']!='Value']
        if not values:values=[identity['declared_mpn']] if identity['declared_mpn'] else []
        result[ref]=list(dict.fromkeys(values))[:8]
    return result


def questions(project):
    from .product_context import project_questions
    context=project.design_context
    entries=project_questions(project,context.get('product_context',{}),context=context)['all_questions']
    result={q['id']:{'question':q['question'],'kind':'product'} for q in entries}
    review=project.ai_review.get('result',{})
    followups=list(review.get('questions',[]))
    for action in review.get('actions',[]):
        for check in action.get('pin_checks',[]):
            followups.extend(check.get('missing_context',[]))
    followups=list(dict.fromkeys(followups))
    for question in followups:
        if isinstance(question,str):
            result['followup:'+digest(question)[:20]]={'question':question,'kind':'followup'}
    return result


def packet(project):
    workflow=project.provenance.get('chat_workflow',{})
    return {'allowed_parts':allowed_parts(project),'questions':questions(project),
            'pending_choices':workflow.get('pending_choices',[]),
            'saved_part_choices':project.design_context.get('component_intent',{}),
            'confirmed_this_message':workflow.get('confirmed_this_message',[])}


def validated_proposals(project, values):
    if not isinstance(values,list) or len(values)>3:return []
    parts=allowed_parts(project); qs=questions(project); proposals=[]
    for p in values:
        if not isinstance(p,dict) or set(p)!={'kind','target','value'}:continue
        kind,target,value=p['kind'],p['target'],p['value']
        if any(not isinstance(v,str) for v in (kind,target,value)) or not 1<=len(value)<=1200 or '\x00' in value:continue
        if kind=='part':
            if value not in parts.get(target,[]):continue
            saved=project.design_context.get('component_intent',{}).get(target,{})
            if saved.get('value')==value and saved.get('signature')==part_signature(project.components[target]):continue
            label=f'Use {value} for {target}'
        elif kind=='answer':
            if target not in qs:continue
            if project.design_context.get('product_context',{}).get(target)==value:continue
            if any(a.get('question')==qs[target]['question'] and a.get('answer')==value for a in project.design_context.get('question_answers',{}).values()):continue
            label=qs[target]['question']+' — '+value
        else:continue
        proposals.append({**p,'id':digest(p)[:24],'label':label})
    return proposals


def pending_choices(store, project, channel, report_id):
    rows=store.conversations(context_key(project),channel,limit=1)
    if not rows or rows[0]['report_id']!=report_id or rows[0]['status']!='ai':return []
    meta=rows[0].get('metadata',{})
    if 'decision_basis' not in meta:
        # Older and web-enabled replies have prose only. Recognize a single
        # exact recommended CAD part, never a file edit or electrical verdict.
        answer=rows[0].get('answer','');candidates=[]
        for ref,values in allowed_parts(project).items():
            if not re.search(r'\b'+re.escape(ref)+r'\b',answer):continue
            for value in values:
                if re.search(r'\b(?:keep|recommend|use|choose)\s+[*`]*'+re.escape(value)+r'(?=[*`\s.,;]|$)',answer,re.I):
                    candidates.append({'kind':'part','target':ref,'value':value})
        return validated_proposals(project,candidates) if len(candidates)==1 else []
    if meta.get('decision_basis')!={'sources':project.provenance.get('source_hashes',{}),
                                  'context_revision':project.design_context.get('revision',0)}:return []
    return validated_proposals(project,[{k:p[k] for k in ('kind','target','value')} for p in meta.get('proposals',[]) if isinstance(p,dict) and all(k in p for k in ('kind','target','value'))])


def accepted_choices(question, pending):
    text=question.strip().lower().rstrip('.!')
    explicit=[p for p in pending if text==('Confirm: '+p['label']).lower().rstrip('.!')]
    if explicit:return explicit
    # Generic assent is meaningful only with one current proposal, from the
    # server's saved reply in this conversation. Never act on browser history.
    if len(pending)==1 and re.fullmatch(r'(?:yes|yep|yeah|ok|okay|sure)(?:[, ]+(?:then )?(?:use|choose|keep|do|go with) (?:that|it|this))?(?: please)?',text):
        return pending
    if '?' in text or re.search(r'\b(not|no|dont|don.t|maybe|if|unsure|could|should)\b',text):return []
    return [p for p in pending if text==p['label'].lower() or
            (p['kind']=='part' and re.fullmatch(r'(?:use|choose|keep|select)\s+'+re.escape(p['value'].lower())+r'(?:\s+for\s+'+re.escape(p['target'].lower())+r')?(?: please)?',text))]


def prepare(store, project, channel, report_id, question):
    pending=pending_choices(store,project,channel,report_id)
    project.provenance['chat_workflow']={'pending_choices':pending,
        'confirmed_this_message':accepted_choices(question,pending)}


def finish(state, project, channel, report_id, question, result, key, model, source):
    """Save accepted intent once, then spend at most one remaining review request."""
    workflow=project.provenance.get('chat_workflow',{})
    confirmed=workflow.get('confirmed_this_message',[])
    result=dict(result)
    raw=result.pop('proposed_decisions',[])
    result['proposals']=validated_proposals(project,raw) if result.get('status')=='ai' else []
    result['decision_basis']={'sources':project.provenance.get('source_hashes',{}),
                              'context_revision':project.design_context.get('revision',0)}
    if not confirmed:return result
    pk=context_key(project)
    with state.lock:
        current=state.store.design_context(pk) or project.design_context
        if (state.report_id!=report_id or current.get('revision',0)!=project.design_context.get('revision',0)):
            result['decision_update']={'status':'not_saved','message':'The project changed while you were chatting. Your choice was not applied; send it again from the current review.'}
            return result
        from .storage import timestamp
        saved_at=timestamp()
        updated=deepcopy(current)
        for choice in confirmed:
            if choice['kind']=='part':
                ref=choice['target']
                updated.setdefault('component_intent',{})[ref]={'value':choice['value'],
                    'signature':part_signature(project.components[ref]),'source':'User confirmed in chat',
                    'question':question,'report_id':report_id,'updated':saved_at}
            else:
                info=questions(project)[choice['target']]
                if info['kind']=='product':
                    updated=edit_context(project,updated,{'product_context':{choice['target']:choice['value']}})
                else:
                    updated.setdefault('question_answers',{})[digest(info['question'].casefold().strip())]={
                        'question':info['question'],'answer':choice['value'],'source':'User confirmed in chat','report_id':report_id,'updated':saved_at}
        saved=state.store.save_context(pk,updated,current.get('revision',0))
        state.store.preference('chat_handled:'+pk,{'report_id':report_id,'context_revision':saved['revision']})
        state.revision+=1
    project.design_context=saved
    result['proposals']=validated_proposals(project,raw)
    result['decision_basis']['context_revision']=saved['revision']
    message='Saved: '+ '; '.join(c['label'] for c in confirmed)+'.'
    result['decision_update']={'status':'saved','message':message,'choices':confirmed,'files_changed':False,
        'review_status':'pending','next_action':'Rechecking the affected findings.'}
    # Stale files may be useful for conversation, never for closing current issues.
    from .chat_safety import evidence_state
    freshness=evidence_state(project,source,saved['revision'])
    if freshness['status'] not in ('current','snapshot'):
        result['decision_update'].update(review_status='needs_update',next_action='Save in your editor, then click Update review so Vector can check the current files.')
        return result
    if result.get('status')!='ai' or not key:
        result['decision_update'].update(review_status='incomplete',next_action='Your choice is saved. Vector could not recheck the connections; retry the review when the connection is available.')
        return result
    refs={c['target'] for c in confirmed if c['kind']=='part'}
    groups=[g for g in project.review.get('groups',[]) if not refs or refs & set(g.get('components',[]))]
    if not groups:
        result['decision_update'].update(review_status='not_needed',next_action='No finding is linked to this choice. No file edit was made.')
        return result
    from .ai.reviewer import AIError, run_ai_review
    from .design_review import compose_plan
    view=deepcopy(project);view.review['groups']=groups
    try:
        view.ai_review=run_ai_review(view,key=key,model=model,design=True,max_attempts=1,timeout=60)
    except AIError as exc:
        # A failed recheck must not fail the chat answer or lose the saved
        # choice: the finding stays open with the reason visible.
        result['decision_update'].update(review_status='incomplete',next_action='Your choice is saved. Vector could not recheck the connections right now; retry the review when the connection is available.',
            detail=str(exc))
        return result
    accepted=view.ai_review.get('result',{}).get('actions',[]) if view.ai_review.get('status')=='completed' else []
    if not accepted:
        result['decision_update'].update(review_status='incomplete',next_action='Your choice is saved. The remaining findings stay open because Vector could not finish their evidence checks.',
            detail=view.ai_review.get('message',''))
        return result
    ids={a['group_id'] for a in accepted}
    plan=compose_plan(view)
    actions=[a for a in plan['actions'] if a['group_id'] in ids]
    record={'context_revision':saved['revision'],'sources':project.provenance.get('source_hashes',{}),
            'actions':actions,'signatures':{g['id']:g['signature'] for g in keyed_groups(asdict(project)) if g['id'] in ids}}
    with state.lock:
        latest=state.store.design_context(pk)
        final_freshness=evidence_state(project,source,saved['revision'])
        if state.report_id!=report_id or latest['revision']!=saved['revision'] or final_freshness['status'] not in ('current','snapshot'):
            result['decision_update'].update(review_status='needs_update',next_action='The files or context changed during the check. Update review; your choice is saved.')
            return result
        state.store.preference('chat_review:'+report_id,record)
        state.revision+=1
    resolved=[a['group_id'] for a in actions if a.get('disposition')=='no_change']
    remaining=[a for a in actions if a.get('disposition')!='no_change']
    next_steps=[step for a in remaining for step in a.get('steps',[])][:3]
    if remaining:
        next_action='Still needed: '+(' '.join(next_steps) if next_steps else 'Vector still needs evidence for the remaining connection checks; no wiring change is confirmed.')
    elif len(ids)<len(groups):next_action='Some findings still need analysis; they remain open.'
    else:next_action='No change is needed for the rechecked findings. They are now in reviewed items.'
    result['decision_update'].update(review_status='completed',resolved=resolved,next_action=next_action)
    return result


def apply_review(plan, record, project, context):
    if not record or record.get('context_revision')!=context.get('revision') or record.get('sources')!=project.provenance.get('source_hashes',{}):return plan
    signatures={g['id']:g['signature'] for g in keyed_groups(asdict(project))}
    replacements={a['group_id']:a for a in record.get('actions',[]) if signatures.get(a['group_id'])==record.get('signatures',{}).get(a['group_id'])}
    plan=deepcopy(plan)
    plan['actions']=[deepcopy(replacements.get(a['group_id'],a)) for a in plan.get('actions',[])]
    return plan

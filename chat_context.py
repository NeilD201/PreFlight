"""Local context selection for chat; never rewrites saved facts or pays for a summary.

The complete conversation stays in SQLite. Requests carry current intent, recent
messages and selected evidence as complete records, with explicit coverage limits.
"""
from copy import deepcopy
import json
import re


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def size(value):
    return len(encode(value).encode('utf-8'))


def compact_product(value):
    """Remove repeated copies of answers, keeping their question and provenance."""
    value=deepcopy(value or {})
    questions={q['id']:q.get('question',q['id']) for q in
               value.get('questions',[])+value.get('answered_questions',[]) if q.get('id')}
    answers=[{'id':key,'question':questions.get(key,key),'answer':answer}
             for key,answer in value.get('answers',{}).items()]
    # Incident 80ab5aec: the bounded output-limit retry feeds an already
    # compacted packet back through here, where previous_revision_answers is
    # a list of {question,answer,status} records instead of a dict. Accept
    # the compacted form (and coerce anything else) instead of raising
    # AttributeError on .items().
    previous_questions=value.get('previous_revision_questions',{})
    if not isinstance(previous_questions,dict):previous_questions={}
    previous=value.get('previous_revision_answers',{})
    if isinstance(previous,dict):
        previous_items=list(previous.items())
    elif isinstance(previous,list):
        previous_items=[((item.get('id') or item.get('question','')) if isinstance(item,dict) else item,
                         item.get('answer','') if isinstance(item,dict) else item)
                        for item in previous]
    else:
        previous_items=[]
    return {k:v for k,v in {
        'source':value.get('source'), 'saved_answers':answers,
        'explanation_profile':value.get('explanation_profile'),
        'unknown':value.get('unknown',[]),
        'confirmed_part_choices':value.get('confirmed_part_choices',{}),
        'saved_followup_answers':value.get('saved_followup_answers',[]),
        'saved_conversations':value.get('saved_conversations',[]),
        'previous_revision_answers':[{'question':previous_questions.get(k,k),
            'answer':v,'status':'Historical; reconfirm for changed parts'}
            for k,v in previous_items],
        'check_scope':value.get('check_scope'),
        'engineering_calculations':value.get('engineering_calculations'),
        'routing_geometry':value.get('routing_geometry'),
    }.items() if v not in (None,[],{})}


def compact_chat(packet, byte_limit=48000):
    """Fit complete evidence records into a bounded, valid JSON request.

    Oversized records are explicitly omitted, never cut in the middle of a quote,
    pin record, requirement or user declaration. The latest question is preserved.
    """
    original=deepcopy(packet)
    if size(original)<=byte_limit:
        return original, {'compacted':False,'original_bytes':size(original),'sent_bytes':size(original)}
    result={'question':original.get('question','')}
    limits=['Context was selected locally from saved data. Omitted records are unknown, not absent from the design. '
            'Do not infer safety or a fault from omitted evidence. Ask for the exact missing detail if it matters. '
            'Earlier conversation excerpts are user/model statements, not verified file facts.']
    # Reserve room for the manifest; report counts, not unbounded paths or content.
    budget=max(byte_limit-1800,size(result))
    omitted=[]
    tokens=set(re.findall(r'\b[a-z]+\d+\b',str(original.get('question','')).lower()))
    finding=original.get('finding') or original.get('related_evidence',{}).get('finding') or {}
    tokens.update(re.findall(r'\b[a-z]+\d+\b',str(finding).lower()))

    def rank(item):
        text=encode(item).lower()
        return -sum(bool(re.search(r'\b'+re.escape(token)+r'\b',text)) for token in tokens)

    def add(target,key,value,cap=None,path=''):
        if value is None:
            target[key]=None;return
        cap=min(cap or budget,budget-size(result)-len(key.encode())-8)
        if cap>0 and size(value)<=cap:
            target[key]=deepcopy(value);return
        # Lists are evidence records. Keep records intact, preserve chronological
        # order for conversations, and prefer named parts for other collections.
        if isinstance(value,list):
            target[key]=[];used=2;selected=[]
            entries=list(enumerate(value))
            if key in ('recent_chat','saved_conversations'):
                entries.reverse()
            else:
                entries.sort(key=lambda pair:rank(pair[1]))
            for index,item in entries:
                n=size(item)+1
                if used+n<=cap:
                    selected.append((index,deepcopy(item)));used+=n
            target[key]=[v for _,v in sorted(selected)]
            if len(selected)<len(value):omitted.append((path+key,len(value)-len(selected)))
        else:
            omitted.append((path+key,1))

    if 'chat_workflow' in original:add(result,'chat_workflow',original['chat_workflow'],10000)
    for key in ('selected_step_number','selected_step','all_steps','finding','file_edit_capability',
                'intent_note','operating_context','user_device_description','review_interpretation'):
        if key in original:add(result,key,original[key],6000)
    history=original.get('recent_chat',[])
    add(result,'recent_chat',history[-2:],8500)
    if len(history)>2:add(result,'earlier_messages',history[:-2],3500)

    context_key='project_context' if 'project_context' in original else 'project'
    context=original.get(context_key)
    if isinstance(context,dict):
        out=result[context_key]={}
        for key in ('evidence_state','name','source_revision','context_revision','description','device_description',
                    'operating_context','current_issue_intent','manufacturer','manufacturer_selection',
                    'part_choices','review_status','component_count','board_stackup','selected_files'):
            if key in context:add(out,key,context[key],4000,context_key+'.')
        product=compact_product(context.get('product_context',{}))
        po=out['product_context']={}
        for key in ('explanation_profile','source','saved_answers','unknown','confirmed_part_choices','saved_followup_answers','previous_revision_answers'):
            if key in product:add(po,key,product[key],6000,'product_context.')
    else:
        product={};out={}
        if context_key in original:result[context_key]=None

    if 'memory_review' in original:
        memory=original['memory_review']; result['memory_review']={}
        for key in ('status','limits','requirements','note','devices','observations'):
            if key in memory:add(result['memory_review'],key,memory[key],6000,'memory_review.')
    if 'project_search' in original:
        search=original['project_search']
        result['project_search']={k:search[k] for k in ('scope','query','matching_records','limited')}
        add(result['project_search'],'results',search['results'],13000,'project_search.')
    if 'connection_neighborhood' in original:
        neighborhood=original['connection_neighborhood'];out_neighborhood=result['connection_neighborhood']={}
        for key in ('references','basis','boundary','connectivity_status','omitted_nets','omitted_passive_connections'):
            if key in neighborhood:add(out_neighborhood,key,neighborhood[key],1500,'connection_neighborhood.')
        for key in ('nets','passive_connections'):
            if key in neighborhood:add(out_neighborhood,key,neighborhood[key],7500,'connection_neighborhood.')
    detail=original.get('related_evidence') or original
    dest=result.setdefault('related_evidence',{}) if 'related_evidence' in original else result
    # Prioritize actual pin decisions and citations over repeated board-wide data.
    for key,cap in [('pin_checks',6000),('datasheet_excerpts',9500),('components',16000),
                    ('manufacturer_check',8000),('footprint_context',4000),('finding',4000),
                    ('file_edit_capability',4000),('routing_chains',16000),
                    ('relevant_signal_paths',5000),('erc_findings',4500)]:
        if key in detail and key not in dest:add(dest,key,detail[key],cap)
    if context:
        for key in ('connected_net_measurements','checks_run'):
            if key in context:add(out,key,context[key],4000,context_key+'.')
        for key in ('saved_conversations','check_scope','engineering_calculations','routing_geometry'):
            if key in product:add(out['product_context'],key,product[key],3500,'product_context.')
        if 'board_measurements' in context:
            omitted.append(('project_context.board_measurements',1))
    for key in ('issues','components'):
        if key in original and key not in result:add(result,key,original[key],5000)
    if omitted:
        limits.append('Records not included: '+', '.join(f'{k} ({n})' for k,n in omitted)+'. Full records remain saved locally.')
    result['context_limits']=limits
    return result, {'compacted':True,'original_bytes':size(original),'sent_bytes':size(result),
                    'omitted_records':sum(n for _,n in omitted)}

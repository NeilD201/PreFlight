"""Product-level questions and stable user preferences, independent of PCB hashes."""
import re

PROFILE = [
    ('experience', 'Tell us a little about yourself and your electronics experience.', 'What have you built before, and what are you still learning? For example: “I can solder and use KiCad, but this is my first PCB” or “I design electronics professionally.”', []),
    ('purpose', 'What should the finished product do?', 'Describe the main job it should do for its user.', []),
    ('audience', 'Who will use it?', 'For example: just you, consumers, children, or trained staff.', []),
    ('charger', 'How will the product be powered or charged?', 'Name the charger, USB supply or battery if you know it. Say whether it should work while charging.', []),
    ('enclosure', 'What will the finished product’s case be like?', 'Describe its material, size, openings and whether it touches the body. “No case” is fine.', []),
    ('environment', 'Where will people use it?', 'For example: indoors, outdoors, near water, in a hot car, or on the body.', []),
    ('connections', 'What will people plug into or connect to it?', 'Describe external cables, accessories and anything connected away from the main board.', []),
    ('behavior', 'What should happen during normal use?', 'Describe what runs at the same time, how quickly it should respond, and how long it should run.', []),
]


def experienced(context):
    from .explanation_profile import experience_level
    return experience_level(context.get('product_context', {}).get('experience', '')) == 'technical'


def topics(question):
    text=question.casefold()
    if re.search(r'\b(?:maximum|minimum|measured|measurement|changed|contradict|checked|fits?|approved|clearance)\b',text):return set()
    matches=set()
    case_text=re.sub(r'\b(?:worst[ -]case|operating case|test case|in case)\b','',text)
    if re.search(r'\b(?:enclosure|housing|casing|case)\b|\bnearby materials\b',case_text):matches.add('enclosure')
    if re.search(r'\brange\b|\bwireless (?:use|connection)\b|\bradio use\b',text):matches.add('radio')
    # Bridge review answers into the matching profile fact so the designer is
    # not asked twice. Patterns require the question to SEEK the fact; a mere
    # mention (e.g. "the charger" inside a safety question) must not fill the
    # profile with an off-topic answer. Experience, audience and behavior stay
    # with the profile form, which owns them.
    if re.search(r'\bmeant to do\b|\bintended use\b|\bwhat is this product\b',text):matches.add('purpose')
    if re.search(r'\bwhile charging\b|\bcan (?:it|the product)\b.{0,20}\bcharg|\bhow will\b.{0,30}\bcharg|\bwhat\b.{0,15}\bcharg',text):matches.add('charger')
    if re.search(r'\boutdoors?\b|\bcondensation\b|\bnear water\b|\bindoors\b|\bhot car\b',text):matches.add('environment')
    if re.search(r'\bwhat\b.{0,30}\b(?:connects?|plug|cable)\b|\bwhich\b.{0,20}\b(?:connects?|cable)\b',text):matches.add('connections')
    return matches


def remembered_answers(context):
    """Reuse explicitly saved answers with their original questions; never AI guesses."""
    answers=dict(context.get('product_context', {}))
    records=[{'question':q,'answer':answers.get(key),'updated':''}
             for key,q in context.get('product_questions', {}).items() if key not in dict((p[0],p[1]) for p in PROFILE)]
    records+=list(context.get('question_answers', {}).values())
    for entry in sorted(records,key=lambda e:e.get('updated','')):
        if not entry.get('answer'):continue
        question=entry.get('question','')
        # A statement about a case is different from confirming physical fit.
        if re.search(r'\b(?:checked|fits?|preview|clearance|approved)\b',question,re.I):continue
        for topic in topics(question):
            if topic not in context.get('product_context',{}):answers[topic]=entry['answer']
    return answers


def profile_questions(context):
    answers=remembered_answers(context)
    result=[]
    for key,question,hint,choices in PROFILE:
        result.append({'id':key,'reference':'Your product','question':question,'hint':hint,'choices':choices,
            'known':'Your saved answers are reused across PCB updates. Edit them when the product changes.',
            'review_focus':hint})
    return answers,result


# Intended product behavior (firmware UX) never gates the PCB review, no
# matter which component it is scoped to. Operating conditions (battery,
# radio, loads, connector, mode, environment, body) still gate the checks
# they affect.
_INTENT_PREFIXES = ('behavior:', 'power:board:')


def blocks_review(question_id, profile_ids):
    """Decide whether an unanswered question may hold back analysis."""
    if question_id in profile_ids:
        return False
    return not question_id.startswith(_INTENT_PREFIXES)


def check_flow_questions(result):
    """PCB-check interview: hide intended-behavior questions entirely.

    Firmware UX (LED patterns, key maps, knob functions, startup behavior)
    belongs to firmware planning, not the electrical check. Saved answers
    stay in product context; only the presented question list changes.
    """
    kept = [q for q in result['questions'] if not q['id'].startswith(_INTENT_PREFIXES)]
    kept_ids = {q['id'] for q in kept}
    return {**result, 'questions': kept,
            'unanswered': [qid for qid in result['unanswered'] if qid in kept_ids]}


def guided_questions(project, value=None, context=None):
    from .product_context import project_questions
    context=dict(context if context is not None else project.design_context)
    if value is not None:context['product_context']=value
    answers,profile=profile_questions(context)
    base=project_questions(project,answers,context=context)
    answers={**base['answers'],**answers}
    expert=experienced({'product_context':answers})
    if expert:
        extra=base['all_questions']
    else:
        # Keep direct, plain questions about body contact, radio reach and
        # intended product behavior. Technical pin, timing and load-limit
        # questions belong to the analysis.
        extra=[q for q in base['all_questions'] if q['id'].startswith(('body:','behavior:')) or q['id'].startswith('power:')]
        radio=next((q for q in base['all_questions'] if q['id'].startswith('radio:')),None)
        if radio:extra.append({'id':'radio','reference':radio['reference'],'question':'What should the wireless connection on %s do, and how far should it reach?' % radio['reference'],
            'evidence':radio.get('evidence',[]),'known':radio['known'],'hint':'Describe what it connects to and the distance you need. The app will use your saved case description.',
            'review_focus':radio['review_focus']})
    # Existing answers about enclosure/radio use are retained even when an
    # unrelated component edit changes the old component-specific question ID.
    extra=[q for q in extra if not (topics(q['question']) and all(answers.get(t) for t in topics(q['question'])))]
    all_questions=profile+extra
    # The generic product checklist never becomes a pending queue: PROFILE
    # answers stay saved, editable, and reused, but unanswered profile items
    # are not asked by default. Only the experience question (which sets
    # explanation depth) and hardware-driven component questions become
    # pending. Zero pending questions is a valid result.
    shown=extra if answers.get('experience') else profile[:1]
    pending=[q for q in shown if not answers.get(q['id'])]
    done=[q for q in shown if answers.get(q['id'])]
    # Stable profile preferences (experience level, product description, case,
    # environment…) and intended product behavior (LED patterns, key maps,
    # knob functions, startup UX) are offered in the UI but never block
    # analysis: only hardware operating conditions about the actual circuit
    # gate the review. Explanation depth simply defaults to plain language
    # until the user answers the experience question.
    profile_ids={key for key,_,_,_ in PROFILE}
    return {**base,'answers':answers,'questions':pending+done,'all_questions':all_questions,
        'active_question_ids':[q['id'] for q in all_questions],
        'answered_questions':[dict(q,answer=answers[q['id']]) for q in all_questions if answers.get(q['id'])],
        'unanswered':[q['id'] for q in pending],
        'blocking_unanswered':[q['id'] for q in pending if blocks_review(q['id'],profile_ids)],
        'deferred_count':0}

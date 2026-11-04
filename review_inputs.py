"""Missing review inputs presented directly, with revision-bound saved answers."""
import hashlib
import json
import re
from .ai.origin import RECONCILE_PREFIX
from .product_context import unanswered_followups
from .user_profile import experienced
from .ai import question_creator as qc_creator


PHYSICAL = {
    'mechanical': ('Have you checked that the board fits its case and that the connectors are accessible?',
                   'Describe the case or drawing you checked, and any clearance problems.'),
    'assembly_geometry': ('Have you checked component orientation and solder openings in the assembly preview?',
                          'Describe which preview you checked and any problems with pin 1, polarity or soldering.'),
    'bringup': ('What will you test when the first assembled board arrives?',
                'Describe the power-on test and what results would count as working. If no board exists yet, say so.'),
    'order_preview': ('Have you checked the manufacturer’s preview for this board revision?',
                      'Describe the checked layers, holes and component orientation, or say that you have not ordered yet.'),
}

CALCULATION_QUESTIONS = {
    'interfaces': ('How fast will the connected chips communicate?', 'Enter the limits for each communication line, using its connected parts’ specifications.'),
    'power': ('How much current must each power supply provide?', 'Enter the available supply current and the largest combined load.'),
    'thermal': ('How hot could the board get during use?', 'Enter the worst-case temperature and component heating values for the intended case and cooling.'),
    'transient': ('How much does the load change when parts switch on?', 'Enter the load changes and allowed supply drop for each power line.'),
    'esd_margin': ('What electrical pulses must the exposed connections withstand?', 'Enter the required pulse and protection limits for the exposed connections.'),
    'trace_load': ('How much current will these tracks carry?', 'Enter the current and allowed voltage loss for each relevant connection.'),
}


def review_inputs(data):
    context = data.get('design_context', {})
    expert=experienced(context)
    # The generic product checklist never gates a review: this module reports
    # no profile block of its own. The running app overwrites
    # needs_product_profile from hardware-driven guided_questions unanswered
    # (webapp.py), so missing optional profile answers cannot block anyone.
    saved = list(context.get('question_answers', {}).values())
    source = data.get('provenance', {}).get('source_hashes', {})
    basis = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()[:20]
    tasks = []
    def add(task):
        task['signature'] = task.get('signature') or basis
        task['id'] = task.get('id') or hashlib.sha256(task['question'].casefold().encode()).hexdigest()[:20]
        answered = any(a.get('review_task') == {'id': task['id'], 'signature': task['signature']}
                       and a.get('answer') for a in saved)
        if not answered: tasks.append(task)
    # Candidate interview questions, each tagged with its originating agent so
    # the Question Creator can route answers back and enforce per-agent
    # uncertainty handling. Nothing here asks the user directly; every
    # candidate below passes through the creator gate before becoming a task.
    candidates = [(q, 'ai_review') for q in
                  data.get('saved_review_questions', data.get('ai_review', {}).get('result', {}).get('questions', []))]
    # A missing product fact must reach the interview even if the model placed
    # its question only inside a pin conclusion rather than the top-level list.
    if data.get('ai_review', {}).get('status') == 'completed':
        candidates.extend((question, 'pin_check') for action in data.get('action_plan', {}).get('actions', [])
                          for check in action.get('pin_checks', []) if check.get('decision') == 'needs_context'
                          for question in check.get('missing_context', []))
    # Designer-directed ledger questions: ORIGIN intent gaps plus findings that
    # may contradict confirmed intentions. Technical open questions stay in the
    # replay; only these reach the interview, through the same filters below.
    pipeline = data.get('ai_review', {}).get('result', {}).get('pipeline', {}) or {}
    model = pipeline.get('product_model', {}) or {}
    for asked in model.get('intent_questions', []) or []:
        if isinstance(asked, dict) and isinstance(asked.get('question'), str):
            candidates.append((asked['question'], 'origin'))
    for claim in (pipeline.get('ledger', {}) or {}).get('claims', []):
        for asked in claim.get('open_questions', []) or []:
            if isinstance(asked, str) and asked.startswith(RECONCILE_PREFIX):
                candidates.append((asked, 'origin_reconcile'))
    seen_texts = set()
    deduped = []
    for text, agent in candidates:
        if not isinstance(text, str):
            continue
        key = text.strip()
        if '?' not in key or key in seen_texts:
            continue
        seen_texts.add(key)
        deduped.append((key, agent))
    index = qc_creator.build_evidence_index(data, context)
    tracker = qc_creator.DedupTracker()
    question_decisions = []
    followup_delegations = []
    def gate(question, origin_agent, context_hint=None):
        """Central uncertainty fallback: resolve from evidence, delegate to a
        specialist, ask the user, or preserve unknown. Technical needs never
        become direct user questions; the returned flag decides task entry.
        Beginners get product-behavior questions only: implementation-framed
        needs are preserved for internal investigation, never posed."""
        if context_hint is None and not expert:
            context_hint = 'beginner_interview'
        decision = qc_creator.decide(question, origin_agent=origin_agent, index=index, tracker=tracker,
                                     context_hint=context_hint)
        question_decisions.append(decision)
        return decision['ask_user'], decision
    pending_texts = [text for text, _ in deduped]
    for question, origin_agent in deduped:
        if question not in unanswered_followups(pending_texts, context):
            continue
        if re.search(r'\b(read|inspect|look up|consult|check|verify)\b.{0,60}\b(datasheet|pin table|pinout)\b',question,re.I):
            gate(question, origin_agent or 'pin_check')
            continue
        if not expert and re.search(r'capacitance|sink.current|pull.up|rise.time|thermal resistance|GPIO|\bpin\b|Net-\(|\bU\d+\b|\bR\d{3}\b|RF connector|supply tolerance|(?:I[²2]C|SPI).{0,30}speed|(?:highest|maximum).{0,35}(?:rail |supply )?voltage',question,re.I):
            gate(question, origin_agent or 'ai_review')
            continue
        ask, decision = gate(question, origin_agent)
        if not ask:
            continue
        if decision.get('technical_followup'):
            followup_delegations.append({'id': decision['id'] + '-t',
                                         'target_agent': decision['technical_followup']['target_agent'],
                                         'required_evidence': decision['technical_followup']['required_evidence'],
                                         'origin_agent': decision['originating_agent'],
                                         'need': decision['need'],
                                         'reason': ('Technical half of a mixed need; '
                                                    'the user-specific part was asked separately.'),
                                         'scope': decision.get('scope', {}), 'resolution': None})
        posed = qc_creator.render_question(decision, expert=expert)
        hint = 'This question came from the review of your saved design.'
        if not expert and posed['hint'] != decision['question']:
            hint = posed['hint']
        task = {'kind': 'question', 'question': question, 'hint': hint}
        task['qc'] = {'id': decision['id'], 'originating_agent': decision['originating_agent'],
                      'reason': decision['reason']}
        add(task)
    calculations=data.get('verification_results', {}).get('calculations', [])
    for section in dict.fromkeys(c['section'] for c in calculations):
        if not expert:continue
        group=[c for c in calculations if c['section']==section]
        missing=[c for c in group if c.get('status')=='missing']
        if not missing:continue
        # Board-bound but context-stable: per-target signatures embed a
        # design_context hash that rotates with every saved answer, which
        # re-asked "I don't know" topics forever. Keys plus the board
        # hashes re-ask only when the board itself changes.
        signature=hashlib.sha256(json.dumps([basis,sorted(c['key'] for c in group)],sort_keys=True).encode()).hexdigest()[:20]
        question,hint=CALCULATION_QUESTIONS.get(section,('What limits apply to this check?', 'Enter the operating limits and their source.'))
        task={'kind':'calculation','id':'calculation:'+section,'signature':signature,'section':section,
              'targets':[c['key'] for c in missing], 'question':question,
              'hint':hint+' The app calculates each result as you save. If you do not know these values, say so; do not guess.'}
        ask, decision = gate(question, 'calculator', context_hint='requirements_entry')
        if not ask:
            continue
        task['qc'] = {'id': decision['id'], 'originating_agent': decision['originating_agent'],
                      'reason': decision['reason']}
        add(task)
    for row in data.get('before_ordering', {}).get('checks', []):
        if not expert:continue
        if row['id'] not in PHYSICAL or row['status'] not in ('missing', 'partial'): continue
        question, hint = PHYSICAL[row['id']]
        task={'kind': 'physical', 'id': 'physical:'+row['id'], 'question': question,
              'hint': hint+' Your answer is recorded as your report, not an automatic verification.'}
        ask, decision = gate(question, 'physical_check', context_hint='requirements_entry')
        if not ask:
            continue
        task['qc'] = {'id': decision['id'], 'originating_agent': decision['originating_agent'],
                      'reason': decision['reason']}
        add(task)
    return {'tasks': tasks, 'count': len(tasks), 'needs_product_profile':False,
            'experience':'expert' if expert else 'beginner',
            'technical_inputs_unresolved':sum(c.get('status')=='missing' for c in calculations),
            'question_decisions':[{'id': d['id'], 'decision': d['decision'], 'ask_user': d['ask_user'],
                                   'origin_agent': d['origin_agent'], 'target_agent': d.get('target_agent'),
                                   'required_evidence': d.get('required_evidence'),
                                   'scope': d.get('scope', {}), 'reason': d['reason'], 'need': d['need']}
                                  for d in question_decisions],
            'delegations':[{'id': d['id'], 'target_agent': d.get('target_agent'),
                            'required_evidence': d.get('required_evidence'),
                            'origin_agent': d['origin_agent'], 'need': d['need'],
                            'reason': d.get('reason', ''),
                            'scope': d.get('scope', {}), 'resolution': d.get('resolution')}
                           for d in question_decisions if d['decision'] == qc_creator.DELEGATE
                           ] + followup_delegations,
            'unknowns':[{'id': d['id'], 'reason': d['reason'], 'need': d['need']}
                        for d in question_decisions if d['decision'] == qc_creator.UNKNOWN]}

"""Central Question Creator: the only path from an agent's uncertainty to the user.

No internal agent asks the user directly. Every proposed user question passes
through decide(), which classifies the missing information:

  TYPE 1 (technical / externally verifiable — specs, pin functions, power,
  connectivity, calculations, manufacturing): the creator first tries to
  resolve it from evidence already in hand, otherwise DELEGATEs it to a
  capable specialist agent with the required evidence named. It NEVER becomes
  a direct user question.

  TYPE 2 (user-specific knowledge / design intent — enclosure, environment,
  constraints, application behavior): a candidate for ASK_USER, only when no
  available source, document, previous answer, or specialist can answer it.

Anything else is UNKNOWN: preserved with its reason, never invented, never
looped on.

A 'beginner_interview' context hint additionally suppresses
implementation-framed needs (HOW the circuit should work): beginners are
asked product behavior, never signaling; experts keep those questions.

Guards: per-need deduplication (one investigation, not repeats), a maximum
delegation depth, and a visited-agent chain that terminates circular
delegation safely. The gate itself makes no model calls; delegation reuses
the existing budgeted agent paths. All decisions are recorded with
provenance so answers route back to the originating agent.
"""
import hashlib
import re

VERSION = 1
MAX_DEPTH = 3

DELEGATE = 'delegate'
ASK_USER = 'ask_user'
UNKNOWN = 'unknown'

# Real specialists this creator can route to (module names, not personas).
# 'packet' is the saved design evidence itself (nets, pins, ERC/DRC, BOM);
# 'user' is terminal: only ASK_USER decisions name it.
AGENTS = {
    'investigation': ('evidence investigation tools: trace_connections, '
                      'read_datasheet, calculate_limits, inspect_layout, '
                      'check_manufacturer'),
    'consult': 'evidence-grounded answers to FAULT claim challenges',
    'origin': 'product reconstruction from saved packet facts',
    'packet': 'saved KiCad connectivity, ERC/DRC, BOM, and calculations',
    'user': 'the designer; user-specific knowledge and design intent only',
}

_TECHNICAL = (
    'datasheet', 'specification', 'rating', 'tolerance', 'pinout', 'pin table',
    'pin function', 'pin function', 'pull-up', 'pull-up', 'pullup', 'pulldown',
    'pull-down', 'decoupling', 'regulator', 'regulator requirement', 'headroom',
    'current limit', 'voltage drop', 'impedance', 'rise time', 'fall time',
    'capacitance', 'inductance', 'resistance', 'power dissipation', 'thermal',
    'derating', 'footprint', 'land pattern', 'kicad', 'erc', 'drc', 'netlist',
    'connectivity', 'net split', 'net merge', 'short', 'clearance', 'annular',
    'trace width', 'copper weight', 'stackup', 'via', 'silkscreen', 'soldermask',
    'mpn', 'part number', 'substitute', 'alternate part', 'life cycle',
    'moisture sensitivity', 'esd rating', 'absolute maximum',
)
_USER_SPECIFIC = (
    'enclosure', 'case', 'housing', 'potting', 'conformal coat',
    'operating environment', 'ambient', 'outdoor', 'indoor', 'waterproof',
    'ip rating', 'intended use', 'what is this product', 'meant to do',
    'application', 'user', 'operator', 'installer', 'constraint',
    'requirement', 'must the product', 'should the product', 'behavior',
    'firmware use',
    'firmware behavior', 'what should happen', 'while charging',
    'simultaneously', 'use case', 'market', 'certification goal',
    'budget', 'timeline', 'preference',
)


def _norm(text):
    return ' '.join(str(text or '').split()).casefold()


def classify(need, scope_text=''):
    """TYPE 1 vs TYPE 2 vs unknown. Deterministic keyword families over the
    need plus its scope; ties and empty needs are unknown, never guessed."""
    text = _norm(need) + ' ' + _norm(scope_text)
    if not text.strip():
        return 'unknown'
    tech = sum(1 for k in _TECHNICAL if k in text)
    user = sum(1 for k in _USER_SPECIFIC if k in text)
    if tech and not user:
        return 'technical'
    if user and not tech:
        return 'user_specific'
    if tech and user:
        # Mixed needs (e.g. firmware speed + rail tolerance) stay user-facing
        # only for their user-specific part; the technical part is delegated.
        return 'mixed'
    return 'unknown'


def _signature(need, scope):
    scope = scope or {}
    parts = [_norm(need)] + sorted(scope.get('components', [])) + sorted(scope.get('nets', []))
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:20]


class DedupTracker:
    """One investigation per information need, across agents and wordings."""

    def __init__(self):
        self._seen = {}

    def check(self, need, scope):
        """Return the existing decision id when this need was already routed."""
        return self._seen.get(_signature(need, scope))

    def record(self, need, scope, decision_id):
        self._seen[_signature(need, scope)] = decision_id
        return decision_id


def build_evidence_index(data, context):
    """Facts the gate may consult without any model call: saved answers,
    documented intent, packet connectivity, calculations, supporting docs."""
    context = context or {}
    import re as _re
    def _is_unknown(text):
        return bool(_re.search(r"not\s+sure|don't\s+know|do\s+not\s+know|unknown|not\s+yet|skip|n/?a\b", _norm(text)))
    answers = {}
    for key in ('question_answers',):
        for ident, rec in (context.get(key) or {}).items():
            if isinstance(rec, dict) and rec.get('answer') and not _is_unknown(rec.get('answer')):
                # Explicit unknowns ("not sure yet") are non-answers: they
                # must never resolve a need, or resolved questions could
                # never be re-asked after the evidence changes.
                answers[_norm(rec.get('question'))] = rec.get('answer')
    design_context = context
    documented = []
    for field in ('description',):
        val = design_context.get(field)
        if isinstance(val, str) and val.strip():
            documented.append(('description', val.strip()[:500]))
    for intent in design_context.get('intentions', []) or []:
        if isinstance(intent, dict) and str(intent.get('note', '')).strip():
            documented.append(('intention:%s' % intent.get('group_id', '?'),
                               str(intent['note']).strip()[:500]))
    operating = design_context.get('operating_context', {}) or {}
    if isinstance(operating, dict):
        for key, val in operating.items():
            if isinstance(val, str) and val.strip():
                documented.append(('operating:%s' % key, val.strip()[:300]))
    documents = []
    for holder in (context, data or {}):
        docs = holder.get('documents') or holder.get('attachments') or []
        if isinstance(docs, list):
            for doc in docs:
                text = doc.get('text') if isinstance(doc, dict) else doc
                if isinstance(text, str) and text.strip():
                    name = doc.get('name', 'document') if isinstance(doc, dict) else 'document'
                    documents.append((name, text.strip()[:2000]))
    components = {}
    pins = {}
    nets = set()
    for comp in (data or {}).get('components', []) or []:
        if isinstance(comp, str):
            if comp:
                components[comp] = {'reference': comp}
            continue
        if not isinstance(comp, dict):
            continue
        ref = comp.get('reference', '')
        if ref:
            components[ref] = comp
            for pin in comp.get('pins', []) or []:
                if not isinstance(pin, dict):
                    continue
                pins[(ref, str(pin.get('number', '')))] = pin
                if pin.get('net'):
                    nets.add(pin['net'])
    calc_missing = set()
    for calc in ((data or {}).get('verification_results', {}) or {}).get('calculations', []) or []:
        if calc.get('status') == 'missing':
            calc_missing.add(calc.get('section', ''))
    return {'answers': answers, 'documented': documented, 'documents': documents,
            'components': components, 'pins': pins, 'nets': nets,
            'calc_missing': calc_missing}


#: A need must ask about connectivity before packet pin/net facts can
#: resolve it; anything else merely mentioning a part is not answered.
_CONNECTIVITY_QUESTION = ('pin', 'net', 'connect', 'wire', 'rout', 'pad',
                          'signal', 'short', 'continuity')


def _mentions_documented(need, index):
    text = _norm(need)
    hits = []
    for source, fact in index['documented']:
        words = [w for w in _norm(fact).split() if len(w) > 3]
        overlap = sum(1 for w in words if w in text)
        if overlap >= 2 or (_norm(fact)[:60] and _norm(fact)[:60] in text):
            hits.append((source, fact))
    for name, doctext in index['documents']:
        words = [w for w in _norm(doctext).split() if len(w) > 3]
        overlap = sum(1 for w in words if w in text)
        if overlap >= 2:
            hits.append(('document:%s' % name, doctext[:500]))
    for question, answer in index['answers'].items():
        if question and (question in text or text in question):
            hits.append(('previous_answer', '%s -> %s' % (question[:200], str(answer)[:200])))
    return hits


def try_resolve(need, index, scope=None):
    """Resolve from evidence already in hand. Returns (fact, provenance) or
    None. Only exact, cited facts — never inference dressed as resolution."""
    documented = _mentions_documented(need, index)
    if documented:
        source, fact = documented[0]
        return ('Documented: %s' % fact[:400], 'already recorded in %s' % source)
    scope = scope or {}
    refs = set(scope.get('components', []))
    refs |= set(re.findall(r'\b[A-Z]{1,3}\d{1,4}\b', str(need)))
    nets = set(scope.get('nets', []))
    nets |= set(re.findall(r'Net-\([^)]+\)|\+?\d*V\d*|\bSDA\b|\bSCL\b', str(need)))
    # A part merely mentioned by a judgment question (intent, reconcile,
    # placement preference) is not answered by that part's existence in the
    # packet. Only connectivity questions can be resolved from connectivity.
    if not any(k in _norm(need) for k in _CONNECTIVITY_QUESTION):
        refs, nets = set(), set()
    facts = []
    for ref in sorted(refs):
        comp = index['components'].get(ref)
        pins = (comp or {}).get('pins', [])
        if comp and pins:
            pin_nets = sorted({p.get('net', '?') for p in pins})
            facts.append('%s pins/nets: %s' % (ref, ', '.join(pin_nets)))
    known_nets = sorted(n for n in nets if n in index['nets'])
    if known_nets:
        facts.append('nets present in the saved design: %s' % ', '.join(known_nets))
    if facts:
        return (' ; '.join(facts)[:800], 'saved KiCad connectivity (packet evidence)')
    return None


def delegate_target(classification, index):
    """Which specialist can pursue this, and what evidence it needs."""
    if classification == 'technical':
        if index.get('calc_missing'):
            return ('investigation',
                    'calculate_limits on saved revision-matched inputs for: %s'
                    % ', '.join(sorted(index['calc_missing'])))
        return ('investigation',
                'trace_connections / read_datasheet / inspect_layout on the referenced parts and nets')
    if classification == 'mixed':
        return ('investigation',
                'technical half via trace_connections / read_datasheet; user-specific half stays with the user')
    return (None, '')


#: Agents whose questions are designer-directed by construction (product
#: meaning, intent gaps, reconciliation). Their unclassifiable questions are
#: presumed user-specific rather than dropped — but only when the need names
#: an exact component, net, or decision. Generic product questions with no
#: hardware anchor are preserved as unknown, never posed. Technical content
#: still delegates and is never asked directly.
ORIGIN_DIRECTED = ('origin', 'origin_reconcile', 'ai_review', 'pin_check')

#: A user-facing question must name what makes it necessary: a component
#: reference, a net, or scope refs from the originating finding. Anything
#: else is generic curiosity, not an engineering need.
_ANCHOR = re.compile(r'\b[A-Z]{1,3}\d{1,4}\b|Net-\([^)]*\)')

#: Technical verification language: asking the user to confirm, check, or
#: verify a datasheet fact, pinout, pad type, footprint, or wiring
#: requirement. That work belongs to internal investigation (datasheets,
#: packet connectivity, specialists) — never to the user, anchored or not,
#: beginner or expert. Board-level entry requirements (calculators,
#: before-ordering checks) are exempt by construction.
_VERIFY_TECHNICAL = re.compile(
    r'\b(?:confirm|verify|verifying|validate|validating|double[ -]?check|'
    r'check|inspect|measure|trace|ensure)\b'
    r'.{0,60}\b(?:datasheets?|pinouts?|pin\s+tables?|pin\s+functions?|'
    r'mountings?|mechanical|footprints?|land\s+patterns?|terminals?|'
    r'wiring|wires?|nets?|connections?|continuity|specifications?|mpns?|'
    r'part\s+(?:identity|number)|tolerances?|ratings?)\b',
    re.IGNORECASE)

#: Electrical implementation language: HOW the circuit should work (shared
#: wires, pulls, interrupts, logic levels, GPIO behavior, signaling,
#: firmware mechanics). Beginners decide product behavior, never this.
#: Matched case-insensitively against the need text.
_IMPLEMENTATION = re.compile(
    r'shared\s+(\w+\s+)?wire|\bpull\b.{0,40}\b(low|high)\b|pull\s+(up|down)|'
    r'logic[ -]?levels?|interrupt|gpio\b|signaling|signal\s+integrity|'
    r'debounce|matrix\s+scan|\bscan\s+the\s+(keys|matrix|rows)|'
    r'poll(ing|ed)?\s+the|firmware\s+(behavior|behaviour|speed|use)|'
    r'baud\s*rate|\bi2c\b|\bspi\b|\buart\b|\badc\b|\bpwm\b|'
    r'\btap\s+the\s+main|wire\s+to\s+tap|active[ -]?(high|low)',
    re.IGNORECASE)


def decide(need, *, origin_agent, scope=None, index=None, tracker=None,
           chain=(), depth=0, context_hint=None):
    """The single gate. Returns a DELEGATE / ASK_USER / UNKNOWN decision;
    never routes endlessly (depth cap, visited chain, dedup). context_hint
    'requirements_entry' marks operating requirements the designer must
    supply (user-defined constraints); everything else classifies by text."""
    scope = scope or {}
    index = index if isinstance(index, dict) else {
        'answers': {}, 'documented': [], 'documents': [],
        'components': {}, 'pins': {}, 'nets': set(), 'calc_missing': set()}
    tracker = tracker if tracker is not None else DedupTracker()
    chain = tuple(chain)
    if context_hint == 'requirements_entry':
        kind = 'user_specific'
    else:
        kind = classify(need, ' '.join(scope.get('components', [])) + ' ' + ' '.join(scope.get('nets', [])))
    # Technical verification is never user homework, anchored or not: asking
    # the user to confirm a datasheet fact, pinout, pad type, footprint, or
    # wiring requirement classifies as technical and routes to internal
    # investigation, never to the user.
    if context_hint != 'requirements_entry' and _VERIFY_TECHNICAL.search(str(need)):
        kind = 'technical'
    base = {'need': ' '.join(str(need).split())[:500], 'origin_agent': origin_agent,
            'scope': {'components': sorted(set(scope.get('components', []))),
                      'nets': sorted(set(scope.get('nets', [])))},
            'classification': kind, 'depth': depth, 'chain': list(chain)}
    # Deduplication first: same need, one investigation.
    prior = tracker.check(need, scope)
    if prior:
        return {**base, 'id': prior, 'decision': DELEGATE, 'ask_user': False,
                'target_agent': 'deduplicated',
                'required_evidence': 'already routed; see the recorded decision',
                'reason': 'Duplicate of an already-routed information need.'}
    if depth > MAX_DEPTH:
        decision = {**base, 'decision': UNKNOWN, 'ask_user': False,
                    'reason': 'Delegation depth exceeded; preserved unresolved rather than looping.',
                    'unresolved_information': base['need']}
        decision['id'] = 'qc-%s' % _signature(need, scope)
        tracker.record(need, scope, decision['id'])
        return decision
    resolved = try_resolve(need, index, scope)
    if resolved:
        fact, provenance = resolved
        decision = {**base, 'decision': DELEGATE, 'ask_user': False,
                    'target_agent': 'packet',
                    'required_evidence': provenance,
                    'resolution': fact,
                    'reason': 'Answerable from evidence already in hand; no agent run or user question needed.'}
        decision['id'] = 'qc-%s' % _signature(need, scope)
        tracker.record(need, scope, decision['id'])
        return decision
    if kind == 'technical':
        target, evidence = delegate_target(kind, index)
        if target in chain:
            decision = {**base, 'decision': UNKNOWN, 'ask_user': False,
                        'reason': 'Circular delegation (%s already asked); preserved unresolved.' % ' -> '.join(
                            list(chain) + [target]),
                        'unresolved_information': base['need']}
            decision['id'] = 'qc-%s' % _signature(need, scope)
            tracker.record(need, scope, decision['id'])
            return decision
        decision = {**base, 'decision': DELEGATE, 'ask_user': False,
                    'target_agent': target, 'required_evidence': evidence,
                    'reason': 'Technical uncertainty: routed to a capable specialist with named evidence.'}
        decision['id'] = 'qc-%s' % _signature(need, scope)
        tracker.record(need, scope, decision['id'])
        return decision
    # Board-level entry requirements (calculator limits, before-ordering
    # checks) are anchored by construction: the designer opted into those
    # checks for this revision. Everything else posed to the user must name
    # its hardware anchor.
    if context_hint == 'requirements_entry':
        anchored = True
    else:
        anchored = bool(set(scope.get('components', [])) | set(scope.get('nets', []))
                        or _ANCHOR.search(str(need)))
    # Beginners decide product behavior, never electrical implementation. A
    # HOW-the-circuit-works need aimed at a beginner is preserved for
    # internal investigation (technical) or as unresolved (user-specific),
    # never posed. Experts keep the anchored implementation questions.
    beginner_impl = (context_hint == 'beginner_interview'
                     and kind != 'technical'
                     and bool(_IMPLEMENTATION.search(str(need))))
    if beginner_impl and kind == 'mixed':
        target, evidence = delegate_target(kind, index)
        decision = {**base, 'decision': DELEGATE, 'ask_user': False,
                    'target_agent': target, 'required_evidence': evidence,
                    'reason': ('Beginner review: the technical half is delegated for investigation; '
                               'the implementation-framed user half is preserved unresolved rather '
                               'than asking a beginner to decide signaling.')}
        decision['id'] = 'qc-%s' % _signature(need, scope)
        tracker.record(need, scope, decision['id'])
        return decision
    if beginner_impl:
        decision = {**base, 'decision': UNKNOWN, 'ask_user': False,
                    'reason': ('Beginner review: electrical implementation detail. Beginners are asked '
                               'what the product should do, not how its circuits should work; '
                               'preserved unresolved for internal investigation.'),
                    'unresolved_information': base['need']}
        decision['id'] = 'qc-%s' % _signature(need, scope)
        tracker.record(need, scope, decision['id'])
        return decision
    if kind == 'mixed':
        target, evidence = delegate_target(kind, index)
        if not anchored:
            # The technical half is still investigated; the user-specific
            # half names nothing to ask about, so it is preserved unresolved
            # instead of becoming a generic question.
            decision = {**base, 'decision': DELEGATE, 'ask_user': False,
                        'target_agent': target, 'required_evidence': evidence,
                        'reason': ('Technical half delegated for investigation; the user-specific part '
                                   'references no component, net, or decision, so it is preserved '
                                   'unresolved rather than asked generically.')}
            decision['id'] = 'qc-%s' % _signature(need, scope)
            tracker.record(need, scope, decision['id'])
            return decision
        # The user-specific part needs the designer; the technical part is
        # delegated alongside so it is investigated, not just asked about.
        decision = {**base, 'decision': ASK_USER, 'ask_user': True,
                    'originating_agent': origin_agent,
                    'question': base['need'],
                    'reason': ('Part of this needs user knowledge no source or specialist '
                               'can establish; the technical part is delegated in parallel.'),
                    'technical_followup': {'target_agent': target, 'required_evidence': evidence}}
        decision['id'] = 'qc-%s' % _signature(need, scope)
        tracker.record(need, scope, decision['id'])
        return decision
    if kind == 'user_specific' or (kind == 'unknown' and origin_agent in ORIGIN_DIRECTED):
        if not anchored:
            decision = {**base, 'decision': UNKNOWN, 'ask_user': False,
                        'reason': ('No component, net, or engineering decision referenced: a generic '
                                   'product question is preserved unresolved, never posed.'),
                        'unresolved_information': base['need']}
            decision['id'] = 'qc-%s' % _signature(need, scope)
            tracker.record(need, scope, decision['id'])
            return decision
        if kind == 'unknown':
            base = {**base, 'classification': 'user_specific (origin-directed presumption)'}
        decision = {**base, 'decision': ASK_USER, 'ask_user': True,
                    'originating_agent': origin_agent,
                    'question': base['need'],
                    'reason': ('No available source, document, previous answer, or specialist '
                               'can establish this user-specific fact.')}
        decision['id'] = 'qc-%s' % _signature(need, scope)
        tracker.record(need, scope, decision['id'])
        return decision
    decision = {**base, 'decision': UNKNOWN, 'ask_user': False,
                'reason': 'Not classifiable as technical or user-specific with the available context.',
                'unresolved_information': base['need']}
    decision['id'] = 'qc-%s' % _signature(need, scope)
    tracker.record(need, scope, decision['id'])
    return decision


#: Evidence phrases produced by delegate_target, in priority order, mapped to
#: the deterministic specialist tool that gathers that evidence. The router
#: executes DELEGATE decisions through these tools; never invents a tool.
EVIDENCE_TOOLS = (
    ('read_datasheet', ('read_datasheet', 'datasheet', 'pinout', 'pin table', 'specification',
                        'mpn', 'part number')),
    ('calculate_limits', ('calculate_limits', 'calculate', 'operating limits')),
    ('inspect_layout', ('inspect_layout', 'inspect layout', 'layout', 'copper', 'trace width',
                        'stackup')),
    ('check_manufacturer', ('check_manufacturer', 'manufactur')),
    ('trace_connections', ('trace_connections', 'trace connection', 'connectivity')),
)


def delegation_task(decision):
    """Deterministic specialist task for a DELEGATE decision.

    Maps the decision's required evidence to one allowlisted tool and its
    references to packet components. Returns None when the decision is not
    executable (a user question, an unknown, evidence already in hand, or no
    fitted references): the caller must preserve it as an unexecuted
    delegation, never invent a substitute task.
    """
    if not isinstance(decision, dict):
        return None
    if decision.get('decision') in (ASK_USER, UNKNOWN):
        return None
    target = decision.get('target_agent')
    if target in (None, '', 'packet', 'deduplicated'):
        return None
    required = str(decision.get('required_evidence') or '').casefold()
    tool = next((tool for tool, keys in EVIDENCE_TOOLS
                 if any(k in required for k in keys)), 'trace_connections')
    scope = decision.get('scope') or {}
    refs = list(dict.fromkeys(list(scope.get('components') or []) + re.findall(
        r'\b[A-Z]{1,3}\d{1,4}\b', str(decision.get('need') or ''))))[:8]
    question = ' '.join(str(decision.get('need') or '').split())[:600]
    reason = ' '.join(str(decision.get('reason') or '').split())[:600]
    if not refs or not question or not reason:
        return None
    return {'id': decision.get('id'), 'tool': tool, 'references': refs,
            'question': question, 'reason': reason,
            'origin_agent': decision.get('originating_agent', ''),
            'need': decision.get('need', '')}


def render_question(decision, *, expert=False):
    """User-facing phrasing. Uses only the decision's own question and
    reason — never invents specifics. Beginners get plain language plus why
    it matters; experts keep the technical framing."""
    if decision.get('decision') != ASK_USER:
        raise ValueError('Only ASK_USER decisions can be posed to the user.')
    question = decision.get('question', '')
    why = decision.get('reason', '')
    if expert:
        return {'question': question,
                'hint': 'Routed from %s. %s' % (decision.get('originating_agent', 'analysis'), why)}
    return {'question': question,
            'hint': ('This affects what the review can conclude. %s '
                     'If you do not know, say so — unknowns stay unresolved, never guessed.'
                     % why)}


def route_answer(decision, answer):
    """Provenance record sending a user answer back to the originating agent."""
    if decision.get('decision') != ASK_USER:
        raise ValueError('Only ASK_USER decisions accept user answers.')
    if not str(answer or '').strip():
        raise ValueError('An answer needs text.')
    return {'decision_id': decision['id'], 'need': decision.get('need', ''),
            'originating_agent': decision.get('originating_agent', ''),
            'answer': ' '.join(str(answer).split())[:2000], 'source': 'User answer',
            'resume': 'resume %s with this fact; re-run verification before closing the finding' % (
                decision.get('originating_agent', 'analysis'))}


def resume_for(need_signature_or_text, stored_answers):
    """Find a recorded user answer covering this need, if any."""
    if not isinstance(stored_answers, dict):
        return None
    want = _norm(need_signature_or_text)
    for ident, rec in stored_answers.items():
        if not isinstance(rec, dict) or not rec.get('answer'):
            continue
        if want and (want in _norm(rec.get('question')) or _norm(rec.get('question')) in want):
            return rec
        if ident == need_signature_or_text:
            return rec
    return None

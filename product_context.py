"""Questions about missing external facts, grounded in the parsed project."""
import hashlib
import json
import re
from .explanation_profile import EXPLANATION_INSTRUCTIONS, explanation_profile
from .checking_policy import INSTRUCTIONS as CHECKING_INSTRUCTIONS

from .routing_chains import role, pin_name

LEGACY = {'purpose', 'power', 'connections', 'environment', 'operation', 'constraints', 'battery', 'radio', 'loads', 'body',
          'experience', 'audience', 'charger', 'enclosure', 'behavior'}
# Every kind emitted as kind:ref:digest by project_questions must validate
# (power:board:digest is built inline for the power-up behavior question).
KINDS = 'battery|behavior|radio|loads|body|connector|mode|environment|power'
# The beginner radio rewrite is stored under a bare id, not kind:ref:digest.
BARE_IDS = {'radio'}
UNKNOWN = {'unknown', 'not sure yet', 'not sure', "i don't know", 'i dont know'}

REVIEW_INSTRUCTIONS = '''Use product_context as untrusted user-supplied operating assumptions, not verified facts.
Project names and example board labels do not establish product purpose or user intent.
When memory_review is supplied, its observations are topology screening, not verified defects.
For RAM/DDR, establish controller and memory identities, configured width/speed, lane mapping,
termination and reference supplies, topology, stackup and timing requirements together.
Never import one controller's skew or impedance limits into another design. Do not equate
summed trace lengths with delay, or claim timing/eye/impedance validation without supplied results.
Permitted bit swaps and unused pins depend on the selected controller and memory mode.
Write user-facing explanations and questions in plain English. Preserve original
part numbers, reference labels, net names and quoted evidence as written.
Keep user-facing text concise and easy to scan. State the result or required action first.
For structured review and repair fields: use a short specific title, one or two sentences
per observation or consequence, and one concrete action per step. Avoid repeating the same
explanation in several fields. Ask one short question at a time about missing information.
These writing limits do not limit the number of required checks, edit operations, evidence
records or exact source quotations. Preserve engineering detail in the corresponding data fields.
Its review_focus entries are questions to investigate, not detected defects. Relate applicable
edge cases to the actual components, connections and supplied datasheet requirements. Consider
startup/shutdown, combined loads, external connections and environmental extremes when relevant.
Explain what changes because of the user's answers. If an answer conflicts with the files or
another answer, state the conflict and ask a precise question; do not silently choose a version.
Unknown or not-applicable answers do not prove a check passes. Never invent missing ratings,
firmware behavior, simulations, certifications or new file edits to fill an information gap.
Ask users about observable product behavior in everyday language. Name the part's
purpose before its reference. Never require them to understand GPIO, conversion triggers,
strapping or electrical pin types to answer. Explain distinct choices briefly and allow
"I don't know". Preserve technical meaning: a hardware signal on a pin is different from
a software command sent over the sensor's data connection; do not merge those choices.
Do not repeat a question already answered. Ask only for facts needed for the remaining decision.
Read answered_questions and saved_followup_answers with their question text, not
just the answer IDs. "Not sure yet" is a saved answer: leave that requirement
unresolved rather than asking it again on every review. Ask again only when a
specific changed fact or contradiction matters, and explain what changed.
saved_conversations are previous user messages with the preceding conversational
prompt. Use relevant product statements as user assumptions, not measured facts.
Questions, hypothetical examples, and earlier assistant suggestions are not user
requirements. Do not convert them into facts. Newer explicit corrections supersede
older answers; if the correction is ambiguous, ask only about that conflict.
Use product_context.check_scope to choose the review areas for this product BEFORE
giving advice. Its selection is not a defect or a passed check. For needs_context,
ask the specific missing use question instead of proposing a repair. Do not suggest
adding an antenna when radio use is unconfirmed or explicitly unused. Do not drop
EMC or ESD just because a product is wired. Relate fast-edge, noise, heating,
return-path and transient concerns to actual evidence and operating conditions.
Never claim the scope planner performed a simulation listed as unavailable.
Use the saved experience preference. Beginners describe their finished product, charger,
users, case and behavior. Do not ask them for capacitance, sink-current ratings, resistor
tolerance, thermal resistance or other engineering parameters. First derive what you can
from the saved parts, connections and actual supplied datasheet evidence. Separate known
values, unconfirmed suggestions and genuinely missing measurements. Ask only for missing
product-use facts in everyday language; show unresolved technical limits in the review.
For experienced EEs, precise electrical questions are appropriate when values cannot be
established from evidence. Never infer a maximum rating from a nominal net label.
Reuse enclosure, charger and intended-use answers across revisions. A PCB edit does not
mean the product's case changed. Ask again only about a specific contradiction, explaining it.
'''
REVIEW_INSTRUCTIONS += '\n' + EXPLANATION_INSTRUCTIONS
REVIEW_INSTRUCTIONS += '\n' + CHECKING_INSTRUCTIONS



def validate_answers(value):
    if not isinstance(value, dict) or len(value) > 1000:
        raise ValueError('Use a product-answer object with at most 1,000 entries.')
    result = {}
    for key, text in value.items():
        if not isinstance(key, str) or (key not in LEGACY and key not in BARE_IDS
                and not re.fullmatch(r'(?:'+KINDS+r'):[A-Za-z0-9_.-]{1,40}:[a-f0-9]{10}', key)):
            raise ValueError('Use the supported product questions.')
        if not isinstance(text, str) or len(text) > 1200 or '\x00' in text:
            raise ValueError('Keep each product answer under 1,200 characters.')
        if text.strip():result[key] = text.strip()
    if len(json.dumps(result, ensure_ascii=False).encode()) > 1024*1024:
        raise ValueError('Saved product answers exceed 1 MB.')
    return result


def merge_answers(current, updates):
    """Patch only supplied answers; a blank explicitly clears that answer."""
    validate_answers(updates)
    merged=dict(current)
    for key, value in updates.items():
        if value.strip(): merged[key]=value.strip()
        else: merged.pop(key,None)
    return validate_answers(merged)


def _normalize_question(text):
    """Canonicalize a question for repeat detection.

    Folds only character ENCODINGS of the same physical unit (micro signs to
    'u', ohm signs to 'ohm'); both sides pass through the same function, and
    a match still requires the entire normalized string to be identical.
    Broader semantic merging is deliberately declined: 'verify the C105
    value' vs 'verify the C105 voltage rating' share a component but are
    distinct requirements, and no deterministic overlap rule separates those
    from true paraphrases without risking coverage loss.
    """
    folded = str(text).casefold()
    for glyph, ascii_form in (('µ', 'u'), ('μ', 'u'), ('Ω', 'ohm'), ('Ω', 'ohm')):
        folded = folded.replace(glyph, ascii_form)
    return re.sub(r'\W+', ' ', folded).strip()


def unanswered_followups(questions, context):
    """Suppress exact repeats; semantic follow-ups still need model reasoning."""
    normalize = _normalize_question
    answered={normalize(q['question']) for q in context.get('question_answers',{}).values() if q.get('answer')}
    from .user_profile import remembered_answers, topics
    facts=remembered_answers(context)
    return [q for q in questions if normalize(q) not in answered
            and not (topics(q) and all(facts.get(t) for t in topics(q)))]


def _parts(project):
    result = {}
    for ref, component in getattr(project,'components',{}).items():
        records = getattr(component,'schematic',[]) or component.pcb
        if not records or records[0].dnp:continue
        record = records[0]
        result[ref] = {'value':record.value, 'symbol':record.symbol, 'file':record.source_file,
            'properties':record.properties, 'pins':component.schematic_pins,
            'description':' '.join(str(v) for k,v in record.properties.items() if k.lower()=='description')}
    return result


def _ref_sort_key(ref):
    """Order SW2 before SW10 so numeric series compress to a range."""
    match = re.fullmatch(r'([A-Za-z_]+)(\d+)', ref)
    if match:
        return (match.group(1), int(match.group(2)))
    return (ref, -1)


def _ref_range(refs):
    """Compact 'SW5–SW94' for a numeric series, else a short list with count."""
    ordered = sorted(refs, key=_ref_sort_key)
    match = re.fullmatch(r'([A-Za-z_]+)(\d+)', ordered[0]) if len(ordered) > 1 else None
    if match:
        prefix = match.group(1)
        numbers = []
        for ref in ordered:
            part = re.fullmatch(re.escape(prefix) + r'(\d+)', ref)
            if not part:
                break
            numbers.append(int(part.group(1)))
        else:
            if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
                return '%s%d–%s%d' % (prefix, numbers[0], prefix, numbers[-1])
    if len(ordered) <= 6:
        return ', '.join(ordered)
    return '%s, … (%d switches)' % (', '.join(ordered[:6]), len(ordered))


def project_questions(project, value=None, context=None):
    """No role is inferred from the project name or from the user's answers."""
    answers = validate_answers(value if value is not None else project.design_context.get('product_context', {}))
    parts = _parts(project)
    by_net = {}
    for ref, part in parts.items():
        for pin in part['pins']:
            if pin.net and not pin.no_connect and not pin.net.startswith('unconnected-'):
                by_net.setdefault(pin.net, []).append((ref, pin))
    questions=[]; consumed_connectors=set()

    def connectors(nets):
        return sorted({ref for net in nets for ref,pin in by_net.get(net, [])
                       if ref.startswith(('J','P','BT')) or 'Connector' in parts[ref]['symbol']})

    def pin_facts(ref, pins):
        return [f"{ref} pin {p.number} ({p.name}) → {p.net}" for p in pins if p.net and not p.net.startswith('unconnected-')][:8]

    def specified_loads(pins, prefix):
        return sorted({r for pin in pins for r,_ in by_net.get(pin.net,[])
            if r.startswith(prefix) and parts[r]['value'].lower() not in
            ('', '~', 'battery', 'battery_cell', 'motor', 'motor_dc', 'motor_ac')})

    def add(kind, ref, question, known, why, pins=(), peers=()):
        # A changed part or pin/net assignment gets a new question ID. Old
        # answers stay visible as historical context, never silently migrate.
        refs=sorted({ref,*peers})
        signature=[(r,parts[r]['value'],parts[r]['symbol'],
                    sorted((p.number,p.name,p.net) for p in parts[r]['pins'])) for r in refs if r in parts]
        digest=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()[:10]
        key=f'{kind}:{ref}:{digest}'
        # Read answers written by the earlier signature format without treating
        # footprint/library metadata or pin ordering as a changed use case.
        legacy=[(r,parts[r]['value'],parts[r]['symbol'],sorted(parts[r]['properties'].items()),
                 [(p.number,p.name,p.net) for p in parts[r]['pins']]) for r in refs if r in parts]
        old=f'{kind}:{ref}:'+hashlib.sha256(json.dumps(legacy,sort_keys=True).encode()).hexdigest()[:10]
        if key not in answers and old in answers:
            answers[key]=answers.pop(old)
        questions.append({'id':key,'reference':ref,'question':question,'known':known,
            'hint':why, 'evidence':pin_facts(ref,pins), 'source':parts[ref]['file'],
            'review_focus':why})

    def behavior_questions():
        # Output/behavior questions: what the finished product should DO.
        # Derived from parsed inputs/outputs, never from the project name.
        addr_chain = []
        switch_groups = {}
        for ref, part in sorted(parts.items()):
            text=f"{part['value']} {part['symbol']} {part['description']}".lower()
            pins=[p for p in part['pins'] if p.net and not p.net.startswith('unconnected-')]
            if not pins:
                continue
            label=f'{ref} ({part["value"]})'
            if 'encoder' in text or 'rotary' in text:
                add('behavior',ref,f'What should happen when the {ref} knob is turned or pressed?',
                    f'{label} is a rotary input; its contacts reach '+', '.join(sorted({p.net for p in pins}))+'.',
                    'The files show the contact wiring, not what the knob should control (menu, volume, selection) or which action a press confirms.',
                    pins)
            elif ref.startswith('D') and 'led' in text:
                if any(k in text for k in ('sk6812','ws2812','apa102','neopixel','addressable')):
                    addr_chain.append((ref,part,pins))
                    continue
                nets=sorted({p.net for p in pins})
                add('behavior',ref,f'What should the {ref} indicator show?',
                    f'{label} connects to '+', '.join(nets)+'.',
                    'The files show the LED wiring, not what it should signal (power, charging, faults, activity) or how bright it should be.',
                    pins)
            elif ('sw_push' in text or 'tact' in text or ref.startswith('RST')
                    or (ref.startswith('BOOT') and 'button' in text)):
                if ref.startswith(('RST', 'BOOT')):
                    members=sorted({r for pin in pins for r,_ in by_net.get(pin.net,[]) if r!=ref})
                    add('behavior',ref,f'What should happen when {ref} is pressed?',
                        f'{label} pulls '+', '.join(sorted({p.net for p in pins}))+(' (shared with '+', '.join(members[:4])+').' if members else '.'),
                        'The files show the button wiring, not the intended behavior (reset, bootloader entry, user action) or how long a press should last.',
                        pins)
                else:
                    # Matrix/key switches share one role; asking per key spams
                    # dozens of identical questions. Group identical parts and
                    # ask once below.
                    switch_groups.setdefault((part['value'].lower(), part['symbol'].lower(),
                                              part['description'].lower()), []).append((ref, part, pins))
        # Addressable LED chains get one question, not one per pixel.
        if len(addr_chain) >= 2:
            refs = sorted(r for r, _, _ in addr_chain)
            data_nets = sorted({p.net for _, _, pins in addr_chain for p in pins
                                if p.net and not re.fullmatch(r'GND|VSS|VDD|VCC|\+?.*V\d*', p.net or '', re.I)})
            chain_pins = [p for _, _, pins in addr_chain for p in pins]
            add('behavior', refs[0],
                f'What should the addressable LED chain ({", ".join(refs[:6])}{", …" if len(refs) > 6 else ""}) display?',
                f'{len(refs)} addressable LEDs in one chain'+(' with data on '+', '.join(data_nets) if data_nets else '')+'.',
                'The files show the LED wiring, not the intended patterns, colors, brightness or startup state.',
                chain_pins, [r for r in refs[1:]])
        elif addr_chain:
            ref, part, pins = addr_chain[0]
            add('behavior', ref, f'What should the {ref} indicator show?',
                f'{ref} ({part["value"]}) is an addressable LED.',
                'The files show the LED wiring, not what it should signal or display.',
                pins)
        # Identical switches share one question, not one per key. Small sets
        # (fewer than four) still ask individually since each button may differ.
        for members in switch_groups.values():
            if len(members) < 4:
                for ref, part, pins in members:
                    label = f'{ref} ({part["value"]})'
                    others = sorted({r for pin in pins for r, _ in by_net.get(pin.net, []) if r != ref})
                    add('behavior', ref, f'What should happen when {ref} is pressed?',
                        f'{label} pulls ' + ', '.join(sorted({p.net for p in pins})) + (' (shared with ' + ', '.join(others[:4]) + ').' if others else '.'),
                        'The files show the button wiring, not the intended behavior or how long a press should last.',
                        pins)
                continue
            refs = sorted(r for r, _, _ in members)
            chain_pins = [p for _, _, pins in members for p in pins]
            nets = sorted({p.net for p in chain_pins if p.net and not p.net.startswith('unconnected-')})
            add('behavior', refs[0],
                f'What should pressing the {len(refs)} {members[0][1]["value"]} switches ({_ref_range(refs)}) do?',
                f'{len(refs)} identical switches in one matrix/array' + (' on ' + ', '.join(nets[:6]) + '.' if nets else '.'),
                'The files show the switch wiring, not what the keys should do. Answer once for the whole set; name any key that behaves differently.',
                chain_pins, [r for r in refs[1:]])
        # Project-level power-on behavior, asked once when a supply exists.
        supply_refs=sorted({ref for ref,part in parts.items()
            if role(part['value'])=='power regulator'
            or re.search(r'\b(ldo|regulator|ld1117|ap2112|ams1117|buck|boost|charger)\b',
                         f"{part['value']} {part['symbol']} {part['description']}".lower())})
        rail_nets=sorted({p.net for ref,part in parts.items() for p in part['pins']
            if p.net and re.fullmatch(r'\+?(5V|3V3|3\.3V|VBUS|VBAT|VIN)',p.net or '',re.I)})
        if supply_refs or rail_nets:
            known_bits=[]
            if supply_refs:known_bits.append('regulation: '+', '.join(supply_refs))
            if rail_nets:known_bits.append('rails: '+', '.join(rail_nets))
            key_hint='power:board:'+hashlib.sha256(('|'.join(supply_refs+rail_nets)).encode()).hexdigest()[:10]
            if key_hint not in answers:
                questions.append({'id':key_hint,'reference':'power',
                    'question':'What should happen when the device receives power?',
                    'known':'The files show '+('; '.join(known_bits))+'.',
                    'hint':'Describe the expected startup: indicators, default state, and anything that must NOT happen on power-up. Say “Not sure yet” if the behavior is undecided.',
                    'evidence':[],'source':'','review_focus':'Expected power-up behavior for startup and sequencing checks.'})

    behavior_questions()
    for ref, part in sorted(parts.items()):
        value=part['value'];pins=part['pins'];declared=role(value)
        text=f"{value} {part['symbol']} {part['description']}".lower()
        label=f'{ref} ({value})'
        if declared=='battery charger' or re.search(r'\b(battery charger|charge management|li-ion charger)\b',text):
            outputs=[p for p in pins if pin_name(p.name) in ('VBAT','BAT','BAT+','BATT')]
            targets=connectors([p.net for p in outputs]);consumed_connectors.update(targets)
            # Report an actual programming resistor instead of asking the user
            # to look up a value the schematic already contains.
            resistors=sorted({r for p in pins if 'PROG' in pin_name(p.name)
                for r,_ in by_net.get(p.net,[]) if r.startswith('R')})
            known=f'The saved symbol identifies {label} as a charger.'
            if targets:known+=' Its battery net reaches '+', '.join(targets)+'.'
            if resistors:known+=' Programming connection: '+', '.join(r+' = '+parts[r]['value'] for r in resistors)+'.'
            batteries=specified_loads(outputs,'BT')
            if batteries:known+=' Declared battery: '+', '.join(r+' ('+parts[r]['value']+')' for r in batteries)+'.'
            question=(f'Can the product run while charging {", ".join(batteries)}, and which functions run together?'
                if batteries else 'Which battery pack will connect to '+(', '.join(targets) or label)+', and can the product run while charging?')
            add('battery',ref,question,
                known,('The battery is declared in the files. Real operating loads during charging are still unknown and affect power-path checks.' if batteries else 'The files show the charger circuit, not the actual battery pack or load during charging. Give the pack model or capacity and allowed charge current if known; these affect charging and power-path checks.'),outputs,targets+resistors+batteries)
        antenna=[p for p in pins if pin_name(p.name) in ('ANT','RF','RFIO','RF_P','RFP','RFN')]
        if antenna or declared=='radio controller':
            paths=[path for path in project.signal_paths.get('paths',[]) if path.get('id')=='rf-'+ref]
            nets=sorted({p.net for p in antenna if p.net}|{n['net'] for path in paths for n in path.get('nets',[])})
            peers=sorted({r for n in nets for r,_ in by_net.get(n,[]) if r!=ref})
            known=f'{label} has a saved radio/antenna path.'
            if peers:known+=' Connected parts on the traced nets: '+', '.join(r+' ('+parts[r]['value']+')' for r in peers[:6])+'.'
            add('radio',ref,f'What enclosure and nearby materials will surround {ref}’s antenna, and what range do you need?',
                known,'The PCB shows the radio path, but not its final surroundings. Mention nearby metal, the battery or a hand/body, and any antenna connected off-board. This guides RF and keepout review without guessing RF performance.',antenna,peers)
        if declared=='vibration driver' or re.search(r'\b(haptic driver|motor driver|solenoid driver)\b',text):
            outputs=[p for p in pins if pin_name(p.name).startswith(('OUT','MOT'))]
            targets=connectors([p.net for p in outputs]);consumed_connectors.update(targets)
            loads=specified_loads(outputs,'M')
            known=f'The saved symbol identifies {label} as a driver. '
            known+=('Declared load: '+', '.join(r+' ('+parts[r]['value']+')' for r in loads)+'.' if loads
                else 'Its outputs reach '+', '.join(targets)+'.' if targets else 'The attached load specification is not established by its driver symbol.')
            add('loads',ref,(f'When will {", ".join(loads)} run, and which other functions run at the same time?' if loads else f'What motor or actuator will {ref} drive, and when will it run?'),
                known,
                'Operating duration and simultaneous loads affect supply demand. Include mechanical loading or startup/stall conditions that are not documented in the files.',outputs,targets+loads)
        electrodes=[p for p in pins if pin_name(p.name) in ('ECGP','ECGN','BIP','BIN')]
        if electrodes or declared=='ECG sensor':
            targets=connectors([p.net for p in electrodes]);consumed_connectors.update(targets)
            add('body',ref,'Will electrodes be connected to a person while a charger, debugger or other cable is attached?',
                f'{label} has biopotential/ECG connections'+(' reaching '+', '.join(targets) if targets else '')+'.',
                'The files show electrode inputs, not simultaneous real-world cable connections. This matters when reviewing external power and fault paths; it does not establish medical safety.',electrodes,targets)
        straps=[p for p in pins if re.match(r'^GPIO\d+',pin_name(p.name)) and re.fullmatch(r'(GND|VSS|\+?\d+(?:\.\d+)?V\d*)',p.net,re.I)]
        if straps:
            add('mode',ref,f'What should {ref} use '+', '.join(p.name for p in straps)+' for: leave it unused, read an incoming signal, or send a signal to another part?' ,
                'The schematic already shows this direct connection: '+'; '.join(pin_facts(ref,straps))+'.',
                'This wire is connected directly to a power or ground line. Your files do not say what the software will use it for. Choose “Not sure yet” if you have not decided; the review must check the sensor’s allowed uses before suggesting a change.',straps)

    for ref, part in sorted(parts.items()):
        if ref in consumed_connectors or not (ref.startswith(('J','P','BT')) or 'Connector' in part['symbol']):continue
        pins=[p for p in part['pins'] if p.net and not p.net.startswith('unconnected-')]
        if not pins:continue
        peers=[(r,p) for pin in pins for r,p in by_net.get(pin.net,[]) if r!=ref]
        debug=[(r,p) for r,p in peers if pin_name(p.name) in ('SWDIO','SWDCLK','TCK','TMS')]
        known=f"{ref} ({part['value']}) has "+'; '.join(pin_facts(ref,pins))+'.'
        if debug:
            known+=' These nets also reach '+', '.join(f'{r} {p.name}' for r,p in debug)+'.'
            question=f'When a debugger is plugged into {ref}, will it supply power or only sense the board’s voltage?'
            why='The SWD/debug wiring is in the files; debugger power settings and simultaneous external supplies are not. This affects back-powering and power-off checks.'
        else:
            signal_peers=[(r,p) for r,p in peers if r.startswith('U')
                and not re.search(r'GND|VSS|VDD|VCC',pin_name(p.name))
                and not re.fullmatch(r'(GND|VSS|\+.*|\d+(?:\.\d+)?V\d*)',p.net,re.I)]
            if signal_peers:known+=' Signal peers: '+', '.join(f'{r} {p.name}' for r,p in signal_peers[:4])+'.'
            question=f'What external device or supply connects to {ref}, and can it stay powered when this board is off?'
            why='The files identify these nets, not the connected equipment, its voltage range or hot-plug behavior. Give those details to check interface compatibility and back-powering.'
        # Signature only includes non-power peers to avoid repeating questions
        # when an unrelated decoupling capacitor on a shared rail changes.
        refs=sorted({r for r,p in debug})
        add('connector',ref,question,known,why,pins,refs)
    order={'battery':0,'power':0,'radio':1,'behavior':1,'body':2,'loads':3,'mode':4,'connector':5}
    all_questions=sorted(questions,key=lambda q:(order.get(q['id'].split(':')[0],9),q['reference']))
    # Keep the conversation bounded; the UI reports deferred questions rather
    # than implying all external requirements have been discovered.
    from .check_scope import project_scope
    scope=project_scope(project,{**(context if context is not None else project.design_context),'product_context':answers})
    all_questions=all_questions+scope['intake_questions']
    for q in scope['questions']:
        if q.get('answer'):
            prefix=q['id'].rsplit(':',1)[0]+':'
            answers={k:v for k,v in answers.items() if not k.startswith(prefix) or k==q['id']}
            answers[q['id']]=q['answer']
    # Completed answers must not occupy the entire first page while relevant
    # follow-ups remain hidden behind the question limit.
    pending=[q for q in all_questions if not answers.get(q['id'])]
    completed=[q for q in all_questions if answers.get(q['id'])]
    questions=(pending+completed)[:12]
    records={q['id']:q for q in [*all_questions,*scope['questions']]}
    return {'answers':answers,'questions':questions,
        'answered_questions':[dict(q,answer=answers[q['id']]) for q in records.values() if answers.get(q['id'])],
        'all_questions':list(records.values()),
        'active_question_ids':[q['id'] for q in all_questions],
        'unanswered':[q['id'] for q in questions if q['id'] not in answers],
        'deferred_count':sum(q['id'] not in {shown['id'] for shown in questions} for q in pending),
        'basis':'Parsed component identities and saved schematic pin/net connections'}


def product_evidence(context, project=None):
    answers=validate_answers(context.get('product_context',{}))
    if project is None:
        return {'answers':answers,'source':'User declarations; not independently verified',
                'explanation_profile':explanation_profile(context)}
    result=project_questions(project,answers,context=context)
    from .user_profile import remembered_answers
    result['user_profile']=remembered_answers(context)
    result['explanation_profile']=explanation_profile(context)
    answers=result['answers']
    result.pop('all_questions',None)
    from .check_scope import project_scope
    scope=project_scope(project,context)
    result['check_scope']={'checks':scope['checks'],'special_reviews':scope['special_reviews'],'note':scope['note']}
    ids=set(result.pop('active_question_ids',[])) | {q['id'] for q in scope['questions']}
    result['previous_revision_answers']={k:v for k,v in answers.items() if k not in ids and k not in LEGACY}
    result['previous_revision_questions']={k:context.get('product_questions',{}).get(k,k)
        for k in result['previous_revision_answers']}
    result['answers']={k:v for k,v in answers.items() if k in ids or k in LEGACY}
    result['source']='User declarations; not independently verified. Historical answers must be reconfirmed for changed parts.'
    result['confirmed_part_choices']=context.get('component_intent',{})
    result['saved_followup_answers']=list(context.get('question_answers',{}).values())
    result['saved_conversations']=context.get('conversation_memory',[])
    result['unknown']=result['unanswered']+[k for k,v in result['answers'].items() if v.lower().strip('. ') in UNKNOWN]
    result['review_focus']=[q['review_focus'] for q in result['questions']]
    from .verification import compact_evidence
    from dataclasses import asdict
    result['engineering_calculations']=compact_evidence({
        'components':{ref:asdict(c) for ref,c in project.components.items()},
        'circuit':project.circuit,'signal_paths':project.signal_paths,'provenance':project.provenance,
        'design_context':context,'verification':project.verification})
    geometry=project.board_checks.get('signal_geometry',{})
    if geometry.get('status') in ('measured','partial'):
        result['routing_geometry']={k:geometry[k] for k in ('status','straight_tracks','parallel_count','reference_coverage','limits')}
        result['routing_geometry']['reference_coverage']=[{k:v for k,v in r.items() if k!='gaps'} for r in geometry['reference_coverage']]
    return result

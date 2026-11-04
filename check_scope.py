"""Choose review scope from saved parts and current product declarations.

Selection is not a pass/fail result. No simulation is claimed by this module.
"""
import hashlib
import json
import re
from dataclasses import asdict

YES = 'Yes, this product uses this'
NO = 'No, this is not used in this product'
UNKNOWN = 'Not sure yet'


def project_scope(project, context=None):
    # Avoid serializing PDF pages, raw DRC reports and rendered board geometry.
    return build_scope({'components':{ref:{
        'schematic':[{'value':r.value,'symbol':r.symbol,'dnp':r.dnp} for r in c.schematic],
        'pcb':[{'value':r.value,'dnp':r.dnp} for r in c.pcb],
        'schematic_pins':[asdict(p) for p in c.schematic_pins]}
        for ref,c in project.components.items()},
        'design_context':context if context is not None else project.design_context})


def build_scope(data):
    context=data.get('design_context',{})
    answers=context.get('product_context',{})
    features={name:[] for name in ('radio','external','digital','analog','pulse','power')}
    inventory=[]
    for ref,component in sorted(data.get('components',{}).items()):
        records=component.get('schematic') or component.get('pcb') or []
        if not records or all(r.get('dnp') for r in records):continue
        record=next(r for r in records if not r.get('dnp'))
        value=str(record.get('value',''));symbol=str(record.get('symbol',''))
        pins=[p for p in component.get('schematic_pins',[]) if not p.get('no_connect')]
        names=' '.join(str(p.get('name','')) for p in pins).upper()
        text=(value+' '+symbol).upper()
        inventory.append([ref,value,symbol,[(p.get('number'),p.get('name'),p.get('net')) for p in pins]])
        def found(kind):features[kind].append(ref+' · '+value)
        if re.search(r'NRF(?:5[234]|91)|ESP32|CC26\d|SX12\d|ANTENNA|BLUETOOTH|WI-?FI',text) or re.search(r'\b(?:ANT|RF_P|RF_N|RFIO)\b',names):found('radio')
        if ref.startswith(('J','P')) or re.search(r'CONNECTOR|USB|SW_PUSH|KEYSWITCH',text):found('external')
        if re.search(r'USB|ETHERNET|DDR|HDMI',text) or re.search(r'\b(?:D\+|D-|USB_DP|USB_DM|SCK|SCLK|CLK|MCLK|SWCLK)\b',names):found('digital')
        if re.search(r'MAX3000|ADS12|ADS129|ECG|OPAMP|MICROPHONE|PHOTODIODE',text) or re.search(r'\b(?:AIN\d*|INP|INN|ADC\d*)\b',names):found('analog')
        if re.search(r'DRV\d|MOTOR|SOLENOID|RELAY|WS281|SK681|MAX301|NRF5|ESP32|USB',text):found('pulse')
        if ref.startswith(('U','Q')) or re.search(r'REGULATOR|CHARGER|MCP738|AP2112|TP4056',text):found('power')
    # This signature protects concurrent edits, not the lifetime of product-use
    # answers. Enclosure and intended use survive unrelated board revisions.
    from .memory_review import review_memory
    memory=review_memory(data)
    features['digital'].extend(d['reference']+' · '+d['kind'] for d in memory['devices'])
    signature=hashlib.sha256(json.dumps([inventory,context.get('description',''),context.get('operating_context',{})],sort_keys=True).encode()).hexdigest()[:10]
    def question_key(kind):
        return f'environment:scope_{kind}:'+hashlib.sha256(('product-use-v1:'+kind).encode()).hexdigest()[:10]
    def saved_answer(kind):
        key=question_key(kind)
        if key in answers:return answers[key]
        legacy=f'environment:scope_{kind}:{signature}'
        if legacy in answers:return answers[legacy]
        previous=[v for k,v in answers.items() if k.startswith(f'environment:scope_{kind}:')]
        return previous[-1] if previous else ''
    labels={
        'radio':('Will this product use wireless communication, such as Bluetooth or Wi-Fi?',
                 'A radio-capable chip can be fitted even when its radio is unused. Only you know the intended firmware and any external radio modules.'),
        'external':('Can someone touch the controls, plug in a cable, or reach a connector during normal use?',
                    'This sets the ESD and connector-protection review. A wired product can still need these checks.'),
        'digital':('Will these connections carry fast digital signals or connect to a cable?',
                   'The files show pin names, but do not establish signal edge speed, cable length or the selected interface mode.'),
        'analog':('Does the product need to measure very small signals accurately?',
                  'This sets the priority of noise and coupling review. Examples include microphone, electrode and precision sensor inputs.'),
        'pulse':('Can a motor, bright LED, radio or other load switch on suddenly while the product is running?',
                 'The files cannot establish simultaneous peak current, timing or startup behavior. USB plug-in behavior is reviewed separately.'),
    }
    if inventory:
        labels['wet']=('Will the product be used outdoors, near water, or where condensation can form?',
                       'Your files cannot show rain, splashes or condensation. This determines whether moisture, corrosion and enclosure protection need a separate review.')
    if features['power']:
        labels['sealed']=('Will the electronics sit in a closed case with little airflow?',
                          'The PCB files do not show the final enclosure. A closed case makes power loss, ambient temperature and heat removal important review inputs.')
    # Ask follow-ups only when their parent use case has been confirmed. The
    # identity of these questions remains stable while the answer set changes.
    def yes(kind):
        value=saved_answer(kind)
        return value.casefold().strip() in (YES.casefold(),'yes')
    if yes('radio'):
        labels['radio_case']=('Will metal, a battery or a person’s body be close to the antenna?',
                              'This adds an antenna-surroundings review. The final enclosure, battery position and wearing position are not established by the schematic.')
    if yes('external'):
        labels['hotplug']=('Can users plug in or unplug these cables while either device is powered?',
                          'This adds hot-plug, back-powering and startup-current review. Connector wiring alone does not establish this behavior.')
        labels['long_cable']=('Will any connected cable leave the enclosure and run beside motors, power wiring or other equipment?',
                             'This adds external cable noise and protection review. Cable routing and surroundings are outside the PCB files.')
    if yes('pulse'):
        labels['simultaneous']=('Can these loads turn on together, for example a radio transmission while the motor or lights are on?',
                               'This adds combined peak-load review. Individual part currents do not establish the worst operating combination.')
    questions=[];declared={};basis={}
    for kind,(question,hint) in labels.items():
        key=question_key(kind)
        answer=saved_answer(kind)
        declared[kind]=True if answer.casefold().strip() in (YES.casefold(),'yes') else False if answer.casefold().strip() in (NO.casefold(),'no') else None
        related={'wet':'external','sealed':'power','radio_case':'radio','hotplug':'external','long_cable':'external','simultaneous':'pulse'}.get(kind,kind)
        basis[kind]='; '.join(features[related][:8]) or 'This operating condition is not recorded in the PCB or schematic.'
        questions.append(dict(id=key,reference='Product use',question=question,hint=hint,
            known=basis[kind],evidence=features[related][:8],source='Saved component and pin inventory',
            choices=[YES,NO,UNKNOWN],choice_labels=['Yes','No','Not sure yet'],answer=answer,review_focus=hint,feature=kind))
    rows=[]
    def add(key,title,selection,why,features_used,available,limits):
        rows.append(dict(id=key,title=title,selection=selection,reason=why,
            evidence=[v for f in features_used for v in features[f]][:10],
            available=available,limits=limits,
            result='not_selected' if selection=='not_applicable' else 'not_verified'))
    if declared['radio'] is False:
        radio='not_applicable';radio_reason='You said wireless is unused. Antenna performance is outside this operating case; unused radio-pin requirements still need datasheet review.'
    elif declared['radio'] is True:
        radio='relevant';radio_reason='You said this product uses wireless communication.'
    elif features['radio'] or re.search(r'wireless|bluetooth|wi-?fi|radio',context.get('description',''),re.I):
        radio='needs_context'
        radio_reason=('Radio hardware was identified, but its intended use is not confirmed. Confirm use before recommending an antenna change.' if features['radio'] else
                      'The product description mentions wireless, but this scan did not identify its hardware. Confirm whether a radio or external module is used before choosing antenna checks.')
    else:
        radio='not_applicable';radio_reason='No radio or antenna was identified. Antenna checks are not selected provisionally; include them if an external module or unrecognized radio is used.'
    add('antenna','Antenna & RF',radio,radio_reason,['radio'],
        'Saved RF pin/net paths and supported sourced pin requirements.',
        'No antenna tuning, radiation, matching or RF impedance simulation.')
    add('emc','Electromagnetic compatibility (EMC)','relevant' if inventory else 'needs_context',
        'Wired cables, clocks and switching circuits can matter too. Product name alone never disables EMC review.',
        ['digital','external','power'],'Circuit evidence can be supplied to AI for a targeted review.',
        'No emissions/immunity simulation or compliance measurement; AI advice is not a verified EMC result.')
    add('esd','ESD & exposed connections','relevant' if declared['external'] is True else 'needs_context',
        'User-accessible connections were confirmed.' if declared['external'] is True else
        'Assembly/service exposure and enclosure details still matter, even if no controls are exposed during normal use.',
        ['external'],'Saved connector nets, datasheet requirements and a positive-pulse clamp/inductance margin calculation with entered limits.',
        'The clamp estimate is conditional. No physical discharge-path, negative-pulse or system ESD immunity verification.')
    coupling=declared['digital'] is True or declared['analog'] is True or bool(features['digital'] or features['analog'])
    add('crosstalk','Crosstalk','relevant' if coupling else 'needs_context',
        'Digital or sensitive analog candidates need signal-speed and noise-limit context.' if coupling else
        'Signal edge speeds and sensitivity are not established. This is a question, not a wiring fault.',
        ['digital','analog'],'Nearby parallel straight tracks are measured on the refilled snapshot. Saved connections and trace lengths are available.',
        'No coupled-field or crosstalk-voltage calculation.')
    add('thermal','Heating','relevant' if inventory else 'needs_context',
        'Power dissipation, ambient temperature and enclosure determine the needed thermal analysis; low complexity does not prove low heating.',
        ['power'],'Per-rail current budgets and steady-state junction-temperature estimates with entered loss and assembly-specific thermal resistance.',
        'Temperature estimates depend on supplied thermal assumptions. No thermal-field, transient heating or whole-board component-derating verification.')
    add('returns','Return-current paths','relevant' if inventory else 'needs_context',
        'Signal and power current need return paths. Review priority depends on edge speed and load behavior.',
        ['digital','analog','pulse'],'Native shorts/open-copper checks, straight-trace DC loss calculations, and projected ground-fill coverage on adjacent layers.',
        'Centerline coverage does not establish plane connectivity, a complete return path, current distribution or loop inductance.')
    add('transients','Pulse currents & startup','relevant' if features['pulse'] or declared['pulse'] is True else 'needs_context',
        'Switching-load or cable candidates were identified; startup and simultaneous peaks need operating context.' if features['pulse'] else
        'Startup and load timing are not encoded in ordinary KiCad files.',
        ['pulse','external'],'Simultaneous load budgets and a capacitor/ESR/inductance supply-drop estimate with entered load-step limits.',
        'No regulator control-loop, inrush waveform, power sequencing, recovery overshoot or pulse-survival simulation.')
    if memory['devices']:
        add('memory','Memory interfaces (RAM / DDR)','relevant',
            'Memory pin roles or declared identities were found. Controller and memory requirements must be reviewed together.',
            ['digital'],'Pin-role inventory, shared signal nets, signal-to-supply connections and peers reachable through fitted series resistors/inductors.',
            'No memory timing, byte-lane mapping, termination, impedance, signal-integrity simulation or training verification. Exact parts, operating speed and stackup are required.')
    # Ask the high-value questions first, while keeping every choice available
    # on the coverage page for unrecognized parts and external subsystems.
    needed={'radio'} if radio=='needs_context' or features['radio'] else set()
    needed.update(k for k in ('external','digital','analog','pulse') if features[k])
    if features['power'] and not features['external']:needed.add('external')
    special=[]
    for kind,title in [('wet','Moisture and outdoor exposure'),('sealed','Heat inside the enclosure'),
                       ('radio_case','Objects close to the antenna'),('hotplug','Connecting cables while powered'),
                       ('long_cable','External cable noise'),('simultaneous','Loads switching on together')]:
        if kind not in labels:continue
        needed.add(kind)
        question=next(q for q in questions if q['feature']==kind)
        special.append(dict(id=kind,title=title,
            selection='relevant' if declared[kind] is True else 'not_applicable' if declared[kind] is False else 'needs_context',
            reason=question['hint'] if declared[kind] is True else 'You said this condition does not apply.' if declared[kind] is False else question['question'],
            available='Your operating answer is included in review, chat and repair evidence.',
            limits='This selects additional review work; no environmental, thermal or electrical test has been performed.',
            evidence=question['evidence'],result='not_selected' if declared[kind] is False else 'not_verified'))
    return dict(version=1,signature=signature,description=context.get('description',''),memory_review=memory,declared_features=declared,
        context_revision=context.get('revision',0),checks=rows,special_reviews=special,questions=questions,
        intake_questions=[q for q in questions if q['feature'] in needed],
        note='Scope selection uses saved parts and your declarations. It is not a pass result. Missing inputs stay unresolved; no simulator is implied.')

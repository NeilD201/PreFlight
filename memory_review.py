"""Memory interface screening from pin roles and net topology, never timing sign-off.

No board name, reference designator, DDR generation or routing tolerance is assumed.
"""
from collections import Counter, defaultdict
import re

GUIDE = 'https://www.ti.com/lit/an/sprad06c/sprad06c.pdf'
LIMITS = (
    'Pin names identify candidates; confirm the exact controller and memory ordering codes.',
    'Series paths include fitted two-pin resistors, inductors and ferrites only. Buffers, resistor arrays and AC coupling need separate interpretation.',
    'No byte-lane mapping, permitted bit swaps, termination values, delay/skew, impedance, eye diagram or memory training is verified.',
    'Total drawn copper length is not end-to-end delay. Package delays, vias, layers and branches matter.',
    'Controller-specific DDR limits must not be copied to another memory interface.',
)


def project_data(project):
    return {'components':{ref:{
        'schematic':[{k:getattr(r,k,False if k=='dnp' else '') for k in ('value','symbol','dnp')} for r in c.schematic],
        'pcb':[{k:getattr(r,k,False if k=='dnp' else '') for k in ('value','dnp')} for r in c.pcb],
        'schematic_pins':[{k:getattr(p,k,False if k=='no_connect' else '') for k in ('number','name','net','no_connect','electrical_type')} for p in c.schematic_pins]}
        for ref,c in project.components.items()}, 'connectivity':project.connectivity}


def review_project(project):
    return review_memory(project_data(project))


def role(name):
    # Match complete pin-role tokens, including DDR0_DQ3 and active-low ~{WE}.
    text=re.sub(r'[~{}#]', '', str(name).upper())
    tokens=re.split(r'[/\s]+',text)
    for token in tokens:
        token=re.sub(r'^(?:DDR\d*|LPDDR\d*|SDRAM|RAM|EMIF\d*)_', '', token)
        if re.fullmatch(r'(?:DQ|D|IO)\d+',token):return 'data'
        if re.fullmatch(r'(?:DQS|RDQS|LDQS|UDQS)\d*(?:_?[TCPN]|[+-])?',token):return 'strobe'
        if re.fullmatch(r'(?:CK|CLK)\d*(?:_?[TCPN]|[+-])?',token):return 'clock'
        if re.fullmatch(r'(?:A|BA|BG)\d+',token):return 'address'
        if re.fullmatch(r'(?:RAS|CAS|WE|OE|CE|CS|CKE|ODT|RESET|DM|DQM)\d*(?:_?[NP])?',token):return 'control'
        if re.fullmatch(r'(?:VREF(?:CA|DQ)?|VTT|ZQ)\d*',token):return 'reference_or_calibration'
    return None


def review_memory(data):
    fitted={}
    for ref,c in data.get('components',{}).items():
        records=c.get('schematic') or c.get('pcb') or []
        if not records or all(r.get('dnp') for r in records):continue
        fitted[ref]=c
    nets=defaultdict(list);bridges=defaultdict(set);candidates=[]
    for ref,c in fitted.items():
        pins=c.get('schematic_pins',[])
        for pin in pins:
            if pin.get('net') and not pin.get('no_connect'):
                nets[pin['net']].append({'reference':ref, **pin})
        # Do not cross active parts, shunt capacitors, resistor arrays or rails.
        if re.fullmatch(r'(?:R|L|FB)\d+',ref) and len(pins)==2 and all(p.get('net') and not p.get('no_connect') for p in pins):
            a,b=(p['net'] for p in pins)
            bridges[a].add(b);bridges[b].add(a)
        identities=' '.join(str(r.get('value',''))+' '+str(r.get('symbol','')) for r in c.get('schematic') or c.get('pcb') or [])
        signals=[{'number':p.get('number',''),'name':p.get('name',''),'net':p.get('net',''),
                  'no_connect':p.get('no_connect',False),'role':role(p.get('name',''))} for p in pins if role(p.get('name',''))]
        counts=Counter(p['role'] for p in signals)
        declared=bool(re.search(r'\b(?:LPDDR[2-5X]*|DDR[2-5]*|SDRAM|SRAM|PSRAM)\b|Memory_RAM[:_]',identities,re.I))
        parallel=counts['data']>=4 and (counts['strobe'] or counts['address']>=3 and counts['control']>=2)
        if declared or parallel:
            family=('Memory interface (type unconfirmed)' if not parallel else
                    'DDR-like (generation unconfirmed)' if counts['strobe'] else
                    'Synchronous parallel memory candidate' if counts['clock'] else 'Parallel memory candidate')
            candidates.append({'reference':ref,'identity':identities.strip(),'kind':family,
                               'identification':'Declared memory identity' if declared else 'Pin-role pattern; controller or memory',
                               'signals':signals,'role_counts':dict(counts)})
    supply_nets={net for net,members in nets.items() if any(p.get('electrical_type') in ('power_in','power_out') for p in members)}
    observations=[];candidate_refs={c['reference'] for c in candidates}
    def peers(net,ref):
        seen=set();todo=[net];found={}
        while todo and len(seen)<128:
            current=todo.pop()
            if current in seen:continue
            seen.add(current)
            if current in supply_nets:continue
            for p in nets[current]:
                other=p['reference']
                if other!=ref and not re.fullmatch(r'(?:R|C|L|FB)\d+',other) and p.get('electrical_type') not in ('power_in','power_out'):
                    found[(other,p.get('number',''))]={'reference':other,'number':p.get('number',''),
                        'name':p.get('name',''),'role':role(p.get('name',''))}
            todo.extend(bridges[current]-seen)
        return [found[key] for key in sorted(found)], bool(todo)
    for candidate in candidates:
        ref=candidate['reference'];by_net=defaultdict(list)
        for p in candidate['signals']:
            if p['role'] not in ('data','strobe','clock'):continue
            net=p['net'];endpoints,limited=peers(net,ref) if net and not p['no_connect'] else ([],False)
            p['peers']=sorted({endpoint['reference'] for endpoint in endpoints})
            p['peer_pins']=endpoints
            p['peer_search_incomplete']=limited
            if net and not p['no_connect']:by_net[net].append(p)
            reason=('marked_no_connect' if p['no_connect'] else 'no_net' if not net else
                    'supply_connection' if net in supply_nets else 'search_incomplete' if limited else
                    'no_signal_peer' if not p['peers'] else None)
            if reason:
                observations.append({'reference':ref,'pins':[p['number']],'net':net,'reason':reason,
                    'detail':f"{ref} {p['name']} (pin {p['number']}): {reason.replace('_',' ')}.",
                    'status':'needs_review'})
            incompatible=[endpoint for endpoint in endpoints if endpoint['reference'] in candidate_refs
                          and endpoint['role'] in ('data','clock','strobe') and endpoint['role']!=p['role']]
            if incompatible:
                observations.append({'reference':ref,'pins':[p['number']],'net':net,
                    'reason':'signal_role_mismatch','detail':f"{ref} {p['name']} ({p['role']}) reaches "+
                    ', '.join(f"{e['reference']} pin {e['number']} {e['name']} ({e['role']})" for e in incompatible),
                    'status':'needs_review'})
        for net,pins in by_net.items():
            if len({p['name'] for p in pins})>1:
                observations.append({'reference':ref,'pins':[p['number'] for p in pins],'net':net,
                    'reason':'shared_signal_net','detail':f"{ref}: distinct data/clock/strobe pins share {net}: "+', '.join(p['name'] for p in pins),
                    'status':'needs_review'})
    requirements=[]
    if candidates:
        requirements=[
            {'id':'identity','title':'Exact controller, memory and configured bus width','status':'not_verified'},
            {'id':'mapping','title':'Data lanes, strobes, address pins and permitted swaps','status':'not_verified'},
            {'id':'power','title':'Supply rails, reference voltages, termination and decoupling','status':'not_verified'},
            {'id':'timing','title':'Read/write timing at the chosen memory speed','status':'not_verified'},
            {'id':'layout','title':'Topology, stackup, impedance and return paths','status':'not_verified'},
            {'id':'simulation','title':'Signal/power integrity and operating margin','status':'not_verified'},
        ]
    return {'status':'needs_review' if candidates else 'no_candidate_identified',
            'devices':candidates,'observations':observations,'requirements':requirements,
            'limits':list(LIMITS),'guide':{'title':'TI AM62x DDR layout guide — methodological example, not universal limits','url':GUIDE},
            'unrecognized_parts':sum(not c.get('schematic_pins') for c in fitted.values()),
            'note':'No candidate identified is not a verified absence. Custom symbols and unrecognized interfaces may need explicit identification.'}


def add_findings(project):
    from .models import Issue
    project.issues=[i for i in project.issues if i.source!='memory_screening']
    review=review_project(project)
    by_ref=defaultdict(list)
    for item in review['observations']:by_ref[item['reference']].append(item)
    for ref,items in by_ref.items():
        project.issues.append(Issue('warning','MEMORY_CONNECTION_REVIEW',
            f'{ref}: {len(items)} memory signal connection observations need interpretation.',
            [i['detail'] for i in items],source='memory_screening',component=ref,
            suggested_fix='Compare these pins with the exact memory/controller pinout and configured bus width before proposing a wire change.',
            details={'observations':items}))
    if review['devices'] and 'memory_connection_screening' not in project.checks_performed:
        project.checks_performed.append('memory_connection_screening')
    return review

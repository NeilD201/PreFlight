"""Trace saved circuit connections, with explicit boundaries at active devices.

These are schematic paths and inferred block roles, not current-flow simulation.
Unknown ICs are endpoints: arbitrary pins are never joined through an IC.
"""
from collections import defaultdict
import hashlib
import re
from .checks.circuit import is_ground
from .checks.components import reference_key


def active(component):
    records=component.schematic or component.pcb
    return bool(records and component.schematic_pins and not any(r.dnp for r in component.schematic+component.pcb))


def usable(net):
    return bool(net and not net.startswith('unconnected-('))


def pin_name(name):
    return re.sub(r'[^A-Z0-9/+]', '', name.upper().split('@')[0])


def role(value):
    value=value.upper()
    for pattern,label in [(r'^MCP7383','battery charger'),(r'^AP2112','power regulator'),
        (r'^NRF52','radio controller'),(r'^MAX30001','ECG sensor'),(r'^MAX3010','optical pulse sensor'),
        (r'^MAX3020','temperature sensor'),(r'^LSM6','motion sensor'),(r'^BMP3','pressure sensor'),
        (r'^MAX1704','battery monitor'),(r'^DW01','battery protection controller'),(r'^DRV2605','vibration driver')]:
        if re.search(pattern,value):return label
    return 'component'


def build_routing_chains(project):
    if project.connectivity.get('status')!='compared':
        return {'status':'unavailable','chains':[],'message':'A successful schematic netlist and PCB comparison is needed to trace the circuit.'}
    components={r:c for r,c in project.components.items() if active(c)}
    endpoints=defaultdict(list)
    def node(ref,pin):
        c=components[ref];record=(c.schematic or c.pcb)[0]
        pads=[p for r in c.pcb for p in r.pads if p.number==pin.number]
        nets=sorted({p.net for p in pads})
        return {'reference':ref,'pin':pin.number,'name':pin.name,'net':pin.net,
            'value':record.value,'role':role(record.value),'electrical_type':pin.electrical_type,
            'pcb_nets':nets,'board_matches':bool(pads) and nets==[pin.net],
            'evidence_ids':['C:'+ref,'N:'+pin.net], 'source':record.location}
    for ref,c in components.items():
        for p in c.schematic_pins:
            if usable(p.net) and not p.no_connect:endpoints[p.net].append(node(ref,p))
    debug_headers=set()
    for ref,c in components.items():
        if not ref.startswith('J'):continue
        peer_names={pin_name(e['name']) for p in c.schematic_pins for e in endpoints[p.net] if e['reference']!=ref}
        if {'SWDIO','SWDCLK'} <= peer_names:debug_headers.add(ref)
    stages=[];incoming=defaultdict(list);returns=set()
    targets=defaultdict(list)
    for ref,c in components.items():
        for p in c.schematic_pins:
            if pin_name(p.name) in {'GND','AGND','DGND','PGND','VSS','VSS/GND','VSSPA'} and usable(p.net):returns.add(p.net)
    def ground(net):return is_ground(net) or net in returns
    for ref,c in components.items():
        record=(c.schematic or c.pcb)[0];names=defaultdict(list)
        for p in c.schematic_pins:
            if usable(p.net) and not p.no_connect:names[pin_name(p.name)].append(p)
        pairs=[]
        same_identity=len({r.value.strip().upper() for r in c.schematic+c.pcb})==1
        if same_identity and re.match(r'^AP2112',record.value,re.I):pairs=[('VIN','VOUT','regulator','The declared part is a regulator: VIN is its input and VOUT is its output.')]
        elif same_identity and re.match(r'^MCP7383',record.value,re.I):pairs=[('VDD','VBAT','charger','The declared charger takes power at VDD and connects its charging output to VBAT.')]
        for first,last,kind,why in pairs:
            a,b=names.get(first,[]),names.get(last,[])
            if not a or not b or len({p.net for p in a})!=1 or len({p.net for p in b})!=1:continue
            stage={'reference':ref,'value':record.value,'kind':kind,'input_net':a[0].net,'output_net':b[0].net,
                'input_pins':[node(ref,p) for p in a],'output_pins':[node(ref,p) for p in b],
                'why':why,'basis':'Declared part identity and saved pin roles; operating state is unverified',
                'control_pins':[node(ref,p) for p in c.schematic_pins if usable(p.net) and not p.no_connect and p not in a+b and not ground(p.net) and pin_name(p.name)!='NC'],
                'return_nets':sorted({p.net for p in c.schematic_pins if ground(p.net)}),
                'evidence_ids':['C:'+ref,'N:'+a[0].net,'N:'+b[0].net],
                'status':'input_output_joined' if a[0].net==b[0].net else 'separate_input_output'}
            stages.append(stage);incoming[b[0].net].append(stage)
        for p in c.schematic_pins:
            if not usable(p.net) or p.no_connect or ground(p.net):continue
            if p.electrical_type=='power_in' or pin_name(p.name) in {'VDD','VDDIO','AVDD','DVDD','OVDD','VCC','VIN','VDDH','VLED+','VBUS'}:
                targets[p.net].append(node(ref,p))
    supply_nets=set(targets)|{s['output_net'] for s in stages}
    for stage in stages:
        if stage['kind']=='regulator':
            loads=sorted({p['role'] for p in targets[stage['output_net']] if p['reference']!=stage['reference']})
            stage['why']='Takes its input from '+stage['input_net']+' and supplies '+stage['output_net']+' to '+(', '.join(loads) or 'the connected circuit')+'. The declared regulator and its VIN/VOUT pins establish this intended role; voltage and enabled state still need verification.'
    bridges=defaultdict(list);branches=defaultdict(list)
    for ref,c in components.items():
        if not re.fullmatch(r'(?:R|L|FB|C)\d+',ref) or len(c.schematic_pins)!=2:continue
        a,b=c.schematic_pins
        if not usable(a.net) or not usable(b.net) or a.no_connect or b.no_connect or a.net==b.net:continue
        for first,second in [(a,b),(b,a)]:
            if ground(first.net):continue
            kind=('capacitor to return' if ref.startswith('C') and ground(second.net) else
                  'capacitor between nets' if ref.startswith('C') else
                  'resistor to supply' if ref.startswith('R') and second.net in supply_nets and first.net not in supply_nets else
                  'resistor to return' if ref.startswith('R') and ground(second.net) else 'series component')
            branch={'reference':ref,'value':(c.schematic or c.pcb)[0].value,'kind':kind,
                    'from':node(ref,first),'to':node(ref,second),
                    'why':{'capacitor to return':'A side branch to the return net; this does not create a DC power route.',
                           'capacitor between nets':'A capacitor joins these nets; signal coupling depends on frequency and circuit requirements.',
                           'resistor to supply':'This may pull the signal toward its logic supply. Its resistance and bus requirements still matter.',
                           'resistor to return':'This connects the signal to its return through a resistor; it may be a bias or termination.',
                           'series component':'The two saved pins connect these separate nets through this fitted part.'}[kind]}
            branches[first.net].append(branch)
            if re.fullmatch(r'(?:R|L|FB)\d+',ref) and not ground(second.net):bridges[first.net].append(branch)
    limits=[]
    def upstream(net,seen=frozenset()):
        if net in seen:return [{'steps':[],'source_status':'loop','question':'This power dependency returns to a previously visited net.'}]
        if len(seen)>=8:return [{'steps':[],'source_status':'limit','question':'The upstream search reached its eight-stage limit.'}]
        seen=seen|{net};routes=[]
        for endpoint in endpoints[net]:
            if endpoint['reference'].startswith('J') and endpoint['reference'] not in debug_headers:
                routes.append({'steps':[{'kind':'external_connection','net':net,'pins':[endpoint],
                    'why':'A connector pin reaches this net. The external device and actual voltage are not established by the file.'}],
                    'source_status':'external_connection','question':'What is attached to this connector? Its role can be supplied in device context.'})
        for stage in incoming[net]:
            if stage['input_net']==net:
                routes.append({'steps':[{'kind':'stage',**stage}],'source_status':'joined_input_output','question':'Input and output share one wire, so the file does not show a separate regulated output.'})
                continue
            for route in upstream(stage['input_net'],seen):
                routes.append({**route,'steps':route['steps']+[{'kind':'stage',**stage}]})
        # Follow DC passives upstream when no converter or connector identifies the source.
        if not routes:
            for bridge in bridges[net]:
                if bridge['to']['net'] in seen:continue
                for route in upstream(bridge['to']['net'],seen):
                    routes.append({**route,'steps':route['steps']+[{'kind':'series','reference':bridge['reference'],
                        'value':bridge['value'],'input_net':bridge['to']['net'],'output_net':net,
                        'input_pins':[bridge['to']],'output_pins':[bridge['from']],'why':bridge['why']}]})
        if not routes:return [{'steps':[],'source_status':'not_identified','question':'No connector or supported converter path was identified upstream. An external or internal source may need more context.'}]
        if len(routes)>4:limits.append('Some alternative upstream routes were omitted (four routes per supply).')
        return routes[:4]
    signal_bridges=defaultdict(list)
    for net,edges in bridges.items():signal_bridges[net].extend(edges)
    for net,edges in branches.items():
        if net in supply_nets:continue
        signal_bridges[net].extend(e for e in edges if e['kind']=='capacitor between nets' and e['to']['net'] not in supply_nets and not ground(e['to']['net']))
    chains=[]
    def add(kind,title,nets,**data):
        names=list(dict.fromkeys(nets));refs=sorted({n['reference'] for name in names for n in endpoints[name]},key=reference_key)
        chains.append({'id':'T:'+hashlib.sha256((kind+'|'+ '|'.join(names)+'|'+title).encode()).hexdigest()[:16],
            'kind':kind,'title':title,'nets':names,'components':refs,
            'evidence_ids':['N:'+name for name in names]+['C:'+ref for ref in refs],**data})
    for net,loads in sorted(targets.items()):
        routes=upstream(net)
        nets=[net]+[step.get(k) for route in routes for step in route['steps'] for k in ('net','input_net','output_net') if step.get(k)]
        add('power',f'Power reaching {net}',nets,target_net=net,routes=routes,loads=loads,
            branches=[b for name in dict.fromkeys(nets) for b in branches[name]],
            explanation='Follow each possible source through the declared charger/regulators or fitted series parts to the supply pins. Ground and signal branches are shown separately.',
            checks=[{'status':s['status'],'reference':s['reference'],'detail':s['why']} for s in stages if s['output_net'] in nets])
    # Signal paths stop at active IC pins. They never pass through arbitrary IC pins.
    visited=set()
    candidates=[n for n in endpoints if not ground(n) and n not in supply_nets]
    for seed in candidates:
        if seed in visited:continue
        queue=[seed];nets=[]
        while queue and len(nets)<12:
            net=queue.pop(0)
            if net in nets:continue
            nets.append(net)
            for bridge in signal_bridges[net]:
                other=bridge['to']['net']
                if other not in supply_nets and not ground(other) and other not in nets:queue.append(other)
        visited.update(nets)
        ends=[n for name in nets for n in endpoints[name] if not re.fullmatch(r'(?:R|L|FB|C)\d+',n['reference'])]
        if len({e['reference'] for e in ends})<2 and not any(pin_name(e['name'])=='ANT' for e in ends):continue
        rf=any(pin_name(e['name'])=='ANT' for e in ends)
        bus=seed in {'SCL','SDA'}
        title=('Radio → matching parts → endpoint' if rf else f'{seed} · shared sensor bus' if bus else f'{seed} · signal connections')
        add('rf' if rf else 'bus' if bus else 'signal',title,nets,endpoints=ends,
            series=[b for name in nets for b in signal_bridges[name] if b['to']['net'] in nets and name < b['to']['net']],
            branches=[b for name in nets for b in branches[name] if b['to']['net'] not in nets],
            explanation=('The ANT pin reaches the fitted series part and the capacitor branches shown below. The antenna endpoint and RF performance still need supporting evidence.' if rf else
                         'Follow these distinct wires through the listed parts to their endpoints. Capacitors between signal nets indicate possible AC coupling, not DC continuity. Direction depends on pin function and operating mode; firmware-controlled pins are not assumed to be outputs.'),
            complete=not queue)
    for ref,c in components.items():
        return_nets=sorted({p.net for p in c.schematic_pins if ground(p.net) and usable(p.net)})
        if not ref.startswith('Q') or len(return_nets)<2:continue
        controls=[p.net for p in c.schematic_pins if usable(p.net) and not ground(p.net) and
                  any(e['electrical_type']=='output' for e in endpoints[p.net])]
        add('return','Return connections through '+ref,return_nets+controls,
            endpoints=[e for name in return_nets+controls for e in endpoints[name] if e['reference']==ref or e['reference'].startswith(('J','U'))],
            series=[],branches=[],
            explanation='The separate return nets reach different pins on this switching part. The listed controller outputs reach its control connections. The schematic cannot establish which internal switches conduct; compare the exact switch pin table and intended protection state.',complete=False)
    return {'status':'traced','chains':chains,'stages':stages,
        'basis':'Exported schematic pins and nets; each listed pin includes its PCB pad comparison.',
        'limitations':list(dict.fromkeys(limits))+[
            'Part roles and internal converter direction are inferred from declared identities and saved pin names. Datasheet and operating-mode review must confirm them.',
            'A path through a converter is a functional dependency, not a direct copper connection between its pins.',
            'Connections and drawn copper do not measure actual voltage, current, impedance, noise or switch state.',
            'Capacitors to ground are side branches; unrecognized ICs are endpoints. Internal switching paths and ground returns are not guessed.']}


def routing_evidence(routing):
    """Compact path facts for AI; full pin records already exist in its packet."""
    endpoint=lambda n: {'reference':n['reference'],'pin':n['pin'],'net':n['net'],'pcb_matches':n['board_matches']}
    edge=lambda s: {'reference':s['reference'],'value':s['value'],'kind':s['kind'],
        'input_net':s['input_net'],'output_net':s['output_net'],
        'input_pins':[p['pin'] for p in s['input_pins']],'output_pins':[p['pin'] for p in s['output_pins']],
        'why':s['why'],'control_pins':[endpoint(p) for p in s.get('control_pins',[])]}
    chains=[]
    for c in routing.get('chains',[]):
        row={k:c[k] for k in ('id','kind','title','nets','components')}
        row['endpoints']=[endpoint(p) for p in c.get('loads',c.get('endpoints',[]))]
        row['routes']=[{'source_status':r['source_status'],'steps':[
            {'kind':'external_connection','pins':[endpoint(p) for p in s['pins']]} if s['kind']=='external_connection' else edge(s)
            for s in r['steps']]} for r in c.get('routes',[])]
        row['series']=[{'reference':s['reference'],'value':s['value'],'kind':s['kind'],'from_net':s['from']['net'],'to_net':s['to']['net']} for s in c.get('series',[])]
        row['branches']=[{'reference':b['reference'],'kind':b['kind'],'from_net':b['from']['net'],'to_net':b['to']['net']} for b in c.get('branches',[])]
        chains.append(row)
    return {'status':routing['status'],'chains':chains,'limitations':routing.get('limitations',[])}

"""Deterministic retrieval from the current saved project; no extra model call."""
import re
from .datasheets import excerpts

STOP=set('a an the it is are was were how what why which where when can could should would do does did i me my we you your this that these those to of for on in with and or about explain please tell simply using saved project board connect connects connected connecting used use needs needed'.split())
SYNONYMS={
    'battery':{'battery','bat','vbat','li','charger','charging'},
    'charger':{'charger','charging','charge','charged','battery'},
    'charged':{'charger','charging','charge','charged','battery'},
    'antenna':{'antenna','ant','radio','rf','wireless'},
    'radio':{'radio','rf','antenna','wireless'},
    'temperature':{'temperature','temp','sensor'},
    'ground':{'ground','gnd','vss'},
    'noise':{'noise','coupling','crosstalk','return'},
    'vias':{'via','vias','drill','filled','capped'},
    'power':{'power','supply','voltage','regulator'},
}


def words(text):
    return set(re.findall(r'[a-z0-9]+',str(text).lower()))-STOP


def search_project(project, question, history=(), limit=12):
    query=words(question)
    # Use earlier user text only for an actual follow-up, not to override a new topic.
    if not query or re.search(r'\b(that|it|those|these|further|more)\b',question,re.I):
        for message in reversed(history):
            if message.get('role')=='user':query.update(words(message.get('text','')));break
    expanded=set(query)
    for token in query:expanded.update(SYNONYMS.get(token,()))
    records=[]
    def add(kind, identity, value, text, refs=()):
        found=expanded & words(text)
        exact=identity.lower() in query
        score=(100 if exact else 0)+len(found)+4*len(query & found)
        records.append({'kind':kind,'id':identity,'references':list(refs),
                        'matched_terms':sorted(found),'evidence':value,'_score':score})
    for group in project.review.get('groups',[]):
        value={k:group.get(k) for k in ('id','title','summary','why','components','evidence')}
        action=next((a for a in project.action_plan.get('actions',[]) if a.get('group_id')==group['id']),{})
        value['pin_checks']=action.get('pin_checks',[])
        add('issue',group['id'],value,str(value),group.get('components',[]))
    for ref,component in project.components.items():
        record=(component.schematic or component.pcb)
        value={'reference':ref,'value':record[0].value if record else '',
               'footprint':record[0].footprint if record else '',
               'pins':[{'number':pin.number,'name':pin.name,'net':pin.net} for pin in component.schematic_pins]}
        add('component',ref,value,str(value),[ref])
    for name,pins in project.schematic_nets.items():
        add('net',name,{'name':name,'pins':pins},name+' '+str(pins))
    for doc in excerpts(project):
        value={k:doc.get(k) for k in ('evidence_id','references','mpn','part_match','page','text','url')}
        add('datasheet',doc['evidence_id'],value,str(value),doc.get('references',[]))
    for chain in project.circuit.get('routing_chains',{}).get('chains',[]):
        add('circuit_path',chain['id'],chain,str(chain),chain.get('components',[]))
    # A matched datasheet identifies its actual fitted parts. Include those parts
    # even when their numeric MPN has no everyday words such as 'charger'.
    best_docs=sorted([r for r in records if r['kind']=='datasheet' and r['_score']>0],
                     key=lambda r:-r['_score'])[:3]
    linked_refs={ref for r in best_docs for ref in r['references']}
    for record in records:
        if record['kind']=='component' and record['id'] in linked_refs:
            record['_score']+=20
    records=[r for r in records if r['_score']>0]
    records.sort(key=lambda r:(-r['_score'],r['kind'],r['id']))
    # Avoid one long datasheet crowding out component and connection matches.
    chosen=[];counts={}
    for record in records:
        kind=record['kind']
        if counts.get(kind,0)>=4:continue
        chosen.append({k:v for k,v in record.items() if k!='_score'})
        counts[kind]=counts.get(kind,0)+1
        if len(chosen)>=limit:break
    return {'scope':'Current saved project only; not an internet search',
            'query':question,'results':chosen,'matching_records':len(records),
            'limited':len(chosen)<len(records)}


def connection_neighborhood(project, references, max_nets=12, max_endpoints=20):
    """Read adjacent pins even when no ERC/DRC or AI finding exists for a part.

    Extend one step through fitted two-pin R/L/FB/C parts, stopping at power
    returns and every active IC. A capacitor is a connection, not a DC short.
    """
    from .checks.circuit import is_ground
    seeds=[ref for ref in dict.fromkeys(references) if ref in project.components][:4]
    by_net={}
    for ref,c in project.components.items():
        rows=c.schematic or c.pcb
        if not rows:continue
        for pin in c.schematic_pins:
            if not pin.net or pin.no_connect or pin.net.startswith('unconnected-('):continue
            by_net.setdefault(pin.net,[]).append({'reference':ref,'pin':pin.number,'name':pin.name,
                'value':rows[0].value,'electrical_type':pin.electrical_type,'source':rows[0].location,
                'population':'not fitted' if any(r.dnp for r in c.schematic+c.pcb) else 'declared fitted'})
    direct=list(dict.fromkeys(p.net for ref in seeds for p in project.components[ref].schematic_pins
        if p.net and not p.no_connect and not p.net.startswith('unconnected-(')))
    # Prioritize signals; a large common ground must not crowd out the bus pins.
    direct.sort(key=lambda net:(is_ground(net),len(by_net.get(net,[]))>max_endpoints))
    adjacent=[];bridges=[]
    for ref,c in project.components.items():
        if not re.fullmatch(r'(?:R|L|FB|C)\d+',ref) or len(c.schematic_pins)!=2:continue
        if any(r.dnp for r in c.schematic+c.pcb):continue
        a,b=c.schematic_pins
        if (a.no_connect or b.no_connect or not a.net or not b.net or a.net==b.net
                or a.net.startswith('unconnected-(') or b.net.startswith('unconnected-(')):continue
        if not any(net in direct and not is_ground(net) for net in (a.net,b.net)):continue
        rows=c.schematic or c.pcb
        bridges.append({'reference':ref,'value':rows[0].value if rows else '',
            'pins':[{'pin':p.number,'net':p.net} for p in (a,b)],
            'behavior':'Frequency-dependent coupling; not a DC short' if ref.startswith('C') else 'Impedance depends on part value and operating conditions'})
        adjacent.extend(net for net in (a.net,b.net) if net not in direct)
    names=list(dict.fromkeys(direct+adjacent));selected=names[:max_nets];nets=[]
    for name in selected:
        endpoints=sorted(by_net.get(name,[]),key=lambda p:p['reference'] not in seeds)
        nets.append({'net':name,'evidence_id':'N:'+name,'endpoints':endpoints[:max_endpoints],
                     'total_endpoints':len(endpoints),'omitted_endpoints':max(0,len(endpoints)-max_endpoints)})
    selected_bridges=[b for b in bridges if any(pin['net'] in selected for pin in b['pins'])]
    return {'references':seeds,'nets':nets,'passive_connections':selected_bridges[:16],
        'connectivity_status':project.connectivity.get('status','unavailable'),
        'omitted_nets':max(0,len(names)-len(selected)), 'omitted_passive_connections':max(0,len(selected_bridges)-16),
        'basis':'Exported schematic pin/net membership; does not establish physical copper continuity or simulate component behavior',
        'boundary':'One passive hop; never traverses through an IC. Omitted connections are unknown, not absent.'}

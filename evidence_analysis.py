"""Bind native ERC findings to the exported schematic's actual pin connections."""
import re


def erc_findings(project, group):
    findings=[]
    for fid in group.get('issue_ids', []):
        if not re.fullmatch(r'F\d+', fid):continue
        index=int(fid[1:])-1
        if not 0<=index<len(project.issues):continue
        issue=project.issues[index]
        if issue.source!='kicad_erc':continue
        connections=[]
        for item in issue.items:
            match=re.match(r'Symbol (\S+) (?:Hidden pin|Pin) (\S+) \[', item.get('description',''))
            if not match:continue
            ref,number=match.groups();component=project.components.get(ref)
            pins=[p for p in component.schematic_pins if p.number==number] if component else []
            nets={p.net for p in pins}
            pin=pins[0] if pins else None
            connections.append({'reference':ref,'pin':number,
                'name':pin.name if pin else '', 'electrical_type':pin.electrical_type if pin else '',
                'net':pin.net if len(nets)==1 else None,
                'net_members':project.schematic_nets.get(pin.net,[]) if len(nets)==1 else [],
                'uuid':item.get('uuid',''), 'virtual':ref.startswith('#'),
                'description':item.get('description','')})
        findings.append({'evidence_id':fid,'code':issue.code,'message':issue.message,'connections':connections})
    return findings


def pin_targets(project, group):
    targets={}
    for finding in erc_findings(project,group):
        for connection in finding['connections']:
            if connection['virtual'] or connection['net'] is None:continue
            targets[(connection['reference'],connection['pin'])]=connection
    return list(targets.values())


def requires_pin_analysis(project, group):
    return any(f['code']=='KICAD_ERC_PIN_TO_PIN' for f in erc_findings(project,group))


def net_aliases(project, group):
    """Explain label selection separately from electrical requirements or shorts."""
    rows=[]
    for finding in erc_findings(project,group):
        if finding['code']!='KICAD_ERC_MULTIPLE_NET_NAMES':continue
        match=re.fullmatch(r"Both (.+) and (.+) are attached to the same items; (.+) will be used in the netlist",finding['message'].strip())
        if not match:continue
        first,second,chosen=(name.replace('{slash}','/') for name in match.groups())
        pins=[]
        for pin in finding['connections']:
            if pin['virtual']:continue
            component=project.components.get(pin['reference'])
            pads=[p for r in component.pcb for p in r.pads if p.number==pin['pin']] if component else []
            nets=sorted({p.net for p in pads})
            pins.append({'reference':pin['reference'],'pin':pin['pin'],'name':pin['name'],
                         'schematic_net':pin['net'],'pcb_nets':nets,
                         'board_matches':bool(pads) and pin['net'] is not None and nets==[pin['net']]})
        hidden=any('Hidden pin' in pin['description'] for pin in finding['connections'])
        rows.append({'evidence_id':finding['evidence_id'],'names':[first,second],'chosen_net':chosen,
                     'net_members':project.schematic_nets.get(chosen,[]),'pins':pins,
                     'explanation':('A hidden power pin supplies the second name. KiCad keeps the connected rail name.' if hidden else
                                    'Two labels name the same connected wire. KiCad exports it under one name.'),
                     'intent_question':('Were these meant to be separate connections? They are already joined in the schematic.' if not hidden else '')})
    return rows

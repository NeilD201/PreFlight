"""Small, package-specific schematic checks backed by manufacturer requirements.

Only direct net connections and declared nominal values are checked. No rail voltage,
physical placement, or firmware behavior is inferred from names.
"""
from decimal import Decimal
import re
from .components import _passive_value
from .circuit import part_rule, MCP_SOURCE, MAX_SOURCE


def schematic_checks(project):
    findings, checked = [], []
    usable = lambda c: bool(c.schematic and c.schematic_pins and not any(r.dnp for r in c.schematic+c.pcb))
    def net(pin):
        return pin.net if pin and pin.net and not pin.no_connect and not pin.net.startswith('unconnected-(') else None
    def passives(prefix, first, second):
        result=[]
        if not first or not second or first==second:return result
        for ref,c in project.components.items():
            if not re.fullmatch(prefix+r'\d+',ref) or not usable(c) or len(c.schematic)!=1 or len(c.schematic_pins)!=2:continue
            if {net(p) for p in c.schematic_pins}=={first,second}:
                value=_passive_value(ref,c.schematic[0].value)
                result.append((ref,value if value is not None and value.is_finite() and value>0 else None))
        return result
    for ref,c in project.components.items():
        rule=part_rule(c)
        if not rule or not usable(c) or len(c.schematic)!=1:continue
        pins={p.number:p for p in c.schematic_pins}
        source=MCP_SOURCE if rule=='mcp73831_dfn' else MAX_SOURCE
        def add(code,title,observed,requirement,steps,priority='review',numbers=()):
            findings.append({'reference':ref,'rule':'schematic:'+code,'pads':[], 'category':'electrical',
                'priority':priority,'title':f'{ref}: {title}','summary':observed,
                'why':requirement+' '+observed,'next_step':steps[0],'steps':steps,
                'evidence':[c.schematic[0].location]+[f'{ref}.{n}: {net(pins.get(n)) or "not connected"}' for n in numbers],
                'sources':[source] + ([{'title':'MAX30102 serial interface, page 28', 'url':MAX_SOURCE['url'].split('#')[0]+'#page=28'}] if code=='sda_pullup' else []), 'schematic_check':True})
        def capacitors(power,ground,label,minimum=None):
            if power not in pins or ground not in pins:return
            supply,return_net=net(pins[power]),net(pins[ground])
            if not supply or not return_net or supply==return_net:return
            caps=passives('C',supply,return_net)
            total=sum((v for _,v in caps if v is not None),Decimal(0))
            adequate=bool(caps) if minimum is None else total>=minimum
            checked.append({'reference':ref,'rule':'schematic:'+label.lower()+'_capacitor','check':label+' capacitor connection','status':'connection_found' if adequate else 'review',
                            'detail':', '.join(r for r,_ in caps) or 'No directly connected fitted capacitor found.'})
            if adequate:return
            requirement=(f'The datasheet calls for at least {float(minimum)*1e6:g} µF between {label} and ground.' if minimum else f'The datasheet recommends a capacitor between {label} and its ground pin.')
            observed=f'Between {ref} pin {power} ({supply}) and pin {ground} ({return_net}), '+(f'the readable fitted capacitor values add up to {float(total)*1e6:g} µF.' if caps else 'no fitted capacitor was found.')
            add(label.lower()+'_capacitor','check the '+label+' capacitor',observed,requirement,
                [f'Open {ref} in the schematic. Follow pin {power} and pin {ground}.',
                 'Check the capacitor connected between those lines. '+requirement+' A capacitor elsewhere on the same line may already provide it.',
                 'If the circuit uses a switch, filter, or external capacitor, check that arrangement in the datasheet before adding anything. Confirm the real capacitor value under voltage and its placement.',
                 'Save the schematic, update the PCB from it, then check the updated files here.'],numbers=(power,ground))
        power,ground=('1','6') if rule=='mcp73831_dfn' else ('11','12')
        # Only make a definite short claim when both pin identities exist in the export.
        if power in pins and ground in pins:
            short=net(pins[power]) and net(pins[power])==net(pins[ground])
            checked.append({'reference':ref,'rule':'schematic:supply_ground','check':'Supply versus ground','status':'conflict' if short else 'checked' if net(pins[power]) and net(pins[ground]) else 'unavailable',
                            'detail':'Pin connections checked; voltages are unknown.'})
            if short:add('supply_ground','power and ground are on the same wire',f'Pins {power} and {ground} both use {net(pins[power])}.',
                'These pins have different supply and ground functions in the part’s pin table.',
                [f'Find {ref} pins {power} and {ground} in the schematic.', 'Check the exact chip and symbol pin numbers, then separate the power wire from ground.',
                 'Update the PCB from the schematic and check the updated files here.'],priority='fix',numbers=(power,ground))
        if rule=='mcp73831_dfn':
            capacitors('1','6','VDD',Decimal('0.0000047'))
            capacitors('3','6','VBAT',Decimal('0.0000047'))
            if '8' in pins and '6' in pins:
                resistors=passives('R',net(pins['8']),net(pins['6']))
                valid=len(resistors)==1 and resistors[0][1] is not None and Decimal(2000)<=resistors[0][1]<=Decimal(67000)
                detail=(f'{resistors[0][0]} sets about {float(Decimal(1000000)/resistors[0][1]):g} mA nominal charge current.' if valid else 'No single readable charge-setting resistor in the supported range was confirmed.')
                checked.append({'reference':ref,'rule':'schematic:charge_resistor','check':'Charge-setting resistor','status':'connection_found' if valid else 'review','detail':detail})
                if not valid:add('charge_resistor','check how charging is enabled',detail,
                    'A resistor from PROG to VSS sets the charging current. Leaving PROG floating disables charging, which can be intentional.',
                    [f'Follow pin 8 (PROG) on {ref} in the schematic.', 'Check whether it reaches a resistor to pin 6’s ground, directly or through a switch.',
                     'If you want charging enabled, choose the resistor using the chip datasheet and the battery’s allowed charge current. If charging is deliberately disabled, explain that in the context box.'],numbers=('8','6'))
        else:
            capacitors('11','12','VDD')
            capacitors('9','4','VLED')
            if '3' in pins and net(pins['3']):
                bus=net(pins['3']);pullups=[]
                for candidate in project.circuit.get('bus_candidates', []):
                    if candidate['net'] == bus:
                        pullups.extend(p['reference'] for p in candidate['pullups'])
                checked.append({'reference':ref,'rule':'schematic:sda_pullup','check':'SDA pull-up candidate','status':'review' if not pullups else 'candidate_found',
                                'detail':', '.join(pullups)+' connects this line to a recognized supply; voltage and timing remain unverified.' if pullups else 'No pull-up to a recognized supply found.'})
                if not pullups:add('sda_pullup','check the data wire’s pull-up resistor',f'No fitted pull-up to a recognized supply was found on pin 3 ({bus}).',
                    'SDA is an open-drain output: it needs a pull-up so the wire can return high. An internal or external pull-up elsewhere may provide it.',
                    [f'Follow {ref} pin 3 through the schematic to the controller.', 'Check whether that data line has a pull-up resistor to a suitable logic supply, or a documented internal pull-up.',
                     'Use the datasheet and bus speed to choose its value. Explain any internal pull-up and firmware setting in the device context.'],numbers=('3',))
    from .topology import topology_checks
    path_findings,path_coverage=topology_checks(project)
    return findings+path_findings,checked+path_coverage

"""Check explicit connection contradictions in supported functional stages.

These checks use declared part identities and saved pins. They do not infer a
voltage from a net name or join internal transistor pins.
"""
from ..routing_chains import build_routing_chains, pin_name


def topology_checks(project):
    routing=project.circuit.get('routing_chains') or build_routing_chains(project)
    findings,coverage=[],[]
    for stage in routing.get('stages',[]):
        ref=stage['reference'];component=project.components[ref]
        returns=[p for p in component.schematic_pins if pin_name(p.name) in {'GND','VSS','VSS/GND'} and p.net and not p.no_connect]
        checks=[('input_output_joined',stage['input_net']==stage['output_net'],stage['input_pins']+stage['output_pins'],
                 'input and output share one wire',
                 'This wire goes around the '+stage['kind']+'. The saved connection bypasses its intended input-to-output function.'),
                ('input_return_joined',stage['input_net'] in {p.net for p in returns},stage['input_pins'],
                 'power input is connected to its own ground',
                 'The input and return pins share one net. The declared '+stage['kind']+' has no separate supply connection.'),
                ('output_return_joined',stage['output_net'] in {p.net for p in returns},stage['output_pins'],
                 'output is connected to its own ground',
                 'The output and return pins share one net. This does not show a separate output supply for the connected parts.')]
        for code,conflict,pins,title,why in checks:
            rule='topology:'+code
            coverage.append({'reference':ref,'rule':rule,'check':title,'status':'conflict' if conflict else 'checked',
                             'detail':'Compared exported pins of '+stage['value']+'. Operating voltage and state are unverified.'})
            if not conflict:continue
            markers=list(pins)
            if code.endswith('return_joined'):
                markers.extend({'reference':ref,'pin':p.number,'net':p.net,'name':p.name} for p in returns if p.net==pins[0]['net'])
            observed='; '.join(f"{p['reference']} pin {p['pin']} ({p['name']}): {p['net']}" for p in markers)
            steps=[f"At {ref}, separate the {title.replace(' share one wire','').replace(' is connected to its own ground',' and ground')} connections if you intend to use this {stage['kind']}.",
                   f"Connect the incoming supply to {ref} input pin {stage['input_pins'][0]['pin']}, the powered parts to output pin {stage['output_pins'][0]['pin']}, and keep its ground connection separate.",
                   'Save the schematic, update the PCB from it, and click Update here.']
            findings.append({'reference':ref,'rule':rule,'pads':[],'category':'electrical','priority':'fix',
                'title':ref+': '+title,'summary':observed,'why':why+' This follows the declared part and pin roles; it does not predict damage or measured voltage.',
                'next_step':steps[0],'steps':steps,'evidence':[component.schematic[0].location,observed],
                'sources':[],'schematic_check':True,'chain_targets':markers,'basis':'Saved circuit path check'})
    return findings,coverage

"""Traceable candidate values; never turn a nominal label into a verified limit."""
import math
import re


def suggestions(data, target):
    if target['kind']!='i2c':return {}
    result={}
    rail=target['rail']
    match=re.fullmatch(r'\+?(\d+(?:\.\d+)?)V',rail,re.I)
    if not match:
        alternate=re.fullmatch(r'\+?(\d+)V(\d+)',rail,re.I)
        if alternate:match=re.fullmatch(r'(\d+(?:\.\d+)?)',alternate[1]+'.'+alternate[2])
    if match and float(match[1])>0:
        result['voltage']={'value':float(match[1]),'basis':f'Supply label {rail} in the saved schematic.',
            'qualification':'Nominal label only. Confirm the highest actual voltage, including supply tolerance.', 'requires_confirmation':True}
    bus=next((b for b in data.get('circuit',{}).get('bus_candidates',[]) if 'i2c:'+b['net']==target['key']),{})
    tolerances=[];refs=[]
    for resistor in bus.get('pullups',[]):
        ref=resistor.get('reference');component=data.get('components',{}).get(ref,{})
        records=component.get('schematic',[])
        props=records[0].get('properties',{}) if records else {}
        values=[str(v).strip() for k,v in props.items() if k.casefold() in ('tolerance','resistor tolerance')]
        if len(set(values))!=1:break
        match=re.fullmatch(r'(?:±|\+/-)?\s*(\d+(?:\.\d+)?)\s*%',values[0])
        if not match or not 0<=float(match[1])<100:break
        tolerances.append(float(match[1]));refs.append(ref)
    if tolerances and len(tolerances)==len(bus.get('pullups',[])):
        result['tolerance']={'value':max(tolerances),'basis':'Declared resistor tolerance: '+', '.join(refs)+'.',
            'qualification':'Largest declared tolerance among the fitted pull-up resistors. Confirm the actual ordered parts.', 'requires_confirmation':True}
    # Never guess capacitance, pin drive limits or bus speed from net names.
    return {k:v for k,v in result.items() if math.isfinite(v['value'])}

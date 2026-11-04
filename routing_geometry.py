"""Measure parallel straight tracks and projected ground-fill coverage.

No geometry observation is treated as a measured noise or return-current result.
Runs against the refilled analyzed snapshot, never the editable source project.
"""
import math
import re
from collections import defaultdict


def point(node):
    try:
        result = tuple(float(v) for v in node.values[:2])
        return result if len(result)==2 and all(math.isfinite(v) for v in result) else None
    except (AttributeError,ValueError,TypeError):return None


def cross(a,b):return a[0]*b[1]-a[1]*b[0]
def sub(a,b):return (a[0]-b[0],a[1]-b[1])
def dot(a,b):return a[0]*b[0]+a[1]*b[1]


def inside(p, polygon):
    odd = False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        edge = sub(b,a); delta = sub(p,a)
        if abs(cross(edge,delta)) < 1e-9 and dot(delta,sub(p,b)) <= 1e-9:return True
        if (a[1]>p[1]) != (b[1]>p[1]) and p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]:odd = not odd
    return odd


def covered_intervals(a,b,polygon):
    """Clip a centerline against an even-odd polygon, including bridge-encoded holes."""
    d=sub(b,a); length2=dot(d,d)
    if length2==0:return []
    cuts=[0.,1.]
    for p,q in zip(polygon,polygon[1:]+polygon[:1]):
        edge=sub(q,p); offset=sub(p,a); denominator=cross(d,edge)
        if abs(denominator)>1e-12:
            t=cross(offset,edge)/denominator; u=cross(offset,d)/denominator
            if 0 <= t <= 1 and 0 <= u <= 1:cuts.append(t)
        elif abs(cross(offset,d))<1e-9:
            cuts.extend(max(0.,min(1.,dot(sub(v,a),d)/length2)) for v in (p,q))
    cuts=sorted(set(cuts));result=[]
    for low,high in zip(cuts,cuts[1:]):
        if high-low>1e-10 and inside((a[0]+d[0]*(low+high)/2,a[1]+d[1]*(low+high)/2),polygon):result.append((low,high))
    return result


def union_fraction(intervals):
    end=0.;total=0.
    for low,high in sorted(intervals):
        total+=max(0.,high-max(end,low));end=max(end,high)
    return min(1.,total)


def parallel_pair(a,b):
    if a['net']==b['net'] or a['layer']!=b['layer']:return None
    da=sub(a['end'],a['start']); db=sub(b['end'],b['start'])
    la=math.hypot(*da);lb=math.hypot(*db)
    if not la or not lb or abs(cross(da,db))/(la*lb)>1e-6:return None
    unit=(da[0]/la,da[1]/la);offset=sub(b['start'],a['start'])
    t0=dot(offset,unit);t1=dot(sub(b['end'],a['start']),unit)
    overlap=min(la,max(t0,t1))-max(0.,min(t0,t1))
    gap=abs(cross(unit,offset))-(a['width_mm']+b['width_mm'])/2
    if overlap<1. or gap>3*max(a['width_mm'],b['width_mm']):return None
    return dict(nets=[a['net'],b['net']], layer=a['layer'],gap_mm=round(max(0.,gap),6),
                parallel_mm=round(overlap,6),tracks=[a,b],
                interpretation='Nearby parallel copper; coupling voltage is not calculated. Check signal edge speed, return planes, impedance and whether these are an intentional differential pair.')


def measure(board):
    names={n.values[0]:n.values[1] for n in board.all('net') if len(n.values)>1}
    # The layer number is the sexpr tag, so the first value is the layer name.
    layers=[n.values[0] for n in (board.first('layers').children if board.first('layers') else []) if n.values and n.values[0].endswith('.Cu')]
    grounds={name for name in names.values() if re.fullmatch(r'(?:/)?(?:[ADP]?GND\d*|VSS)',name,re.I)}
    tracks=[];unsupported=0
    for item in board.children:
        if item.tag=='arc':unsupported+=1
        if item.tag!='segment':continue
        a=point(item.first('start'));b=point(item.first('end'))
        try:width=float(item.get('width'))
        except (ValueError,TypeError):width=0
        net=names.get(item.get('net'),'')
        if not a or not b or a==b or not math.isfinite(width) or width<=0 or not net:unsupported+=1;continue
        tracks.append(dict(start=list(a),end=list(b),width_mm=width,net=net,layer=item.get('layer'),uuid=item.get('uuid'),line=item.line))
    limited=len(tracks)>5000
    if limited:tracks=tracks[:5000]
    fills=defaultdict(list)
    for zone in board.all('zone'):
        net=names.get(zone.get('net'),zone.get('net_name'))
        if net not in grounds or zone.first('keepout'):continue
        for fill in zone.all('filled_polygon'):
            pts=fill.first('pts');poly=[point(p) for p in pts.all('xy')] if pts else []
            if len(poly)>=3 and all(p is not None for p in poly):fills[(fill.get('layer') or zone.get('layer'),net)].append(poly)
    parallel=[];pair_count=0;comparisons=0
    for layer in layers:
        ordered=sorted((t for t in tracks if t['layer']==layer),key=lambda t:min(t['start'][0],t['end'][0]))
        max_width=max((t['width_mm'] for t in ordered),default=0)
        for i,a in enumerate(ordered):
            for b in ordered[i+1:]:
                if min(b['start'][0],b['end'][0])>max(a['start'][0],a['end'][0])+4*max_width:break
                comparisons+=1
                if comparisons>1_000_000:limited=True;break
                pair=parallel_pair(a,b)
                if pair:
                    pair_count+=1
                    if len(parallel)<200:parallel.append(pair)
            if comparisons>1_000_000:break
    returns=[];work=0
    for t in tracks:
        if t['net'] in grounds or t['layer'] not in layers:continue
        index=layers.index(t['layer']);adjacent=layers[max(0,index-1):index]+layers[index+1:index+2]
        candidates=[(layer,net) for layer,net in fills if layer in adjacent]
        for layer,net in candidates:
            intervals=[]
            for poly in fills[(layer,net)]:
                work+=len(poly)
                if work>8_000_000:limited=True;break
                intervals.extend(covered_intervals(t['start'],t['end'],poly))
            if work>8_000_000:break
            fraction=union_fraction(intervals)
            returns.append(dict(net=t['net'],signal_layer=t['layer'],reference_layer=layer,reference_net=net,
                                length_mm=math.dist(t['start'],t['end']),uncovered_mm=math.dist(t['start'],t['end'])*(1-fraction),track=t))
        if work>8_000_000:break
    groups={}
    for r in returns:
        key=(r['net'],r['signal_layer'],r['reference_layer'],r['reference_net'])
        row=groups.setdefault(key,dict(zip(('net','signal_layer','reference_layer','reference_net'),key),length_mm=0.,uncovered_mm=0.,gaps=[]))
        row['length_mm']+=r['length_mm'];row['uncovered_mm']+=r['uncovered_mm']
        if r['uncovered_mm']>1e-6 and len(row['gaps'])<20:row['gaps'].append(r['track'])
    for row in groups.values():
        for key in ('length_mm','uncovered_mm'):row[key]=round(row[key],6)
    return dict(version=1,status='partial' if limited or unsupported else 'measured',
                straight_tracks=len(tracks),unsupported_items=unsupported,bounded=limited,
                parallel_count=pair_count,parallel_pairs=parallel,parallel_details_omitted=max(0,pair_count-len(parallel)),
                reference_coverage=list(groups.values()),ground_nets=sorted(grounds),
                limits=['Parallel candidates use at least 1 mm overlap and edge spacing ≤3 times the larger track width. This is a screening rule, not a crosstalk limit or a defect.',
                        'Reference coverage is the track centerline projected onto filled ground zones on adjacent copper layers. It does not prove plane connectivity, return-current distribution or impedance.',
                        'Ground nets are recognized by GND/AGND/DGND/PGND/VSS names. Curved tracks, pads, vias, reference transitions and copper graphics are not analyzed. Missing candidates never establish a pass.',
                        'Measurements are bounded to 5000 straight tracks, 1 million pair comparisons and 8 million polygon edges considered for clipping; exceeded bounds are reported as partial.'])

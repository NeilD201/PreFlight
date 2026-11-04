"""Source UUIDs and board geometry for precise finding overlays (millimetres)."""
import hashlib
import math
from .parsers.sexpr import parse, properties

VERSION = 2


def _xy(node, key):
    values = node.first(key).values if node.first(key) else []
    if len(values) < 2:
        return None
    point = [float(v) for v in values[:2]]
    return point if all(math.isfinite(v) for v in point) else None


def _arc(start, mid, end):
    ax,ay=start;bx,by=mid;cx,cy=end
    denominator=2*(ax*(by-cy)+bx*(cy-ay)+cx*(ay-by))
    if abs(denominator)<1e-12:
        return [start,mid,end]
    ux=((ax*ax+ay*ay)*(by-cy)+(bx*bx+by*by)*(cy-ay)+(cx*cx+cy*cy)*(ay-by))/denominator
    uy=((ax*ax+ay*ay)*(cx-bx)+(bx*bx+by*by)*(ax-cx)+(cx*cx+cy*cy)*(bx-ax))/denominator
    angles=[math.atan2(y-uy,x-ux) for x,y in (start,mid,end)]
    sweep=(angles[2]-angles[0])%(2*math.pi)
    if (angles[1]-angles[0])%(2*math.pi)>sweep:
        sweep-=2*math.pi
    radius=math.hypot(ax-ux,ay-uy)
    steps=max(8,math.ceil(abs(sweep)*16))
    return [[ux+radius*math.cos(angles[0]+sweep*i/steps),uy+radius*math.sin(angles[0]+sweep*i/steps)] for i in range(steps+1)]


def board_locations(content):
    root=parse(content.decode('utf-8-sig'),'kicad_pcb')
    nets={n.values[0]:n.values[1] for n in root.all('net') if len(n.values)>1}
    copper=[n.values[0] for n in (root.first('layers').children if root.first('layers') else [])
            if n.values and n.values[0].endswith('.Cu')]
    items={};pads={}
    edge_points=[]
    for node in root.children:
        if not node.tag.startswith('gr_') or node.get('layer')!='Edge.Cuts':continue
        for key in ('start','mid','end','center','at'):
            point=_xy(node,key)
            if point:edge_points.append(point)
        for point in node.all('xy'):
            if len(point.values)>=2:
                try:edge_points.append([float(point.values[0]),float(point.values[1])])
                except ValueError:pass
    outline=([min(p[0] for p in edge_points),min(p[1] for p in edge_points),
              max(p[0] for p in edge_points),max(p[1] for p in edge_points)] if edge_points else None)
    def add(node, entry):
        uuid=node.get('uuid') or node.get('tstamp')
        if not uuid:return
        net=node.first('net')
        entry.update(uuid=uuid,line=node.line,net=(net.values[1] if len(net.values)>1 else nets.get(net.values[0],'')) if net and net.values else '')
        items[uuid]=entry
    for node in root.children:
        if node.tag.startswith('gr_') and node.get('layer','').endswith('.Cu'):
            start,end=_xy(node,'start'),_xy(node,'end')
            points=[]
            if node.tag=='gr_line' and start and end:points=[start,end]
            elif node.tag=='gr_arc':
                mid=_xy(node,'mid')
                if start and mid and end:points=_arc(start,mid,end)
            elif node.tag in ('gr_poly','gr_rect'):
                polygon=node.first('pts')
                points=[[float(p.values[0]),float(p.values[1])] for p in polygon.all('xy') if len(p.values)>=2] if polygon else []
            elif node.tag=='gr_circle':
                center=_xy(node,'center');end=_xy(node,'end')
                if center and end:points=[center,end]
            if points:
                xs=[p[0] for p in points];ys=[p[1] for p in points]
                outside=bool(outline and (max(xs)<outline[0] or min(xs)>outline[2] or max(ys)<outline[1] or min(ys)>outline[3]))
                stroke=node.first('stroke');width=float(stroke.first('width').values[0]) if stroke and stroke.first('width') else 0
                add(node,dict(kind='graphic',point=[sum(xs)/len(xs),sum(ys)/len(ys)],points=points,
                    width_mm=width,layers=[node.get('layer')],label=('Copper drawing outside board outline' if outside else 'Unnetted copper drawing'),
                    outside_outline=outside,outline_mm=outline))
        elif node.tag in ('segment','arc'):
            start,end=_xy(node,'start'),_xy(node,'end')
            if not start or not end:continue
            mid=_xy(node,'mid')
            points=_arc(start,mid,end) if node.tag=='arc' and mid else [start,end]
            add(node,dict(kind='track',point=start,points=points,width_mm=float(node.get('width','0')),
                          layers=[node.get('layer')],label='Track'))
        elif node.tag=='via':
            point=_xy(node,'at')
            if not point:continue
            layer_node=node.first('layers');layers=layer_node.values if layer_node else []
            if len(layers)==2 and all(layer in copper for layer in layers):
                a,b=sorted(copper.index(layer) for layer in layers);layers=copper[a:b+1]
            add(node,dict(kind='via',point=point,radius_mm=float(node.get('size','0'))/2,
                          drill_mm=float(node.get('drill','0')),layers=layers,label='Via'))
        elif node.tag=='footprint':
            origin=_xy(node,'at')
            if not origin:continue
            at=node.first('at').values;angle=math.radians(float(at[2]) if len(at)>2 else 0)
            ref=properties(node).get('Reference','')
            add(node,dict(kind='footprint',point=origin,reference=ref,layers=[node.get('layer')],label=ref,approximate=True))
            for pad in node.all('pad'):
                local,size=_xy(pad,'at'),_xy(pad,'size')
                if local is None or size is None or len(pad.values)<3:continue
                x,y=local
                point=[origin[0]+x*math.cos(angle)+y*math.sin(angle),origin[1]-x*math.sin(angle)+y*math.cos(angle)]
                pa=pad.first('at').values
                rotation=math.radians(float(pa[2]) if len(pa)>2 else 0)
                corners=[[point[0]+x*math.cos(rotation)+y*math.sin(rotation),point[1]-x*math.sin(rotation)+y*math.cos(rotation)]
                         for x,y in [(-size[0]/2,-size[1]/2),(size[0]/2,-size[1]/2),(size[0]/2,size[1]/2),(-size[0]/2,size[1]/2)]]
                layers=pad.first('layers').values if pad.first('layers') else []
                layers=copper if '*.Cu' in layers else [layer for layer in layers if layer.endswith('.Cu')]
                entry=dict(kind='pad',point=point,points=corners,size_mm=size,shape=pad.values[2],
                           radius_mm=size[0]/2 if pad.values[2]=='circle' and size[0]==size[1] else None,
                           reference=ref,pin=pad.values[0],layers=layers,label=f'{ref} pad {pad.values[0]}',
                           envelope=pad.values[2] not in ('rect','circle'))
                add(pad,entry)
                if entry.get('uuid'):pads.setdefault(ref,{}).setdefault(pad.values[0],[]).append(entry['uuid'])
    return {'geometry_version':VERSION,'geometry_sha256':hashlib.sha256(content).hexdigest(),
            'item_locations':items,'pad_locations':pads,'copper_layers':copper}


def enrich_visual(metadata, snapshot, expected_hash):
    """Upgrade a saved preview from its checked copy, without rereading edited originals."""
    if metadata.get('status')!='ready' or not expected_hash:
        return metadata
    if metadata.get('geometry_version')==VERSION and metadata.get('geometry_sha256')==expected_hash:
        return metadata
    try:
        if snapshot.is_symlink() or snapshot.stat().st_size>64*1024*1024:
            return metadata
        content=snapshot.read_bytes()
        if hashlib.sha256(content).hexdigest()!=expected_hash or metadata.get('analyzed_pcb_sha256')!=expected_hash:
            return {**metadata,'geometry_message':'Saved board geometry does not match this preview. Run Update.'}
        return {**metadata,**board_locations(content)}
    except (OSError,ValueError,TypeError):
        return {**metadata,'geometry_message':'Exact board shapes are unavailable for this saved run. Run Update.'}


def merge_verified_source_artifacts(metadata, content, expected_hash):
    """Add detached copper drawings from the exact original PCB to a checked-copy preview.

    KiCad's refill/export copy can omit unnetted drawing objects. Only merge
    copper graphics outside Edge.Cuts, and only when the original file still
    matches the hash recorded by the analysis.
    """
    if not expected_hash or hashlib.sha256(content).hexdigest()!=expected_hash:
        return metadata
    try:
        source=board_locations(content)
    except (ValueError,TypeError):
        return metadata
    items=dict(metadata.get('item_locations') or {})
    added=False
    for uuid,item in source['item_locations'].items():
        if item.get('kind')!='graphic' or not item.get('outside_outline') or item.get('net') or uuid in items:
            continue
        items[uuid]={**item,'source_only':True}
        added=True
    if not added:return metadata
    return {**metadata,'item_locations':items,
            'source_geometry_sha256':expected_hash}

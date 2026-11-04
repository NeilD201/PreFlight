"""Small explicit schematic edit used when a sourced antenna is added to a PCB."""
import uuid
from pathlib import Path

from .parsers.sexpr import parse, properties
from .native import find_kicad
from .schematic_visual import schematic_locations


def _uid():
    return str(uuid.uuid4())


def _font():
    return '(effects (font (size 1.27 1.27)))'


def _library_symbol():
    binary=find_kicad()
    if not binary:raise ValueError('KiCad is required to add the antenna schematic symbol.')
    library=Path(binary).resolve().parent.parent/'SharedSupport/symbols/Device.kicad_sym'
    source=library.read_text(encoding='utf-8-sig');lib=parse(source,'kicad_symbol_lib')
    antenna=next((s for s in lib.all('symbol') if s.values and s.values[0]=='Antenna'),None)
    if not antenna:raise ValueError('The installed KiCad Device:Antenna symbol was not found.')
    embedded=source[antenna.start:antenna.end].replace('(symbol "Antenna"','(symbol "Device:Antenna"',1)
    prefix='(kicad_symbol_lib '
    parsed=parse(prefix+embedded+')','kicad_symbol_lib')
    antenna_copy=parsed.first('symbol')
    unit=next((s for s in antenna_copy.all('symbol') if s.values and s.values[0]=='Antenna_1_1'),None)
    if unit is None:raise ValueError('The KiCad antenna symbol has no electrical pin unit.')
    nc='''
        (pin no_connect line (at 5.08 -5.08 90) (length 2.54)
          (name "NC" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27))))
        )
    '''
    insert_at=unit.end-1-len(prefix)
    return embedded[:insert_at]+nc+embedded[insert_at:]


def _rf_pin_point(root,reference,pin_number):
    references={s.get('uuid'):properties(s).get('Reference','') for s in root.all('symbol')}
    _,_,pins=schematic_locations(root,references)
    entry=pins.get(reference,{}).get(str(pin_number))
    if not entry:raise ValueError(f'Could not locate schematic pin {reference}.{pin_number} for the RF feed.')
    return tuple(entry['point'])


def _antenna_placement(root,anchor):
    ax,ay=anchor
    # The stock Device:Antenna RF pin transforms to (x, y + 5.08) at 0°.
    # Extend the existing net horizontally from the exact connected pin.
    for distance in (12.7,15.24,17.78,20.32,25.4,30.48,38.1,50.8):
        x=round(ax-distance,4);y=round(ay-5.08,4)
        blocked=False
        for wire in root.all('wire'):
            pts=wire.first('pts')
            coords=[tuple(map(float,n.values[:2])) for n in pts.all('xy')] if pts else []
            if any(abs(py-ay)<0.001 and x+0.001<px<ax-0.001 for px,py in coords):blocked=True
            for a,b in zip(coords,coords[1:]):
                if abs(a[1]-b[1])<0.001 and abs(a[1]-ay)<0.001:
                    low,high=sorted((a[0],b[0]))
                    if max(low,x)<min(high,ax)-0.001:blocked=True
                elif min(a[1],b[1])+0.001<ay<max(a[1],b[1])-0.001:
                    cross_x=a[0]+(ay-a[1])*(b[0]-a[0])/(b[1]-a[1])
                    if x+0.001<cross_x<ax-0.001:blocked=True
        for symbol in root.all('symbol'):
            at=symbol.first('at')
            if at and len(at.values)>=2:
                try:cx,cy=map(float,at.values[:2])
                except ValueError:continue
                if (cx-x)**2+(cy-y)**2<7**2:blocked=True
        if not blocked:return x,y
    raise ValueError('No clear schematic spot was found beside the selected RF connection.')


def add_reference_antenna(path, *, reference, value, footprint, datasheet, net, anchor_reference, anchor_pin):
    """Insert a one-pin RF antenna symbol and global feed label into the staged schematic."""
    path=Path(path);text=path.read_text(encoding='utf-8-sig');root=parse(text,'kicad_sch')
    if not net or not root.first('lib_symbols') or not root.get('uuid'):
        raise ValueError('The schematic lacks a root UUID, library symbols or RF feed net.')
    if any(properties(s).get('Reference')==reference for s in root.all('symbol')):
        raise ValueError(f'Schematic reference {reference} already exists.')
    lib_id='Device:Antenna'
    lib=root.first('lib_symbols')
    already_embedded=any(s.values and s.values[0]==lib_id for s in lib.all('symbol'))
    anchor=_rf_pin_point(root,anchor_reference,anchor_pin)
    sx,sy=_antenna_placement(root,anchor)
    symbol_uuid,pin1_uuid,pin2_uuid,nc_uuid,wire_uuid=_uid(),_uid(),_uid(),_uid(),_uid()
    project=path.stem
    instance_path='/'+root.get('uuid')
    embedded='' if already_embedded else _library_symbol()
    instance=f'''\n    (symbol
      (lib_id "{lib_id}") (at {sx} {sy} 0) (unit 1)
      (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
      (uuid "{symbol_uuid}")
      (property "Reference" "{reference}" (at {sx+5.08} {sy} 0) {_font()})
      (property "Value" "{value}" (at {sx+5.08} {sy+2.54} 0) {_font()})
      (property "Footprint" "{footprint}" (at {sx} {sy} 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "{datasheet}" (at {sx} {sy} 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (pin "1" (uuid "{pin1_uuid}")) (pin "2" (uuid "{pin2_uuid}"))
      (instances (project "{project}" (path "{instance_path}" (reference "{reference}") (unit 1))))
    )
    (no_connect (at {sx+5.08} {sy+5.08}) (uuid "{nc_uuid}"))
    (wire (pts (xy {anchor[0]} {anchor[1]}) (xy {sx} {anchor[1]}))
      (stroke (width 0) (type default)) (uuid "{wire_uuid}"))\n'''
    if embedded:
        lib_close=lib.end-1
        text=text[:lib_close]+'\n'+embedded+'\n'+text[lib_close:]
    root=parse(text,'kicad_sch')
    root_close=root.end-1
    text=text[:root_close]+instance+text[root_close:]
    parse(text,'kicad_sch')
    path.write_text(text,encoding='utf-8')
    return {'path':path.name,'reference':reference,'net':net,'symbol':lib_id,'placement_mm':[sx,sy],
            'connected_from':{'reference':anchor_reference,'pin':str(anchor_pin),'point_mm':list(anchor)}}

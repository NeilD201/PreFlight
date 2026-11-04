"""Fixed operation interpreter, run by KiCad Python on a staged PCB only."""
import json
import sys
from pathlib import Path
import uuid
import pcbnew


def edit(path, operations):
    board = pcbnew.LoadBoard(path)
    if board is None:
        raise ValueError('KiCad could not load the edit copy.')
    point = lambda x, y: pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
    layers = {board.GetLayerName(n): n for n in board.GetEnabledLayers().Seq() if pcbnew.IsCopperLayer(n)}
    nets = {n.GetNetname(): n for n in board.GetNetsByNetcode().values()}
    # Snapshot tracks once before mutation. On large KiCad boards the SWIG
    # TRACKS iterator can become invalid after board.Remove(), so repeated
    # board.GetTracks() calls in a delete/move batch are not reliable.
    tracks_by_uuid={track.m_Uuid.AsString():track for track in board.GetTracks()}
    uuid_remap={}
    keepouts=[]
    for op in operations:
        kind = op['kind']
        if kind in ('move_footprint', 'replace_footprint'):
            matches = [f for f in board.GetFootprints() if f.GetReference() == op['reference']]
            if len(matches) != 1 or matches[0].IsLocked():
                raise ValueError('Footprint is missing, ambiguous or locked: '+op['reference'])
            old = matches[0]
            if kind == 'move_footprint':
                old.SetPosition(point(op['x_mm'], op['y_mm']))
                old.SetOrientationDegrees(op['rotation_deg'])
            else:
                library, name = op['footprint'].split(':')
                replacement = pcbnew.FootprintLoad(op['library_path'], name)
                if replacement is None:
                    raise ValueError('KiCad could not load the replacement footprint.')
                old_pads = {p.GetNumber(): p for p in old.Pads() if p.GetNumber()}
                new_pads = [p for p in replacement.Pads() if p.GetNumber()]
                if len(old_pads) != len([p for p in old.Pads() if p.GetNumber()]) or len(new_pads) != len(old_pads) or {p.GetNumber() for p in new_pads} != set(old_pads):
                    raise ValueError('Replacement requires exactly the same unique pad numbers. A pin remap needs a separate schematic design.')
                replacement.SetReference(old.GetReference())
                replacement.SetValue(old.GetValue())
                replacement.SetFPID(pcbnew.LIB_ID(library, name))
                replacement.SetPath(old.GetPath())
                uuid_remap[replacement.m_Uuid.AsString()]=old.m_Uuid.AsString()
                replacement.SetFields({field.GetName():field.GetText() for field in old.GetFields()})
                population=pcbnew.FP_DNP | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES | pcbnew.FP_BOARD_ONLY
                replacement.SetAttributes((replacement.GetAttributes() & ~population) | (old.GetAttributes() & population))
                board.Add(replacement)
                replacement.SetPosition(old.GetPosition())
                if old.IsFlipped():
                    replacement.Flip(replacement.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
                replacement.SetOrientationDegrees(old.GetOrientationDegrees())
                for pad in new_pads:
                    previous = old_pads[pad.GetNumber()]
                    pad.SetNet(previous.GetNet())
                    uuid_remap[pad.m_Uuid.AsString()]=previous.m_Uuid.AsString()
                board.Remove(old)
        elif kind == 'add_reference_antenna':
            if any(f.GetReference()==op['reference'] for f in board.GetFootprints()):
                raise ValueError('The proposed antenna reference already exists.')
            library,name=op['footprint'].split(':')
            antenna=pcbnew.FootprintLoad(op['library_path'],name)
            if antenna is None or {p.GetNumber() for p in antenna.Pads()}!={'1','2'}:
                raise ValueError('The reference antenna must have its documented two-pad map.')
            antenna.SetReference(op['reference']);antenna.SetFPID(pcbnew.LIB_ID(library,name))
            antenna.SetValue(op.get('value',name))
            antenna.SetField('Datasheet',op.get('datasheet_url',''))
            if op.get('manufacturer'):antenna.SetField('MF',op['manufacturer'])
            excluded=pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES
            antenna.SetAttributes(antenna.GetAttributes() & ~excluded)
            board.Add(antenna);antenna.SetPosition(point(op['x_mm'],op['y_mm']));antenna.SetOrientationDegrees(op['rotation_deg'])
            feed_net=nets[op['net']]
            if op.get('target_net') and feed_net.GetNetname()!=op['target_net']:
                if op['target_net'] in nets:raise ValueError('The proposed schematic antenna net name already exists.')
                feed_net.SetNetname(op['target_net'])
                nets[op['target_net']]=feed_net
            for pad in antenna.Pads():
                if pad.GetNumber()=='1':pad.SetNet(feed_net)
                elif op.get('ground_net'):pad.SetNet(nets[op['ground_net']])
        elif kind == 'add_copper_keepout':
            keepouts.append(op)
        elif kind == 'add_track':
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(point(op['start_x_mm'], op['start_y_mm']))
            track.SetEnd(point(op['end_x_mm'], op['end_y_mm']))
            track.SetWidth(pcbnew.FromMM(op['width_mm']))
            track.SetLayer(layers[op['layer']])
            track.SetNet(nets[op['net']])
            board.Add(track)
        elif kind == 'move_via':
            track=tracks_by_uuid.get(op['uuid'])
            matches=[track] if track and isinstance(track,pcbnew.PCB_VIA) else []
            if len(matches)!=1 or matches[0].IsLocked():
                raise ValueError('Via is missing, ambiguous or locked: '+op['uuid'])
            matches[0].SetPosition(point(op['x_mm'],op['y_mm']))
        elif kind == 'delete_track':
            track=tracks_by_uuid.get(op['uuid'])
            matches=[track] if track else []
            if len(matches) != 1 or matches[0].IsLocked() or isinstance(matches[0], pcbnew.PCB_VIA):
                raise ValueError('Track is missing, locked or is a via.')
            board.Remove(matches[0])
            tracks_by_uuid.pop(op['uuid'],None)
        else:
            raise ValueError('Unknown operation.')
    if not pcbnew.SaveBoard(path, board):
        raise ValueError('KiCad could not save the edit copy.')
    # SWIG exposes UUIDs read-only. Preserve item identity in the saved copy.
    text=Path(path).read_text()
    for new,old in uuid_remap.items():text=text.replace('(uuid "'+new+'")','(uuid "'+old+'")')
    for op in keepouts:
        x,y,a,b=[op[k] for k in ('start_x_mm','start_y_mm','end_x_mm','end_y_mm')]
        zone=f'''\n(zone (net 0) (net_name "") (layers "*.Cu") (uuid "{uuid.uuid4()}")
          (hatch edge 0.5) (connect_pads (clearance 0)) (min_thickness 0.25)
          (keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))
          (fill (thermal_gap 0.3) (thermal_bridge_width 0.3))
          (polygon (pts (xy {x} {y}) (xy {a} {y}) (xy {a} {b}) (xy {x} {b}))))\n'''
        end=text.rfind(')');text=text[:end]+zone+text[end:]
    Path(path).write_text(text)


if __name__ == '__main__':
    with open(sys.argv[2]) as source:
        edit(sys.argv[1], json.load(source))

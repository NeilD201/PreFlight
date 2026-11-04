"""Run only in KiCad's Python runtime, against a temporary project copy."""
import json
import sys
import pcbnew


def prepare(path):
    board = pcbnew.LoadBoard(path)
    if board is None:
        raise ValueError('KiCad could not load the copied PCB.')
    zones = [z for z in board.Zones() if not z.GetIsRuleArea()]
    if zones and not pcbnew.ZONE_FILLER(board).Fill(board.Zones()):
        raise ValueError('KiCad did not complete the copper refill.')
    if not pcbnew.SaveBoard(path, board):
        raise ValueError('KiCad could not save the refilled copy.')
    outline = pcbnew.SHAPE_POLY_SET()
    edges = [g for g in board.GetDrawings() if g.GetLayer() == pcbnew.Edge_Cuts]
    closed = bool(edges) and board.GetBoardPolygonOutlines(outline)
    bounds = board.GetBoardEdgesBoundingBox()
    copper = [pcbnew.BOARD.GetStandardLayerName(layer) for layer in board.GetEnabledLayers().Seq() if pcbnew.IsCopperLayer(layer)]
    copper.sort(key=lambda name: 0 if name == 'F.Cu' else 100 if name == 'B.Cu' else int(name[2:-3]))
    testpoints = []
    for footprint in board.GetFootprints():
        ref = footprint.GetReference()
        if ref.upper().startswith('TP') or 'testpoint' in str(footprint.GetFPID()).lower():
            for pad in footprint.Pads():
                testpoints.append({'reference':ref, 'pad':pad.GetNumber(), 'net':pad.GetNetname()})
    return {'status':'prepared', 'zone_fill':'refilled' if zones else 'not_needed',
            'copper_zones':len(zones), 'outline_closed':bool(closed),
            'outline_count':outline.OutlineCount() if closed else 0,
            'width_mm':pcbnew.ToMM(bounds.GetWidth()) if edges else None,
            'height_mm':pcbnew.ToMM(bounds.GetHeight()) if edges else None,
            'copper_layers':copper, 'testpoints':testpoints}


if __name__ == '__main__':
    result = prepare(sys.argv[1])
    with open(sys.argv[2], 'w') as output:
        json.dump(result, output)

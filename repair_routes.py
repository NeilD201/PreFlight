"""Select a free route terminal without breaking a saved connection or junction."""
import math


def point_segment_distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = dx * dx + dy * dy
    t = 0 if not length else max(0, min(1, ((point[0] - start[0]) * dx +
                                          (point[1] - start[1]) * dy) / length))
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def feed_endpoint(tracks, target, pad_radius, source_contacts, tolerance_mm):
    """Return one unambiguous free terminal, or None.

    Contacts are (layers, start, end, radius) on the saved feed net. A route
    must have a saved connection before its other end can be adjusted.
    This selects geometry only; native DRC still checks the resulting board.
    """
    ends = [((t['start_x_mm'], t['start_y_mm']), (t['end_x_mm'], t['end_y_mm']))
            for t in tracks]
    # A route already reaches the pad: leave ALL its endpoints untouched.
    if any(t['layer'] == 'F.Cu' and point_segment_distance(target, *points) <= pad_radius
           for t, points in zip(tracks, ends)):
        return None

    def attached(point, layer):
        return any(layer in layers and point_segment_distance(point, a, b) <= radius + 1e-6
                   for layers, a, b, radius in source_contacts)

    neighbors = [set() for _ in tracks]
    anchored = set()
    for i, (track, points) in enumerate(zip(tracks, ends)):
        if any(attached(p, track['layer']) for p in points):
            anchored.add(i)
        for j in range(i):
            if track['layer'] != tracks[j]['layer']:
                continue
            if any(point_segment_distance(p, *ends[j]) < 1e-6 for p in points) or any(
                    point_segment_distance(p, *points) < 1e-6 for p in ends[j]):
                neighbors[i].add(j)
                neighbors[j].add(i)
    pending = list(anchored)
    while pending:
        for other in neighbors[pending.pop()] - anchored:
            anchored.add(other)
            pending.append(other)
    candidates = []
    for i in anchored:
        track = tracks[i]
        if track['layer'] != 'F.Cu':
            continue
        for side, point in zip(('start', 'end'), ends[i]):
            if attached(point, track['layer']):
                continue
            if any(tracks[j]['layer'] == track['layer'] and
                   point_segment_distance(point, *ends[j]) < 1e-6
                   for j in range(len(tracks)) if j != i):
                continue
            if 1e-6 < math.dist(point, target) <= tolerance_mm:
                candidates.append((track, side, math.dist(point, target)))
    return candidates[0] if len(candidates) == 1 else None

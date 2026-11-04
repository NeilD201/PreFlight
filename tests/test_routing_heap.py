"""Regression: heap tiebreak in _shortest_multi with mixed-type nodes at tied cost.

Via-hub keys (str) and coordinate keys (tuple) coexist in the routing graph.
Zero-length barrel edges and the rotation fix make exact cost ties likely;
heappush/heappop then compare the node keys and raise TypeError.
Expected: _shortest_multi must never compare node keys (monotonic counter).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.routing_analysis import _shortest_multi


class HeapTiebreakTests(unittest.TestCase):
    def test_mixed_key_types_with_tied_costs(self):
        adjacent = {'s1': [(('t', 1), 0.0, 'barrel'), ('s2', 0.0, 'x')],
                    ('t', 1): [], 's2': []}
        dist = _shortest_multi(adjacent, ['s1'])
        self.assertEqual(dist[('t', 1)], 0.0)
        self.assertEqual(dist['s2'], 0.0)


if __name__ == '__main__':
    unittest.main()

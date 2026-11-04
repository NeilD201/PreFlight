"""The board preview must place issue markers on the plotted footprint."""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.board_visual import _calibrate, _safe_svg


class BoardVisualTests(unittest.TestCase):
    def test_front_and_mirrored_offsets(self):
        board = [(-38, -42, 0, -42), (-38, 0, 0, 0), (-38, -42, -38, 0)]
        front = [(5, 1, 43, 1), (5, 43, 43, 43), (5, 1, 5, 43)]
        back = [(58, 1, 20, 1), (58, 43, 20, 43), (58, 1, 58, 43)]
        self.assertEqual(_calibrate(board, front), (43, 43))
        self.assertEqual(_calibrate(board, back, mirrored=True), (20, 43))

    def test_crop_svg_and_reject_active_content(self):
        svg='<svg width="100mm" height="90mm" viewBox="0 0 100 90"><path d="M1 2 L3 4"/></svg>'
        result=_safe_svg(svg,(5,6,38,42)).decode()
        self.assertIn('viewBox="5.0000 6.0000 38.0000 42.0000"',result)
        self.assertIn('width="38.0000mm"',result)
        with self.assertRaises(ValueError):
            _safe_svg('<svg viewBox="0 0 1 1"><script>alert(1)</script></svg>',(0,0,1,1))


if __name__ == '__main__':
    unittest.main()

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.repair_routes import feed_endpoint
from pcb_checker.board_edits import planner_operations, validate_operations


def track(a,b,layer='F.Cu'):
    return dict(kind='add_track',net='RF',layer=layer,width_mm=.2,reason='Feed',
                start_x_mm=a[0],start_y_mm=a[1],end_x_mm=b[0],end_y_mm=b[1])


class FeedTerminalTests(unittest.TestCase):
    def choose(self,tracks,target=(5,0),contacts=None):
        return feed_endpoint(tracks,target,.2,
            contacts if contacts is not None else [(['F.Cu'],(0,0),(0,0),.1)],10)

    def test_preserves_shared_vertex_even_when_it_is_closest(self):
        routes=[track((0,0),(4,0)),track((4,0),(3,3))]
        selected=self.choose(routes)
        self.assertIs(selected[0],routes[1]);self.assertEqual(selected[1],'end')

    def test_does_not_snap_a_floating_or_ambiguous_route(self):
        self.assertIsNone(self.choose([track((2,2),(4,4))]))
        self.assertIsNone(self.choose([track((0,0),(3,3)),track((0,0),(3,-3))]))

    def test_already_connected_segment_interior_needs_no_snap(self):
        self.assertIsNone(self.choose([track((0,0),(6,0))]))

    def test_saved_failure_shape_keeps_the_original_feed_end(self):
        # Regression: the former nearest-endpoint rule changed the first
        # segment to close a loop, despite the last segment reaching the pad.
        points=[(-36.69,-35.2),(-37.5,-35.2),(-37.5,-39),(-36.95,-39)]
        routes=[track(a,b) for a,b in zip(points,points[1:])]
        self.assertIsNone(self.choose(routes,target=(-36.95,-39),
            contacts=[(['F.Cu'],points[0],points[0],.2)]))

    def test_back_layer_is_not_a_front_pad_connection(self):
        self.assertIsNone(self.choose([track((0,0),(4,0),'B.Cu')]))

    def test_saved_track_interior_is_a_protected_connection(self):
        route=track((4,0),(2,3))
        selected=self.choose([route],contacts=[(['F.Cu'],(3,0),(6,0),.1)])
        self.assertEqual(selected[1],'end')

    def test_feedback_retains_checked_coordinates_without_native_metadata(self):
        operation={**track((0,0),(5,1)),'library_path':'private','target_net':'renamed'}
        feedback=planner_operations([operation])
        self.assertEqual(validate_operations(feedback,None)[0],track((0,0),(5,1)))


if __name__=='__main__':unittest.main()

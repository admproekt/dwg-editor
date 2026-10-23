"""Тесты растяжения (server/stretch.py) на синтетических чертежах."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import ezdxf  # noqa: E402

import stretch as st  # noqa: E402


def new_doc():
    return ezdxf.new("R2018", setup=True)


class StretchBasics(unittest.TestCase):
    def setUp(self):
        self.doc = new_doc()
        self.msp = self.doc.modelspace()

    def test_line_inside_moves_crossing_stretches_outside_stays(self):
        inside = self.msp.add_line((1, 1), (2, 1))
        crossing = self.msp.add_line((1, 5), (1, 15))
        outside = self.msp.add_line((50, 50), (60, 60))
        res = st.stretch(self.doc, (0, 0, 10, 10), 0, 3)
        self.assertEqual(tuple(inside.dxf.start), (1, 4, 0))
        self.assertEqual(tuple(inside.dxf.end), (2, 4, 0))
        self.assertEqual(tuple(crossing.dxf.start), (1, 8, 0))
        self.assertEqual(tuple(crossing.dxf.end), (1, 15, 0))
        self.assertEqual(tuple(outside.dxf.start), (50, 50, 0))
        self.assertEqual(res["changed"]["LINE"], 2)

    def test_lwpolyline_vertices(self):
        pl = self.msp.add_lwpolyline([(0, 0), (0, 20), (5, 20), (5, 0)], close=True)
        st.stretch(self.doc, (-1, -1, 10, 10), 0, 4)
        pts = [(p[0], p[1]) for p in pl.get_points()]
        self.assertEqual(pts, [(0, 4), (0, 20), (5, 20), (5, 4)])
        self.assertTrue(pl.closed)

    def test_anchored_types_move_by_anchor(self):
        t = self.msp.add_text("A", dxfattribs={"height": 1}).set_placement((2, 2))
        c = self.msp.add_circle((3, 3), 1)
        far = self.msp.add_text("B", dxfattribs={"height": 1}).set_placement((100, 100))
        st.stretch(self.doc, (0, 0, 10, 10), 5, 0)
        self.assertAlmostEqual(t.dxf.insert[0], 7)
        self.assertAlmostEqual(c.dxf.center[0], 8)
        self.assertAlmostEqual(far.dxf.insert[0], 100)

    def test_aligned_text_moves_both_points(self):
        t = self.msp.add_text("A", dxfattribs={"height": 1, "halign": 4, "valign": 2})
        t.dxf.insert = (2, 2, 0)
        t.dxf.align_point = (2, 2, 0)
        st.stretch(self.doc, (0, 0, 10, 10), 0, 3)
        self.assertAlmostEqual(t.dxf.align_point[1], 5)
        self.assertAlmostEqual(t.dxf.insert[1], 5)

    def test_exclude_and_handles(self):
        a = self.msp.add_line((1, 1), (2, 1))
        b = self.msp.add_line((1, 2), (2, 2))
        st.stretch(self.doc, (0, 0, 10, 10), 0, 1, exclude=[a.dxf.handle])
        self.assertEqual(a.dxf.start[1], 1)
        self.assertEqual(b.dxf.start[1], 3)
        st.stretch(self.doc, (0, 0, 10, 10), 0, 1, handles=[a.dxf.handle])
        self.assertEqual(a.dxf.start[1], 2)
        self.assertEqual(b.dxf.start[1], 3)

    def test_arc_inside_moves_and_crossing_raises(self):
        a = self.msp.add_arc((5, 5), 2, 0, 90)
        st.stretch(self.doc, (0, 0, 10, 10), 0, 2)
        self.assertAlmostEqual(a.dxf.center[1], 7)
        b = self.msp.add_arc((5, 5), 2, 0, 90)
        with self.assertRaises(st.StretchError):
            st.stretch(self.doc, (0, 0, 6, 10), 0, 2, handles=[b.dxf.handle])

    def test_hatch_polyline_path(self):
        h = self.msp.add_hatch(color=1)
        h.paths.add_polyline_path([(0, 0), (10, 0), (10, 20), (0, 20)], is_closed=True)
        st.stretch(self.doc, (-1, 10, 11, 25), 0, -5)
        verts = [(v[0], v[1]) for v in h.paths[0].vertices]
        self.assertEqual(verts, [(0, 0), (10, 0), (10, 15), (0, 15)])

    def test_unsupported_types_are_reported(self):
        self.msp.add_ellipse((0, 0), (5, 0), 0.5)
        res = st.stretch(self.doc, (-10, -10, 10, 10), 0, 1)
        self.assertEqual(res["skipped_types"].get("ELLIPSE"), 1)


class StretchDimension(unittest.TestCase):
    def setUp(self):
        self.doc = new_doc()
        self.msp = self.doc.modelspace()

    def block_text(self, dim):
        block = self.doc.blocks.get(dim.dxf.geometry)
        out = []
        for e in block:
            if e.dxftype() == "MTEXT":
                out.append(e.text)
            elif e.dxftype() == "TEXT":
                out.append(e.dxf.text)
        return " ".join(out)

    def make_dim(self, p1, p2, angle, lfac=1.0):
        d = self.msp.add_linear_dim(base=(p1[0] - 5, (p1[1] + p2[1]) / 2), p1=p1, p2=p2, angle=angle,
                                    override={"dimdec": 0, "dimlfac": lfac})
        d.render()
        d.dimension.dxf.actual_measurement = abs(p2[1] - p1[1]) if angle == 90 else abs(p2[0] - p1[0])
        return d.dimension

    def test_vertical_dimension_updates_measurement_and_text(self):
        dim = self.make_dim((0, 0), (0, 100), 90)
        self.assertAlmostEqual(dim.dxf.actual_measurement, 100)
        self.assertIn("100", self.block_text(dim))
        st.stretch(self.doc, (-10, 50, 10, 200), 0, -30)
        self.assertAlmostEqual(dim.dxf.actual_measurement, 70)
        text = self.block_text(dim)
        self.assertIn("70", text)
        self.assertNotIn("100", text)

    def test_measurement_scale_factor_is_respected(self):
        dim = self.make_dim((0, 0), (0, 100), 90, lfac=100)
        self.assertIn("10000", self.block_text(dim))
        st.stretch(self.doc, (-10, 50, 10, 200), 0, -30)
        text = self.block_text(dim)
        self.assertIn("7000", text)
        self.assertNotIn("10000", text)

    def test_text_midpoint_follows_dimension(self):
        dim = self.make_dim((0, 0), (0, 100), 90)
        mid_before = tuple(dim.dxf.text_midpoint)
        st.stretch(self.doc, (-10, 50, 10, 200), 0, -30)
        self.assertAlmostEqual(dim.dxf.text_midpoint[1], mid_before[1] - 15)

    def test_symmetric_shrink_keeps_center_and_shortens(self):
        dim = self.make_dim((0, 0), (0, 100), 90)
        line = self.msp.add_line((20, 0), (20, 100))
        top = self.msp.add_line((30, 90), (31, 90))
        bottom = self.msp.add_line((30, 10), (31, 10))
        st.shrink_symmetric(self.doc, (-100, -100, 100, 200), "y", 50, 20)
        self.assertAlmostEqual(dim.dxf.actual_measurement, 80)
        self.assertIn("80", self.block_text(dim))
        self.assertAlmostEqual(line.dxf.start[1], 10)
        self.assertAlmostEqual(line.dxf.end[1], 90)
        self.assertAlmostEqual(top.dxf.start[1], 80)
        self.assertAlmostEqual(bottom.dxf.start[1], 20)

    def test_horizontal_symmetric_shrink(self):
        line = self.msp.add_line((0, 5), (100, 5))
        st.shrink_symmetric(self.doc, (-100, -100, 200, 100), "x", 50, 10)
        self.assertAlmostEqual(line.dxf.start[0], 5)
        self.assertAlmostEqual(line.dxf.end[0], 95)


class CloseGap(unittest.TestCase):
    def setUp(self):
        self.doc = new_doc()
        self.msp = self.doc.modelspace()
        self.region = (-100, -100, 200, 200)

    def test_gap_is_closed_and_inside_entities_deleted(self):
        left = self.msp.add_line((10, 0), (10, 5))
        inside = self.msp.add_line((50, 0), (50, 5))
        right = self.msp.add_line((90, 0), (90, 5))
        crossing = self.msp.add_line((0, 20), (100, 20))
        inside_handle = inside.dxf.handle.upper()
        res = st.close_gap(self.doc, self.region, "x", 40, 60)
        self.assertEqual(res["deleted"], [inside_handle])
        self.assertAlmostEqual(left.dxf.start[0], 20)
        self.assertAlmostEqual(right.dxf.start[0], 80)
        self.assertAlmostEqual(crossing.dxf.start[0], 10)
        self.assertAlmostEqual(crossing.dxf.end[0], 90)
        self.assertEqual(len(list(self.msp)), 3)

    def test_dimension_across_gap(self):
        d = self.msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(100, 0), angle=0,
                                    override={"dimdec": 0, "dimlfac": 1.0})
        d.render()
        dim = d.dimension
        dim.dxf.actual_measurement = 100
        st.close_gap(self.doc, self.region, "x", 40, 60)
        self.assertAlmostEqual(dim.dxf.actual_measurement, 80)

    def test_exclude_keeps_entity_in_place(self):
        title = self.msp.add_text("T", dxfattribs={"height": 1}).set_placement((50, 50))
        res = st.close_gap(self.doc, self.region, "x", 40, 60, exclude=[title.dxf.handle])
        self.assertAlmostEqual(title.dxf.insert[0], 50)
        self.assertEqual(res["deleted"], [])

    def test_ambiguous_entity_is_reported_and_untouched(self):
        pl = self.msp.add_lwpolyline([(10, 0), (50, 10), (90, 0)])
        res = st.close_gap(self.doc, self.region, "x", 40, 60)
        self.assertEqual(res["ambiguous"], [pl.dxf.handle.upper()])
        self.assertEqual([round(p[0]) for p in pl.get_points()], [10, 50, 90])

    def test_y_axis(self):
        low = self.msp.add_line((0, 10), (5, 10))
        high = self.msp.add_line((0, 90), (5, 90))
        st.close_gap(self.doc, self.region, "y", 40, 60)
        self.assertAlmostEqual(low.dxf.start[1], 20)
        self.assertAlmostEqual(high.dxf.start[1], 80)


if __name__ == "__main__":
    unittest.main()

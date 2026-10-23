"""Тесты инструментов MCP-сервера на синтетических чертежах (без AutoCAD и без ODA).

Запуск из корня репозитория, интерпретатором из окружения плагина:
    python -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import dwg_server as srv  # noqa: E402


def j(s: str):
    return json.loads(s)


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        srv.SESSIONS.clear()
        srv.CURRENT = None
        srv.new_drawing("test_drawing.dxf")

    def msp(self):
        return srv._session(None).doc.modelspace()

    def line(self, a, b, layer=None):
        return j(srv.add_line(a, b, layer=layer))["handle"]

    def ent(self, handle):
        return srv._entity(srv._session(None), handle)


class EngineStatus(ToolTestCase):
    def test_reports_ezdxf(self):
        data = j(srv.engine_status())
        self.assertTrue(data["engines"]["ezdxf"]["available"])
        self.assertTrue(data["engines"]["ezdxf"]["implemented"])
        for name in ("autocad_live", "autocad_core_console", "autodesk_platform_services"):
            self.assertFalse(data["engines"][name]["implemented"])
        self.assertNotIn("secret", json.dumps(data).lower())


class LayerTools(ToolTestCase):
    def test_set_properties(self):
        srv.add_layer("Стены")
        data = j(srv.set_layer_properties("Стены", color=1, on=False, frozen=True, locked=True, lineweight=25))
        self.assertEqual(data["color"], 1)
        self.assertFalse(data["on"])
        self.assertTrue(data["frozen"])
        self.assertTrue(data["locked"])
        data = j(srv.set_layer_properties("Стены", on=True, frozen=False, locked=False))
        self.assertTrue(data["on"] and not data["frozen"] and not data["locked"])
        self.assertEqual(data["color"], 1)

    def test_unknown_layer_and_linetype(self):
        with self.assertRaises(KeyError):
            srv.set_layer_properties("нет такого", color=1)
        srv.add_layer("A")
        with self.assertRaises(ValueError):
            srv.set_layer_properties("A", linetype="НеСуществует")

    def test_rename_moves_entities(self):
        srv.add_layer("Старое")
        h = self.line([0, 0], [1, 0], layer="Старое")
        data = j(srv.set_layer_properties("Старое", new_name="Новое"))
        self.assertEqual(data["name"], "Новое")
        self.assertEqual(self.ent(h).dxf.layer, "Новое")
        names = [l["name"] for l in j(srv.list_layers())]
        self.assertIn("Новое", names)
        self.assertNotIn("Старое", names)

    def test_rename_protected_and_duplicate(self):
        with self.assertRaises(ValueError):
            srv.set_layer_properties("0", new_name="X")
        srv.add_layer("A")
        srv.add_layer("B")
        with self.assertRaises(ValueError):
            srv.set_layer_properties("A", new_name="B")

    def test_delete_layer(self):
        srv.add_layer("Пустой")
        srv.delete_layer("Пустой")
        self.assertNotIn("Пустой", [l["name"] for l in j(srv.list_layers())])

    def test_delete_refuses_used_and_protected(self):
        srv.add_layer("Занят")
        self.line([0, 0], [1, 0], layer="Занят")
        with self.assertRaises(RuntimeError):
            srv.delete_layer("Занят")
        with self.assertRaises(ValueError):
            srv.delete_layer("0")


class Transforms(ToolTestCase):
    def assertPoint(self, p, x, y):
        self.assertAlmostEqual(p[0], x, places=6)
        self.assertAlmostEqual(p[1], y, places=6)

    def test_copy(self):
        h = self.line([0, 0], [10, 0])
        data = j(srv.copy_entities([h], 5, 5))
        self.assertEqual(data["copied"], 1)
        new = self.ent(data["new_handles"][0])
        self.assertPoint(new.dxf.start, 5, 5)
        self.assertPoint(self.ent(h).dxf.start, 0, 0)
        self.assertEqual(len(list(self.msp())), 2)

    def test_rotate(self):
        h = self.line([0, 0], [10, 0])
        srv.rotate_entities([h], 90)
        self.assertPoint(self.ent(h).dxf.end, 0, 10)

    def test_rotate_about_center(self):
        h = self.line([10, 0], [20, 0])
        srv.rotate_entities([h], 180, center=[10, 0])
        self.assertPoint(self.ent(h).dxf.end, 0, 0)

    def test_scale(self):
        h = self.line([1, 1], [3, 1])
        srv.scale_entities([h], 2, center=[1, 1])
        self.assertPoint(self.ent(h).dxf.start, 1, 1)
        self.assertPoint(self.ent(h).dxf.end, 5, 1)
        with self.assertRaises(ValueError):
            srv.scale_entities([h], 0)

    def test_mirror_in_place(self):
        h = self.line([1, 1], [3, 1])
        srv.mirror_entities([h], [0, 0], [1, 0])
        self.assertPoint(self.ent(h).dxf.start, 1, -1)
        self.assertPoint(self.ent(h).dxf.end, 3, -1)

    def test_mirror_keep_original(self):
        h = self.line([1, 1], [3, 1])
        data = j(srv.mirror_entities([h], [0, 0], [0, 1], keep_original=True))
        new = self.ent(data["new_handles"][0])
        self.assertPoint(new.dxf.start, -1, 1)
        self.assertPoint(self.ent(h).dxf.start, 1, 1)

    def test_circle_and_polyline(self):
        c = j(srv.add_circle([2, 0], 1))["handle"]
        p = j(srv.add_polyline([[0, 0], [2, 0], [2, 2]]))["handle"]
        srv.rotate_entities([c, p], 90)
        self.assertPoint(self.ent(c).dxf.center, 0, 2)
        self.assertPoint(list(self.ent(p).get_points())[1], 0, 2)
        srv.mirror_entities([c], [0, 0], [1, 0])
        self.assertPoint(self.ent(c).dxf.center, 0, -2)

    def test_bad_handle_changes_nothing(self):
        h = self.line([0, 0], [10, 0])
        with self.assertRaises(KeyError):
            srv.rotate_entities([h, "FFFF"], 90)
        self.assertPoint(self.ent(h).dxf.end, 10, 0)
        with self.assertRaises(ValueError):
            srv.copy_entities([], 1, 1)


class BulkTools(ToolTestCase):
    def test_modify_entities(self):
        a = self.line([0, 0], [1, 0])
        b = self.line([0, 1], [1, 1])
        srv.modify_entities([{"handle": a, "changes": {"color": 1}},
                             {"handle": b, "changes": {"layer": "Новый", "color": 3}}])
        self.assertEqual(self.ent(a).dxf.color, 1)
        self.assertEqual(self.ent(b).dxf.layer, "Новый")
        self.assertEqual(self.ent(b).dxf.color, 3)

    def test_modify_entities_is_atomic(self):
        a = self.line([0, 0], [1, 0])
        with self.assertRaises(ValueError):
            srv.modify_entities([{"handle": a, "changes": {"color": 1}},
                                 {"handle": a, "changes": {"запрещено": 1}}])
        self.assertNotEqual(self.ent(a).dxf.color, 1)
        with self.assertRaises(KeyError):
            srv.modify_entities([{"handle": a, "changes": {"color": 1}},
                                 {"handle": "FFFF", "changes": {"color": 2}}])
        self.assertNotEqual(self.ent(a).dxf.color, 1)

    def test_move_entities_each(self):
        a = self.line([0, 0], [1, 0])
        b = self.line([0, 1], [1, 1])
        srv.move_entities_each([{"handle": a, "dx": 1, "dy": 2}, {"handle": b, "dx": -1, "dy": 0, "dz": 3}])
        self.assertAlmostEqual(self.ent(a).dxf.start[0], 1)
        self.assertAlmostEqual(self.ent(a).dxf.start[1], 2)
        self.assertAlmostEqual(self.ent(b).dxf.start[0], -1)
        self.assertAlmostEqual(self.ent(b).dxf.start[2], 3)

    def test_move_entities_each_is_atomic(self):
        a = self.line([0, 0], [1, 0])
        with self.assertRaises(KeyError):
            srv.move_entities_each([{"handle": a, "dx": 5, "dy": 0}, {"handle": "FFFF", "dx": 1, "dy": 1}])
        self.assertAlmostEqual(self.ent(a).dxf.start[0], 0)
        with self.assertRaises(ValueError):
            srv.move_entities_each([{"handle": a, "dx": "не число", "dy": 0}])

    def test_aligned_text_moves_with_align_point(self):
        """Регресс: у TEXT с выравниванием положение задаёт align_point, сдвигать надо оба."""
        t = j(srv.add_text("0,35", [10, 10], rotation=90))["handle"]
        e = self.ent(t)
        e.dxf.halign = 4
        e.dxf.valign = 2
        e.dxf.align_point = (10, 10, 0)
        srv.move_entities_each([{"handle": t, "dx": 3, "dy": -1}])
        self.assertAlmostEqual(e.dxf.align_point[0], 13)
        self.assertAlmostEqual(e.dxf.align_point[1], 9)
        self.assertAlmostEqual(e.dxf.insert[0], 13)


class StretchTools(ToolTestCase):
    def test_stretch_entities_moves_part(self):
        a = self.line([0, 0], [0, 100])
        srv.stretch_entities([-10, 50, 10, 200], 0, -30)
        self.assertAlmostEqual(self.ent(a).dxf.end[1], 70)
        with self.assertRaises(ValueError):
            srv.stretch_entities([10, 0, 0, 10], 0, 1)

    def test_close_gap_tool(self):
        a = self.line([0, 0], [100, 0])
        inside = self.line([50, 5], [50, 6])
        res = j(srv.close_gap([-10, -10, 200, 10], "x", 40, 60))
        self.assertEqual(len(res["deleted"]), 1)
        self.assertAlmostEqual(self.ent(a).dxf.start[0], 10)
        self.assertAlmostEqual(self.ent(a).dxf.end[0], 90)
        self.assertEqual(len(list(self.msp())), 1)


if __name__ == "__main__":
    unittest.main()

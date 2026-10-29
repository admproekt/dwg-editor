"""Тесты инструментов чтения IFC и просмотрщика на синтетической модели.

Запуск из папки плагина интерпретатором из его окружения:
    python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
sys.path.insert(0, str(HERE))

import ifc_samples  # noqa: E402
import ifc_server as srv  # noqa: E402


def j(s):
    return json.loads(s)


class IfcTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = ifc_samples.build_sample(str(Path(cls.tmp.name) / "sample.ifc"))

    @classmethod
    def tearDownClass(cls):
        srv.ifc_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.summary = j(srv.ifc_open(self.path))

    def test_summary(self):
        s = self.summary
        self.assertEqual(s["schema"], "IFC4")
        self.assertEqual(s["project"], "Тестовый проект")
        self.assertEqual(s["elements_by_class"], {"IfcWall": 4, "IfcSlab": 1})
        self.assertEqual([st["name"] for st in s["storeys"]], ["Этаж 1", "Этаж 2"])
        self.assertEqual(s["total_elements"], 5)

    def test_open_errors(self):
        with self.assertRaises(FileNotFoundError):
            srv.ifc_open(str(Path(self.tmp.name) / "нет.ifc"))
        bad = Path(self.tmp.name) / "a.txt"
        bad.write_text("x")
        with self.assertRaises(ValueError):
            srv.ifc_open(str(bad))

    def test_list_filters(self):
        walls = j(srv.ifc_list_elements(ifc_class="IfcWall"))
        self.assertEqual(walls["total"], 4)
        self.assertEqual(j(srv.ifc_list_elements(name_contains="стена 3"))["total"], 1)
        self.assertEqual(j(srv.ifc_list_elements(storey="Этаж 1"))["total"], 5)
        self.assertEqual(j(srv.ifc_list_elements(storey="Этаж 2"))["total"], 0)
        page = j(srv.ifc_list_elements(limit=2, offset=1))
        self.assertEqual(len(page["elements"]), 2)
        self.assertEqual(page["total"], 5)

    def test_get_element_has_psets(self):
        gid = j(srv.ifc_list_elements(name_contains="Стена 1"))["elements"][0]["global_id"]
        e = j(srv.ifc_get_element(gid))
        self.assertEqual(e["class"], "IfcWall")
        self.assertEqual(e["psets"]["Pset_WallCommon"]["FireRating"], "EI60")
        self.assertTrue(e["psets"]["Pset_WallCommon"]["IsExternal"])
        with self.assertRaises(KeyError):
            srv.ifc_get_element("не-существует")

    def test_ifc2x3_spatial_elements_are_not_counted(self):
        import ifcopenshell
        import ifcopenshell.guid
        f = ifcopenshell.file(schema="IFC2X3")
        for cls in ("IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcBuildingElementProxy"):
            f.create_entity(cls, GlobalId=ifcopenshell.guid.new(), Name=cls)
        path = str(Path(self.tmp.name) / "x23.ifc")
        f.write(path)
        s = j(srv.ifc_open(path))
        self.assertEqual(s["elements_by_class"], {"IfcBuildingElementProxy": 1})
        self.assertEqual(j(srv.ifc_list_elements())["total"], 1)
        srv.ifc_open(self.path)

    def test_no_model(self):
        srv.ifc_close()
        with self.assertRaises(RuntimeError):
            srv.ifc_list_elements()
        srv.ifc_open(self.path)


class IfcEditing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = ifc_samples.build_sample(str(Path(cls.tmp.name) / "sample.ifc"))

    @classmethod
    def tearDownClass(cls):
        srv.ifc_close()
        cls.tmp.cleanup()

    def setUp(self):
        srv.ifc_open(self.path)

    def _wall_z(self, name):
        import ifcopenshell.geom
        import numpy as np
        gid = j(srv.ifc_list_elements(name_contains=name))["elements"][0]["global_id"]
        s = ifcopenshell.geom.settings()
        s.set("use-world-coords", True)
        v = np.array(ifcopenshell.geom.create_shape(s, srv._find(gid)).geometry.verts).reshape(-1, 3)
        return gid, v.min(0)

    def test_delete_and_save_as(self):
        gid = j(srv.ifc_list_elements(name_contains="Стена 1"))["elements"][0]["global_id"]
        r = j(srv.ifc_delete_elements([gid]))
        self.assertEqual(r["count"], 1)
        self.assertEqual(j(srv.ifc_list_elements(ifc_class="IfcWall"))["total"], 3)
        out = str(Path(self.tmp.name) / "out" / "edited.ifc")
        self.assertTrue(Path(j(srv.ifc_save_as(out))["saved"]).is_file())
        srv.ifc_open(out)
        self.assertEqual(j(srv.ifc_list_elements(ifc_class="IfcWall"))["total"], 3)
        srv.ifc_open(self.path)
        self.assertEqual(j(srv.ifc_list_elements(ifc_class="IfcWall"))["total"], 4)  # исходный файл цел

    def test_delete_spatial_refused(self):
        site = srv.MODEL.file.by_type("IfcSite")[0].GlobalId
        with self.assertRaises(ValueError):
            srv.ifc_delete_elements([site])

    def test_move(self):
        gid, before = self._wall_z("Стена 1")
        srv.ifc_move_elements([gid], dx=10.0, dy=-2.0, dz=1000.0)
        _, after = self._wall_z("Стена 1")
        self.assertAlmostEqual(after[0] - before[0], 10.0, places=3)
        self.assertAlmostEqual(after[1] - before[1], -2.0, places=3)
        self.assertAlmostEqual(after[2] - before[2], 1000.0, places=3)

    def test_save_as_refuses_source_and_existing(self):
        with self.assertRaises(ValueError):
            srv.ifc_save_as(self.path)
        other = Path(self.tmp.name) / "other.ifc"
        other.write_text("x")
        with self.assertRaises(FileExistsError):
            srv.ifc_save_as(str(other))
        srv.ifc_save_as(str(other), overwrite=True)
        self.assertGreater(other.stat().st_size, 10)


class ViewerServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = ifc_samples.build_sample(str(Path(cls.tmp.name) / "sample.ifc"))
        srv.ifc_open(cls.path)
        info = j(srv.ifc_viewer_open())
        cls.url = info["url"]
        cls.info = info
        cls.base, cls.token = cls.url.split("/?t=")

    @classmethod
    def tearDownClass(cls):
        srv.ifc_close()
        cls.tmp.cleanup()

    def get(self, path, token=None):
        t = self.token if token is None else token
        return urllib.request.urlopen(f"{self.base}{path}{'&' if '?' in path else '?'}t={t}")

    def post(self, path, data):
        req = urllib.request.Request(f"{self.base}{path}?t={self.token}", data=json.dumps(data).encode("utf-8"),
                                     method="POST")
        return urllib.request.urlopen(req)

    def test_binds_localhost_and_token(self):
        self.assertTrue(self.url.startswith("http://127.0.0.1:"))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/state", token="wrong")
        self.assertEqual(cm.exception.code, 403)

    def test_model_geometry(self):
        data = j(self.get("/api/model").read().decode("utf-8"))
        self.assertEqual(len(data["elements"]), 5)
        self.assertEqual(self.info["elements_with_geometry"], 5)
        slab = next(e for e in data["elements"] if e["cls"] == "IfcSlab")
        xs = slab["v"][0::3]
        self.assertAlmostEqual(max(xs) - min(xs), 12.36, places=2)
        self.assertTrue(len(slab["f"]) % 3 == 0 and len(slab["color"]) == 3)

    def test_large_coordinates_are_recentered(self):
        far = ifc_samples.build_sample(str(Path(self.tmp.name) / "far.ifc"), offset=(2219200.0, 295580.0, 0.0))
        srv.ifc_open(far)
        info = j(srv.ifc_viewer_open())
        self.assertGreater(info["origin"][0], 2219000.0)
        data = j(self.get("/api/model").read().decode("utf-8"))
        self.assertAlmostEqual(data["origin"][0], info["origin"][0])
        xs = [x for e in data["elements"] for x in e["v"][0::3]]
        self.assertLess(max(abs(x) for x in xs), 100.0)
        srv.ifc_open(self.path)

    def test_viewer_page_served(self):
        html = self.get("/").read().decode("utf-8")
        self.assertIn("Просмотр IFC", html)

    def test_selection_and_messages_roundtrip(self):
        gid = j(srv.ifc_list_elements(ifc_class="IfcSlab"))["elements"][0]["global_id"]
        self.post("/api/selection", {"ids": [gid]})
        sel = j(srv.viewer_get_selection())
        self.assertEqual(sel["count"], 1)
        self.assertEqual(sel["selected"][0]["class"], "IfcSlab")
        self.post("/api/message", {"text": "  сделать толще  "})
        msgs = j(srv.viewer_get_messages(clear=True))
        self.assertEqual(msgs["count"], 1)
        self.assertEqual(msgs["messages"][0]["text"], "сделать толще")
        self.assertEqual(msgs["messages"][0]["selection"], [gid])
        self.assertEqual(j(srv.viewer_get_messages())["count"], 0)

    def test_highlight_visible_to_viewer(self):
        gid = j(srv.ifc_list_elements(ifc_class="IfcWall"))["elements"][0]["global_id"]
        srv.viewer_highlight([gid], "найдено")
        st = j(self.get("/api/state").read().decode("utf-8"))
        self.assertEqual(st["highlight"], [gid])
        self.assertEqual(st["label"], "найдено")
        with self.assertRaises(Exception):
            srv.viewer_highlight(["нет"], "")

    def test_element_endpoint(self):
        gid = j(srv.ifc_list_elements(ifc_class="IfcWall"))["elements"][0]["global_id"]
        d = j(self.get(f"/api/element/{gid}").read().decode("utf-8"))
        self.assertEqual(d["class"], "IfcWall")
        self.assertIn("Pset_WallCommon", d["psets"])


if __name__ == "__main__":
    unittest.main()

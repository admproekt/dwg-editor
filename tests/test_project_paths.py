"""Пути при активном проекте: относительные от корня, запись только внутри и не в sources/."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import dwg_server as srv  # noqa: E402


class ProjectPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        for d in ("sources", "work"):
            (self.root / d).mkdir()
        (self.root / "project.json").write_text("{}", encoding="utf-8")
        os.environ["CAD_PROJECT_ROOT"] = str(self.root)
        srv.SESSIONS.clear()
        srv.CURRENT = None

    def tearDown(self):
        os.environ.pop("CAD_PROJECT_ROOT", None)
        self.tmp.cleanup()

    def test_save_relative_and_reopen(self):
        srv.new_drawing("work/a.dxf")
        srv.add_line([0, 0], [10, 0])
        srv.save_drawing("work/a.dxf")
        self.assertTrue((self.root / "work" / "a.dxf").is_file())
        srv.SESSIONS.clear()
        self.assertIn("a.dxf", srv.open_drawing("work/a.dxf"))

    def test_sources_are_read_only(self):
        srv.new_drawing("work/a.dxf")
        srv.save_drawing("work/a.dxf")
        (self.root / "sources" / "s.dxf").write_bytes((self.root / "work" / "a.dxf").read_bytes())
        srv.open_drawing("sources/s.dxf")
        with self.assertRaises(ValueError):
            srv.save_drawing()  # без имени: перезапись исходника
        with self.assertRaises(ValueError):
            srv.save_drawing("sources/new.dxf")
        srv.save_drawing("work/copy.dxf")
        self.assertTrue((self.root / "work" / "copy.dxf").is_file())

    def test_write_outside_project_refused(self):
        srv.new_drawing("work/a.dxf")
        with self.assertRaises(ValueError):
            srv.save_drawing(str(Path(tempfile.gettempdir()) / "outside_cad_project_test.dxf"))


if __name__ == "__main__":
    unittest.main()

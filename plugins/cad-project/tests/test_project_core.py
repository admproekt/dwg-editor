"""Тесты ядра проекта на временных папках. Домашняя папка пользователя не затрагивается.

Запуск из папки плагина любым Python 3.10+:
    python -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))

import project_core as pc  # noqa: E402
import projectref  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        projectref.ACTIVE_FILE = self.base / "home" / "active.json"
        os.environ.pop("CAD_PROJECT_ROOT", None)

    def tearDown(self):
        self.tmp.cleanup()

    def new(self, name="Тест", **kw):
        r = pc.init_project(str(self.base / "proj"), name, **kw)
        return Path(r["root"]), r


class Init(Base):
    def test_scaffold_and_first_commit(self):
        root, r = self.new(kind="road", crs="МСК-23", units="m")
        for d in ("sources", "work", "models", "views/paths", "recipes", "journal", "skills-draft"):
            self.assertTrue((root / d).is_dir(), d)
        p = json.loads((root / "project.json").read_text(encoding="utf-8"))
        self.assertEqual((p["name"], p["kind"], p["crs"]), ("Тест", "road", "МСК-23"))
        self.assertEqual(len(p["stages"]), 10)
        self.assertIn("Проект «Тест»", (root / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(len(pc.log(root)), 1)
        self.assertEqual(pc.status(root)["git"]["changed"], 0)
        self.assertEqual(r["lfs"], (root / ".gitattributes").exists())

    def test_refuses_second_init_and_bad_kind(self):
        root, _ = self.new()
        with self.assertRaises(pc.ProjectError):
            pc.init_project(str(root), "Ещё")
        with self.assertRaises(pc.ProjectError):
            pc.init_project(str(self.base / "p2"), "X", kind="ship")

    def test_refuses_nested_in_other_repo(self):
        outer = self.base / "outer"
        outer.mkdir()
        subprocess.run(["git", "init", "-q", str(outer)], check=True)
        with self.assertRaises(pc.ProjectError):
            pc.init_project(str(outer / "inner"), "Вложенный")
        r = pc.init_project(str(outer / "inner"), "Вложенный", allow_nested=True)
        self.assertFalse(r["git_new"])

    def test_existing_files_are_not_committed(self):
        d = self.base / "proj"
        d.mkdir()
        (d / "старый.dwg").write_bytes(b"x")
        root, r = self.new()
        self.assertEqual(r["kept_existing"], ["старый.dwg"])
        self.assertIn("старый.dwg", pc.status(root)["git"]["changed_sample"])

    def test_claude_md_keeps_engineer_text(self):
        root, _ = self.new()
        f = root / "CLAUDE.md"
        f.write_text(f.read_text(encoding="utf-8") + "\nМои заметки: не трогать.\n", encoding="utf-8")
        pc.set_fields(root, crs="МСК-61")
        t = f.read_text(encoding="utf-8")
        self.assertIn("Мои заметки: не трогать.", t)
        self.assertIn("МСК-61", t)
        self.assertEqual(t.count(pc.BEGIN), 1)


class Sources(Base):
    def test_add_copy_dedupe_verify(self):
        root, _ = self.new()
        f = self.base / "ГЧ.dwg"
        f.write_bytes(b"abc" * 1000)
        r = pc.add_source(root, str(f), role="drawing", note="генплан")
        self.assertTrue(r["added"])
        self.assertTrue((root / r["path"]).is_file())
        self.assertFalse(pc.add_source(root, str(f))["added"])
        g = self.base / "ГЧ копия.dwg"
        g.write_bytes(b"other")
        self.assertEqual(pc.add_source(root, str(g))["path"], "sources/ГЧ копия.dwg")
        self.assertEqual(pc.verify_sources(root), {"ok": 2, "problems": []})
        (root / r["path"]).write_bytes(b"changed")
        self.assertEqual(pc.verify_sources(root)["problems"][0]["problem"], "хэш не совпал: файл изменён")

    def test_same_name_different_content_gets_suffix(self):
        root, _ = self.new()
        a = self.base / "a" / "план.pdf"
        b = self.base / "b" / "план.pdf"
        a.parent.mkdir(); b.parent.mkdir()
        a.write_bytes(b"1"); b.write_bytes(b"2")
        self.assertEqual(pc.add_source(root, str(a))["path"], "sources/план.pdf")
        self.assertEqual(pc.add_source(root, str(b))["path"], "sources/план_2.pdf")

    def test_link_mode_does_not_copy(self):
        root, _ = self.new()
        f = self.base / "big.ifc"
        f.write_bytes(b"i" * 10)
        r = pc.add_source(root, str(f), copy=False)
        self.assertTrue(r["external"])
        self.assertFalse((root / "sources" / "big.ifc").exists())
        self.assertEqual(pc.verify_sources(root)["ok"], 1)


class JournalGit(Base):
    def test_journal_and_stage(self):
        root, _ = self.new()
        pc.journal_add(root, "урок", "Страницы PDF повёрнуты на 270°", refs=["work/a.py"])
        tail = pc.journal_tail(root)
        self.assertTrue(tail[0].endswith("урок"))
        with self.assertRaises(pc.ProjectError):
            pc.journal_add(root, "шутка", "x")
        pc.set_stage(root, "ingest", "done")
        st = pc.status(root)
        self.assertEqual(st["next_stage"], "georef")
        self.assertIn("ingest: done", st["stages"])
        with self.assertRaises(pc.ProjectError):
            pc.set_stage(root, "nope", "done")

    def test_commit_tag_log(self):
        root, _ = self.new()
        self.assertFalse(pc.commit(root, "ничего")["committed"])
        (root / "work" / "s.py").write_text("print(1)\n", encoding="utf-8")
        r = pc.commit(root, "Скрипт")
        self.assertTrue(r["committed"])
        self.assertEqual(r["sample"], ["work/s.py"])
        pc.tag(root, "v1", "первая версия")
        self.assertEqual(pc.log(root)[0]["message"], "Скрипт")
        with self.assertRaises(pc.ProjectError):
            pc.commit(root, " ")

    def test_renders_are_ignored(self):
        root, _ = self.new()
        (root / "renders" / "f_0001.png").write_bytes(b"png")
        self.assertEqual(pc.status(root)["git"]["changed"], 0)


class Paths(Base):
    def test_resolve_rules(self):
        root, _ = self.new()
        self.assertEqual(projectref.resolve("sources/a.dwg", root=root), (root / "sources" / "a.dwg").resolve())
        self.assertEqual(projectref.resolve("work/x.txt", write=True, root=root), (root / "work" / "x.txt").resolve())
        with self.assertRaises(projectref.ProjectPathError):
            projectref.resolve("sources/a.dwg", write=True, root=root)
        with self.assertRaises(projectref.ProjectPathError):
            projectref.resolve(str(self.base / "elsewhere.dwg"), write=True, root=root)
        self.assertEqual(projectref.resolve(str(self.base / "elsewhere.dwg"), root=root), (self.base / "elsewhere.dwg").resolve())
        with self.assertRaises(projectref.ProjectPathError):
            projectref.resolve("../out.dwg", write=True, root=root)

    def test_find_root_variants(self):
        root, _ = self.new()
        sub = root / "work" / "deep"
        sub.mkdir()
        self.assertEqual(projectref.find_root(sub), root.resolve())
        projectref.set_active(None)
        other = self.base / "other"
        other.mkdir()
        self.assertIsNone(projectref.find_root(other))
        pc.open_project(str(root))
        self.assertEqual(projectref.find_root(other), root.resolve())
        os.environ["CAD_PROJECT_ROOT"] = str(root)
        self.assertEqual(projectref.find_root(other), root.resolve())
        os.environ.pop("CAD_PROJECT_ROOT")
        pc.close_project()
        self.assertIsNone(projectref.find_root(other))

    def test_no_project_keeps_old_behaviour(self):
        d = self.base / "free"
        d.mkdir()
        self.assertEqual(projectref.resolve(str(d / "a.dwg"), write=True, root=None) if False else projectref.resolve(str(d / "a.dwg")), (d / "a.dwg").resolve())

    def test_vendored_copies_are_identical(self):
        mine = (HERE.parent / "server" / "projectref.py").read_bytes()
        for rel in ("../../../server/projectref.py", "../../3d-editor/server/projectref.py"):
            f = (HERE.parent / "server" / rel).resolve()
            if f.exists():
                self.assertEqual(f.read_bytes(), mine, str(f))


if __name__ == "__main__":
    unittest.main()

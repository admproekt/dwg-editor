"""Каталог навыков: формат SKILL.md, проверки и актуальность catalog.json в репозитории."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import skills_catalog as sc  # noqa: E402

GOOD = """---
name: {name}
description: Use for tests. Также для проверок.
metadata:
  kind: {kind}
  drawings: [longitudinal-profile, any]
  tools: [dwg-editor]
  status: draft
  version: 0.1.0
  origin: тест
---

# Навык
"""


def write(base: Path, rel: str, text: str) -> Path:
    f = base / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


class Catalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_frontmatter(self):
        fm = sc.parse_frontmatter(GOOD.format(name="a-b", kind="editing"))
        self.assertEqual(fm["metadata"]["drawings"], ["longitudinal-profile", "any"])
        self.assertEqual(fm["metadata"]["version"], "0.1.0")

    def test_valid_skills_collected_from_both_places(self):
        write(self.base, "skills/one/SKILL.md", GOOD.format(name="one", kind="editing"))
        write(self.base, "plugins/p/skills/two/SKILL.md", GOOD.format(name="two", kind="qa"))
        cat, errs = sc.build(self.base)
        self.assertEqual(errs, [])
        self.assertEqual({e["name"]: e["plugin"] for e in cat["skills"]}, {"one": "dwg-editor", "two": "p"})
        self.assertEqual(len(cat["skills"][0]["sha256"]), 64)

    def test_errors_are_reported(self):
        write(self.base, "skills/wrong/SKILL.md", GOOD.format(name="other", kind="editing"))
        write(self.base, "skills/badkind/SKILL.md", GOOD.format(name="badkind", kind="cooking"))
        write(self.base, "skills/nometa/SKILL.md", "---\nname: nometa\ndescription: x\n---\n# T\n")
        write(self.base, "skills/nofm/SKILL.md", "# нет frontmatter\n")
        _, errs = sc.build(self.base)
        text = "\n".join(errs)
        self.assertIn("не совпадает с папкой", text)
        self.assertIn("metadata.kind", text)
        self.assertIn("нет блока metadata", text)
        self.assertIn("нет frontmatter", text)

    def test_duplicate_names_across_plugins(self):
        write(self.base, "skills/dup/SKILL.md", GOOD.format(name="dup", kind="editing"))
        write(self.base, "plugins/p/skills/dup/SKILL.md", GOOD.format(name="dup", kind="editing"))
        _, errs = sc.build(self.base)
        self.assertTrue(any("уже занято" in e for e in errs))

    def test_too_long_skill_is_flagged(self):
        write(self.base, "skills/long/SKILL.md", GOOD.format(name="long", kind="qa") + "строка\n" * 200)
        _, errs = sc.build(self.base)
        self.assertTrue(any("длиннее" in e for e in errs))

    def test_repository_catalog_is_valid_and_fresh(self):
        cat, errs = sc.build(ROOT)
        self.assertEqual(errs, [])
        self.assertEqual((ROOT / "catalog.json").read_text(encoding="utf-8"),
                         __import__("json").dumps(cat, ensure_ascii=False, indent=2) + "\n")
        self.assertEqual((ROOT / "CATALOG.md").read_text(encoding="utf-8"), sc.render_md(cat))


if __name__ == "__main__":
    unittest.main()

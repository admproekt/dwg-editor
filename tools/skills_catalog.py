"""Каталог навыков маркетплейса: проверка формата и сборка catalog.json и CATALOG.md. Только стандартная библиотека.

    python tools/skills_catalog.py            собрать catalog.json и CATALOG.md
    python tools/skills_catalog.py --check    проверить формат и что каталог актуален (код возврата 1 при ошибках)

Навык — папка с SKILL.md. Служебные поля лежат в блоке `metadata:` внутри frontmatter (обычные `name` и
`description` остаются на верхнем уровне, как требует Claude Code):

    ---
    name: longitudinal-profile
    description: ...
    metadata:
      kind: editing
      drawings: [longitudinal-profile]
      tools: [dwg-editor]
      status: draft
      version: 0.1.0
      origin: dwg-editor
    ---
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

KINDS = {
    "general": "Общие правила работы",
    "ingest": "Ввод чертежей и привязка",
    "editing": "Правка чертежей",
    "geometry": "Геометрия: оси, профили, поверхность, покрытия",
    "modeling": "Моделирование (IFC, объекты)",
    "qa": "Контроль качества: коллизии, проверки",
    "visualization": "Визуализация, виды, рендер, видео",
    "docs": "Документация и отчёты",
}
DRAWINGS = {
    "any": "Любой чертёж",
    "general-plan": "Генеральный план",
    "longitudinal-profile": "Продольный профиль",
    "cross-section": "Поперечные профили и типовые сечения",
    "vertical-planning": "Вертикальная планировка",
    "odd": "ОДД: знаки и разметка",
    "statements": "Ведомости и таблицы",
    "landscape-album": "Альбом благоустройства и парка",
}
STATUSES = ("draft", "reviewed", "stable")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_LINES = 150
MAX_DESC = 1024


def parse_frontmatter(text: str) -> dict:
    """Минимальный разбор: `key: value`, вложенный словарь на один уровень, списки `[a, b]`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("нет frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("frontmatter не закрыт")
    data: dict = {}
    cur = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        key, sep, val = raw.strip().partition(":")
        if not sep:
            raise ValueError(f"строка без двоеточия: {raw!r}")
        val = val.strip()
        if indent == 0:
            if val == "":
                data[key] = cur = {}
            else:
                data[key] = _value(val)
                cur = None
        else:
            if cur is None:
                raise ValueError(f"вложенное поле без родителя: {raw!r}")
            cur[key] = _value(val)
    return data


def _value(v: str):
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [x.strip().strip("\"'") for x in inner.split(",")] if inner else []
    return v.strip("\"'")


def find_skills(repo: Path) -> list:
    out = []
    for pat in ("skills/*/SKILL.md", "plugins/*/skills/*/SKILL.md"):
        out += sorted(repo.glob(pat))
    return out


def plugin_of(path: Path, repo: Path) -> str:
    rel = path.relative_to(repo).parts
    return rel[1] if rel[0] == "plugins" else "dwg-editor"


def check_skill(path: Path, repo: Path) -> tuple:
    """Возвращает (запись каталога, список ошибок)."""
    errs = []
    text = path.read_text(encoding="utf-8-sig")
    try:
        fm = parse_frontmatter(text)
    except ValueError as e:
        return None, [f"{path}: {e}"]
    rel = path.relative_to(repo).as_posix()
    name, desc, meta = fm.get("name"), fm.get("description", ""), fm.get("metadata") or {}
    if not name or not NAME.match(str(name)):
        errs.append(f"{rel}: name должен быть латиницей через дефис")
    elif name != path.parent.name:
        errs.append(f"{rel}: name «{name}» не совпадает с папкой «{path.parent.name}»")
    if not desc:
        errs.append(f"{rel}: нет description")
    elif len(desc) > MAX_DESC:
        errs.append(f"{rel}: description длиннее {MAX_DESC} знаков")
    if not isinstance(meta, dict) or not meta:
        errs.append(f"{rel}: нет блока metadata")
        meta = {}
    kind = meta.get("kind")
    if kind not in KINDS:
        errs.append(f"{rel}: metadata.kind должен быть из {list(KINDS)}")
    drawings = meta.get("drawings", [])
    if not isinstance(drawings, list) or not drawings or any(d not in DRAWINGS for d in drawings):
        errs.append(f"{rel}: metadata.drawings — список из {list(DRAWINGS)}")
    tools = meta.get("tools", [])
    if not isinstance(tools, list):
        errs.append(f"{rel}: metadata.tools — список")
    if meta.get("status") not in STATUSES:
        errs.append(f"{rel}: metadata.status должен быть из {list(STATUSES)}")
    if not SEMVER.match(str(meta.get("version", ""))):
        errs.append(f"{rel}: metadata.version вида 1.2.3")
    if not meta.get("origin"):
        errs.append(f"{rel}: нет metadata.origin (проект или плагин, откуда навык)")
    if len(text.splitlines()) > MAX_LINES:
        errs.append(f"{rel}: длиннее {MAX_LINES} строк, вынесите подробности в отдельные файлы")
    entry = {
        "name": name, "plugin": plugin_of(path, repo), "path": rel, "description": desc,
        "kind": kind, "drawings": drawings, "tools": tools, "status": meta.get("status"),
        "version": meta.get("version"), "origin": meta.get("origin"),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    return entry, errs


def build(repo: Path) -> tuple:
    entries, errs, seen = [], [], {}
    for p in find_skills(repo):
        e, er = check_skill(p, repo)
        errs += er
        if e:
            if e["name"] in seen:
                errs.append(f"{e['path']}: имя «{e['name']}» уже занято ({seen[e['name']]})")
            seen[e["name"]] = e["path"]
            entries.append(e)
    entries.sort(key=lambda e: (e["kind"] or "", e["name"] or ""))
    cat = {"schema": 1, "kinds": KINDS, "drawings": DRAWINGS, "skills": entries}
    return cat, errs


def render_md(cat: dict) -> str:
    lines = ["# Каталог навыков", "", "Файл собирается скриптом `tools/skills_catalog.py`, руками не правится.", ""]
    lines += ["## По видам работ", ""]
    for k, title in KINDS.items():
        rows = [e for e in cat["skills"] if e["kind"] == k]
        if not rows:
            continue
        lines += [f"### {title}", "", "| Навык | Плагин | Типы чертежей | Статус | Версия |", "| --- | --- | --- | --- | --- |"]
        for e in rows:
            lines.append(f"| `{e['name']}` | {e['plugin']} | {', '.join(e['drawings'])} | {e['status']} | {e['version']} |")
        lines.append("")
    lines += ["## По типам чертежей", "", "| Тип чертежа | Навыки |", "| --- | --- |"]
    for d, title in DRAWINGS.items():
        names = [f"`{e['name']}`" for e in cat["skills"] if d in e["drawings"]]
        if names:
            lines.append(f"| {title} | {', '.join(names)} |")
    return "\n".join(lines) + "\n"


def main(argv: list) -> int:
    check = "--check" in argv
    cat, errs = build(REPO)
    cat_f, md_f = REPO / "catalog.json", REPO / "CATALOG.md"
    new_json = json.dumps(cat, ensure_ascii=False, indent=2) + "\n"
    new_md = render_md(cat)
    if check:
        if errs:
            print("\n".join(errs))
        stale = [f.name for f, new in ((cat_f, new_json), (md_f, new_md))
                 if not f.exists() or f.read_text(encoding="utf-8") != new]
        if stale:
            print("Каталог устарел, запустите: python tools/skills_catalog.py  (" + ", ".join(stale) + ")")
        return 1 if errs or stale else 0
    if errs:
        print("Ошибки в навыках:\n" + "\n".join(errs))
        return 1
    cat_f.write_text(new_json, encoding="utf-8")
    md_f.write_text(new_md, encoding="utf-8")
    print(f"Навыков: {len(cat['skills'])}. Записаны catalog.json и CATALOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

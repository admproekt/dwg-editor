"""Активный проект и безопасные пути. Только стандартная библиотека.

Файл один и тот же в плагинах cad-project, dwg-editor и 3d-editor (копия, тест следит за совпадением).
Плагины ставятся независимо, поэтому общий модуль через импорт невозможен.

Как находится проект:
1. переменная среды CAD_PROJECT_ROOT;
2. папка, из которой запущен сервер, и её родители: первая с файлом project.json;
3. проект, который открыл инструмент project_open (запись в ~/.claude/cad-project/active.json).

Если проекта нет, пути работают как раньше. Если проект есть:
- относительный путь отсчитывается от корня проекта;
- писать можно только внутри проекта и не в sources/ (исходники только читаются).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

MARKER = "project.json"
ACTIVE_FILE = Path.home() / ".claude" / "cad-project" / "active.json"
READONLY_DIRS = ("sources",)


class ProjectPathError(ValueError):
    pass


def _is_project(p: Path) -> bool:
    try:
        return (p / MARKER).is_file()
    except OSError:
        return False


def find_root(start: Optional[Path] = None) -> Optional[Path]:
    env = os.environ.get("CAD_PROJECT_ROOT")
    if env and _is_project(Path(env)):
        return Path(env).resolve()
    here = Path(start or Path.cwd()).resolve()
    for p in (here, *here.parents):
        if _is_project(p):
            return p
    try:
        data = json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
        root = Path(data["root"])
        if _is_project(root):
            return root.resolve()
    except (OSError, ValueError, KeyError):
        pass
    return None


def set_active(root: Optional[Path]) -> None:
    ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if root is None:
        ACTIVE_FILE.unlink(missing_ok=True)
        return
    ACTIVE_FILE.write_text(json.dumps({"root": str(Path(root).resolve())}, ensure_ascii=False), encoding="utf-8")


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve(path: str, write: bool = False, root: Optional[Path] = None) -> Path:
    """Путь с учётом проекта. При записи вне проекта или в sources/ бросает ProjectPathError."""
    root = root or find_root()
    p = Path(path).expanduser()
    if root is None:
        return p.resolve()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if write:
        if not _inside(p, root):
            raise ProjectPathError(
                f"Запись вне проекта запрещена: {p}. Проект: {root}. Сохраните внутрь проекта (work, models, views) "
                "или закройте проект инструментом project_close.")
        for d in READONLY_DIRS:
            if _inside(p, root / d):
                raise ProjectPathError(
                    f"{d}/ только для чтения (исходные данные): {p}. Сохраните результат в work/ или models/.")
    return p

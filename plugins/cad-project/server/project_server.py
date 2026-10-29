"""MCP-сервер плагина cad-project: проект инженера как git-репозиторий. Логика в project_core.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

import project_core as pc
import projectref

mcp = FastMCP("cad-project")


def _out(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _root() -> Path:
    r = projectref.find_root()
    if r is None:
        raise pc.ProjectError("Нет активного проекта. Создайте его (project_init) или откройте (project_open).")
    return r


def _safe(fn, *a, **kw) -> str:
    try:
        return _out(fn(*a, **kw))
    except (pc.ProjectError, projectref.ProjectPathError) as e:
        return _out({"error": str(e)})


@mcp.tool()
def project_init(path: str, name: str, kind: str = "other", crs: str = "", units: str = "m",
                 allow_nested: bool = False) -> str:
    """Создать проект в папке: каркас, git init без origin, правила LFS, первый коммит. Папка должна быть вне других
    репозиториев (например C:\\Projects\\<имя>). kind: road, landscape, building, other. Существующие файлы в папке
    не трогаются и в первый коммит не попадают. Проект становится активным."""
    return _safe(pc.init_project, path, name, kind, crs, units, allow_nested)


@mcp.tool()
def project_open(path: str) -> str:
    """Открыть проект (папка с project.json) и сделать активным для остальных плагинов. Возвращает статус."""
    return _safe(pc.open_project, path)


@mcp.tool()
def project_close() -> str:
    """Снять активный проект: пути других плагинов снова работают как раньше, без ограничений проекта."""
    return _safe(pc.close_project)


@mcp.tool()
def project_status() -> str:
    """Состояние проекта: стадии и следующая, git (ветка, изменения, последние коммиты), число исходников, журнал."""
    return _safe(lambda: pc.status(_root()))


@mcp.tool()
def project_set(name: Optional[str] = None, kind: Optional[str] = None, crs: Optional[str] = None,
                units: Optional[str] = None) -> str:
    """Изменить имя, вид, систему координат или единицы проекта. Блок в CLAUDE.md обновится, текст инженера останется."""
    return _safe(lambda: pc.set_fields(_root(), name, kind, crs, units))


@mcp.tool()
def project_stage(stage: str, status: str) -> str:
    """Отметить стадию проекта: ingest, georef, geometry, model, check, views, camera, render, video, release.
    status: todo, doing, done. Запись попадёт в журнал."""
    return _safe(lambda: pc.set_stage(_root(), stage, status))


@mcp.tool()
def source_add(file: str, role: str = "other", note: str = "", copy: bool = True, subdir: str = "") -> str:
    """Внести исходный файл (чертёж, том, съёмку) в sources/: копия, хэш, роль и заметка в sources/sources.json.
    Повторное добавление того же содержимого игнорируется. copy=false только записывает ссылку на внешний файл
    (для очень больших файлов; проект тогда не самодостаточен)."""
    return _safe(lambda: pc.add_source(_root(), file, role, note, copy, subdir))


@mcp.tool()
def sources_verify() -> str:
    """Проверить, что исходники на месте и не изменены (по хэшу)."""
    return _safe(lambda: pc.verify_sources(_root()))


@mcp.tool()
def journal_add(kind: str, text: str, refs: Optional[list[str]] = None) -> str:
    """Запись в журнал проекта (journal/ГГГГ-ММ-ДД.md). kind: решение, ошибка, урок, проверка, заметка.
    Пишите сразу, как только принято решение или найдена ошибка: из журнала потом рождаются навыки."""
    return _safe(lambda: pc.journal_add(_root(), kind, text, refs))


@mcp.tool()
def project_commit(message: str, paths: Optional[list[str]] = None) -> str:
    """Закоммитить изменения проекта (всё, что не в .gitignore, либо только paths). Ничего не отправляет."""
    return _safe(lambda: pc.commit(_root(), message, paths))


@mcp.tool()
def project_tag(name: str, message: str = "") -> str:
    """Отметить версию проекта тегом (например v1-model после сборки IFC)."""
    return _safe(lambda: pc.tag(_root(), name, message))


@mcp.tool()
def project_publish(url: str, remote: str = "origin", push: bool = True) -> str:
    """Подключить проект к серверу git (например Gitea в локальной сети) и отправить ветку, теги и файлы LFS.
    url: адрес пустого репозитория на сервере. push=false только подключает, не отправляя. Вызывайте только по
    просьбе инженера: это отправка данных проекта на сервер. Первая отправка больших файлов бывает долгой."""
    return _safe(lambda: pc.publish_project(_root(), url, remote, push))


@mcp.tool()
def project_clone(url: str, path: str, allow_nested: bool = False) -> str:
    """Скопировать проект с сервера git в новую пустую папку (например C:\\Projects\\<имя>), скачать файлы LFS,
    проверить исходники и сделать проект активным. Активный проект для этого не нужен."""
    return _safe(pc.clone_project, url, path, allow_nested)


@mcp.tool()
def project_log(n: int = 10) -> str:
    """Последние коммиты проекта."""
    return _safe(lambda: pc.log(_root(), n))


if __name__ == "__main__":
    mcp.run()

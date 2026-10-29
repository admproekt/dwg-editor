"""Ядро проекта: каркас, исходники, журнал, git. Только стандартная библиотека, поэтому тестируется без MCP."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import projectref

SCHEMA = 1
KINDS = ("road", "landscape", "building", "other")
STAGES = (
    ("ingest", "Ввод"), ("georef", "Привязка"), ("geometry", "Геометрия"), ("model", "Модель"),
    ("check", "Проверка"), ("views", "Виды"), ("camera", "Путь камеры"), ("render", "Рендер"),
    ("video", "Видео"), ("release", "Выпуск"),
)
STAGE_STATUS = ("todo", "doing", "done")
JOURNAL_KINDS = ("решение", "ошибка", "урок", "проверка", "заметка")
DIRS = ("sources", "work", "models", "views/paths", "recipes", "renders", "journal", "skills-draft", ".claude/skills")
LFS_PATTERNS = ("*.dwg", "*.dxf", "*.pdf", "*.ifc", "*.ifczip", "*.obj", "*.glb", "*.blend", "*.tif", "*.tiff",
                "*.mp4", "*.xls", "*.xlsx", "*.dms")
GITIGNORE = "renders/\nwork/tmp/\n__pycache__/\n*.pyc\n*.log\n*.bak\n.venv/\n"
BEGIN, END = "<!-- cad-project:begin -->", "<!-- cad-project:end -->"


class ProjectError(Exception):
    pass


# ------------------------------------------------------------------------------ git

def git(root: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(root), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise ProjectError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def have_git() -> bool:
    return shutil.which("git") is not None


def have_lfs() -> bool:
    if not have_git():
        return False
    r = subprocess.run(["git", "lfs", "version"], capture_output=True, text=True)
    return r.returncode == 0


def _toplevel(p: Path) -> Optional[Path]:
    q = p
    while not q.exists() and q != q.parent:
        q = q.parent
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(q), capture_output=True, text=True,
                       encoding="utf-8")
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


# ------------------------------------------------------------------------------ файлы проекта

def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read(root: Path) -> dict:
    f = root / projectref.MARKER
    if not f.is_file():
        raise ProjectError(f"Это не проект: нет {projectref.MARKER} в {root}")
    return json.loads(f.read_text(encoding="utf-8"))


def _write(root: Path, data: dict) -> None:
    (root / projectref.MARKER).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _claude_block(p: dict) -> str:
    kinds = {"road": "дорога", "landscape": "парк и благоустройство", "building": "здание", "other": "другое"}
    return "\n".join([
        BEGIN,
        f"# Проект «{p['name']}»",
        "",
        f"Вид: {kinds.get(p['kind'], p['kind'])}. Система координат: {p.get('crs') or 'не задана'}. Единицы: {p.get('units') or 'не заданы'}.",
        "",
        "Правила работы в проекте:",
        "- Пути давайте относительно корня проекта. Писать можно только внутри проекта и не в `sources/`: исходные чертежи только читаются.",
        "- Новый исходник сначала внесите инструментом `source_add`, он копирует файл в `sources/` и записывает хэш.",
        "- Решения, ошибки и уроки записывайте в журнал инструментом `journal_add`, сразу, а не в конце.",
        "- Скрипты и промежуточные данные держите в `work/`, модели в `models/`, виды и пути камеры в `views/`.",
        "- Коммитьте в конце стадии или по просьбе инженера (`project_commit`). Статус и следующий шаг показывает `project_status`.",
        "- Вывод, сделанный из одного образца, помечайте как предположение и проверяйте на следующем чертеже.",
        END,
        "",
    ])


def write_claude_md(root: Path) -> None:
    """Обновляет только блок между маркерами, остальной текст инженера не трогает."""
    block = _claude_block(_read(root))
    f = root / "CLAUDE.md"
    old = f.read_text(encoding="utf-8") if f.exists() else ""
    if BEGIN in old and END in old:
        a, b = old.index(BEGIN), old.index(END) + len(END)
        new = old[:a] + block.rstrip("\n") + old[b:]
    else:
        new = block + ("\n" + old if old else "")
    f.write_text(new, encoding="utf-8")


def _ensure_identity(root: Path) -> Optional[str]:
    if git(root, "config", "user.name", check=False) and git(root, "config", "user.email", check=False):
        return None
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "engineer"
    git(root, "config", "--local", "user.name", user)
    git(root, "config", "--local", "user.email", f"{user}@local")
    return f"в git не задано имя автора, поставлено локально: {user} (git config --global user.name, чтобы задать своё)"


def init_project(path: str, name: str, kind: str = "other", crs: str = "", units: str = "m",
                 allow_nested: bool = False) -> dict:
    if not have_git():
        raise ProjectError("Не найден git. Установите git (winget install Git.Git) и перезапустите приложение.")
    if kind not in KINDS:
        raise ProjectError(f"kind должен быть один из {KINDS}")
    root = Path(path).expanduser().resolve()
    if (root / projectref.MARKER).exists():
        raise ProjectError(f"Здесь уже проект: {root}. Откройте его инструментом project_open.")
    top = _toplevel(root)
    if top is not None and top != root and not allow_nested:
        raise ProjectError(
            f"Папка внутри другого git-репозитория ({top}). Проект должен быть отдельным репозиторием: "
            "выберите папку вне него, например C:\\Projects\\<имя>.")
    root.mkdir(parents=True, exist_ok=True)
    existing = [x.name for x in root.iterdir() if x.name != ".git"]
    for d in DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
        keep = root / d / ".gitkeep"
        if d in ("sources", "views/paths", "recipes", "journal", "skills-draft", ".claude/skills", "models", "work"):
            keep.touch()
    data = {"schema": SCHEMA, "name": name, "kind": kind, "crs": crs, "units": units, "created": _now(),
            "stages": [{"id": i, "name": n, "status": "todo"} for i, n in STAGES], "skills": []}
    _write(root, data)
    (root / "sources" / "sources.json").write_text(json.dumps({"schema": SCHEMA, "items": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    lfs = have_lfs()
    if lfs:
        (root / ".gitattributes").write_text(
            "".join(f"{pat} filter=lfs diff=lfs merge=lfs -text\n" for pat in LFS_PATTERNS), encoding="utf-8")
    write_claude_md(root)
    notes = []
    if top is None:
        git(root, "init", "-b", "main")
    if lfs:
        git(root, "lfs", "install", "--local")
    note = _ensure_identity(root)
    if note:
        notes.append(note)
    if not lfs:
        notes.append("git-lfs не найден: большие файлы пока хранятся обычным git. Установите git-lfs и включите правила.")
    scaffold = [projectref.MARKER, "CLAUDE.md", ".gitignore", "sources/sources.json"] + \
        ([".gitattributes"] if lfs else []) + [f"{d}/.gitkeep" for d in DIRS if (root / d / ".gitkeep").exists()]
    git(root, "add", "--", *scaffold)
    git(root, "commit", "-m", f"Проект «{name}»: каркас")
    projectref.set_active(root)
    return {"root": str(root), "lfs": lfs, "git_new": top is None, "kept_existing": existing, "notes": notes}


def open_project(path: str) -> dict:
    root = Path(path).expanduser().resolve()
    _read(root)
    projectref.set_active(root)
    return status(root)


def close_project() -> dict:
    projectref.set_active(None)
    return {"active": None}


def set_fields(root: Path, name: Optional[str] = None, kind: Optional[str] = None, crs: Optional[str] = None,
               units: Optional[str] = None) -> dict:
    p = _read(root)
    if kind is not None and kind not in KINDS:
        raise ProjectError(f"kind должен быть один из {KINDS}")
    for k, v in (("name", name), ("kind", kind), ("crs", crs), ("units", units)):
        if v is not None:
            p[k] = v
    _write(root, p)
    write_claude_md(root)
    return {k: p[k] for k in ("name", "kind", "crs", "units")}


def set_stage(root: Path, stage: str, status_: str) -> dict:
    p = _read(root)
    if status_ not in STAGE_STATUS:
        raise ProjectError(f"status должен быть один из {STAGE_STATUS}")
    for s in p["stages"]:
        if s["id"] == stage:
            s["status"] = status_
            _write(root, p)
            journal_add(root, "заметка", f"Стадия «{s['name']}»: {status_}")
            return {"stage": stage, "status": status_}
    raise ProjectError(f"Нет стадии {stage}. Есть: {[s['id'] for s in p['stages']]}")


# ------------------------------------------------------------------------------ исходники

def _sha256(f: Path) -> str:
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def add_source(root: Path, file: str, role: str = "other", note: str = "", copy: bool = True,
               subdir: str = "") -> dict:
    src = Path(file).expanduser().resolve()
    if not src.is_file():
        raise ProjectError(f"Нет файла: {src}")
    reg_f = root / "sources" / "sources.json"
    reg = json.loads(reg_f.read_text(encoding="utf-8"))
    digest = _sha256(src)
    for it in reg["items"]:
        if it["sha256"] == digest:
            return {"added": False, "reason": "такой файл уже есть", "path": it["path"], "sha256": digest}
    if copy:
        dest_dir = (root / "sources" / subdir).resolve()
        if not projectref._inside(dest_dir, root / "sources"):
            raise ProjectError("subdir должен оставаться внутри sources/")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        n = 2
        while dest.exists():
            dest = dest_dir / f"{src.stem}_{n}{src.suffix}"
            n += 1
        shutil.copy2(src, dest)
        rel = dest.relative_to(root).as_posix()
    else:
        rel = str(src)
    item = {"path": rel, "sha256": digest, "size": src.stat().st_size, "role": role, "note": note,
            "added": _now(), "from": str(src), "external": not copy}
    reg["items"].append(item)
    reg_f.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"added": True, **item}


def list_sources(root: Path) -> list:
    return json.loads((root / "sources" / "sources.json").read_text(encoding="utf-8"))["items"]


def verify_sources(root: Path) -> dict:
    bad, ok = [], 0
    for it in list_sources(root):
        f = Path(it["path"]) if it.get("external") else root / it["path"]
        if not f.is_file():
            bad.append({"path": it["path"], "problem": "нет файла"})
        elif _sha256(f) != it["sha256"]:
            bad.append({"path": it["path"], "problem": "хэш не совпал: файл изменён"})
        else:
            ok += 1
    return {"ok": ok, "problems": bad}


# ------------------------------------------------------------------------------ журнал

def journal_add(root: Path, kind: str, text: str, refs: Optional[list] = None) -> dict:
    if kind not in JOURNAL_KINDS:
        raise ProjectError(f"kind должен быть один из {JOURNAL_KINDS}")
    if not text.strip():
        raise ProjectError("Пустая запись")
    now = dt.datetime.now()
    f = root / "journal" / f"{now:%Y-%m-%d}.md"
    head = "" if f.exists() else f"# Журнал {now:%Y-%m-%d}\n"
    entry = f"\n## {now:%H:%M} · {kind}\n{text.strip()}\n" + (f"\nСсылки: {', '.join(refs)}\n" if refs else "")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(head + entry)
    return {"file": f.relative_to(root).as_posix()}


def journal_tail(root: Path, n: int = 5) -> list:
    out = []
    for f in sorted((root / "journal").glob("*.md"), reverse=True):
        heads = [l[3:] for l in f.read_text(encoding="utf-8").splitlines() if l.startswith("## ")]
        out += [f"{f.stem} {h}" for h in reversed(heads)]
        if len(out) >= n:
            break
    return out[:n]


# ------------------------------------------------------------------------------ коммиты

def commit(root: Path, message: str, paths: Optional[list] = None) -> dict:
    if not message.strip():
        raise ProjectError("Нужно сообщение коммита")
    git(root, "add", "--", *paths) if paths else git(root, "add", "-A")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(root)).returncode == 0:
        return {"committed": False, "reason": "нет изменений"}
    files = git(root, "diff", "--cached", "--name-only").splitlines()
    git(root, "commit", "-m", message)
    return {"committed": True, "commit": git(root, "rev-parse", "--short", "HEAD"), "files": len(files),
            "sample": files[:10]}


def tag(root: Path, name: str, message: str = "") -> dict:
    git(root, "tag", "-a", name, "-m", message or name)
    return {"tag": name, "commit": git(root, "rev-parse", "--short", "HEAD")}


def log(root: Path, n: int = 10) -> list:
    out = git(root, "log", f"-{n}", "--pretty=format:%h|%ad|%s", "--date=short", check=False)
    return [dict(zip(("commit", "date", "message"), l.split("|", 2))) for l in out.splitlines() if l]


# ------------------------------------------------------------------------------ сервер (publish, clone)

NET_TIMEOUT = 3 * 3600  # первая отправка больших файлов бывает долгой


def _net(args: list, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """git с обращением к серверу: без текстового запроса пароля (запрос покажет менеджер учётных данных)."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(cwd) if cwd else None, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=NET_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ProjectError("git не ответил за отведённое время. Для первой отправки больших файлов выполните "
                           "команду в терминале: git push -u origin main")


def _net_fail(what: str, r: subprocess.CompletedProcess) -> ProjectError:
    msg = (r.stderr or r.stdout).strip()
    hint = ""
    low = msg.lower()
    if "authentication" in low or "could not read username" in low or "403" in low or "401" in low:
        hint = " Войдите на сервер: выполните в терминале `git ls-remote <адрес>` и введите логин и токен, они запомнятся."
    elif "could not resolve host" in low or "unable to access" in low or "connection" in low:
        hint = " Проверьте адрес сервера, что он включён и порт открыт в брандмауэре."
    elif "rejected" in low or "non-fast-forward" in low or "fetch first" in low:
        hint = " На сервере уже есть другая история. Если репозиторий должен быть пустым, пересоздайте его без README."
    return ProjectError(f"{what}: {msg}{hint}")


def _uses_lfs(root: Path) -> bool:
    f = root / ".gitattributes"
    return f.is_file() and "filter=lfs" in f.read_text(encoding="utf-8", errors="replace")


def publish_project(root: Path, url: str, remote: str = "origin", push: bool = True) -> dict:
    """Подключить сервер и отправить текущую ветку (и теги). Файлы LFS уходят вместе с отправкой."""
    url = url.strip()
    if not url:
        raise ProjectError("Нужен адрес репозитория на сервере, например http://сервер:3000/проект/имя.git")
    if _uses_lfs(root) and not have_lfs():
        raise ProjectError("В проекте большие файлы идут через Git LFS, а git-lfs не найден. "
                           "Установите git-lfs (winget install GitHub.GitLFS) и повторите.")
    branch = git(root, "branch", "--show-current", check=False)
    if not branch:
        raise ProjectError("Нет текущей ветки (отсоединённый HEAD). Переключитесь на main.")
    cur = git(root, "remote", "get-url", remote, check=False)
    if cur and cur != url:
        raise ProjectError(f"Удалённый «{remote}» уже указывает на {cur}. Чтобы сменить: "
                           f"git remote set-url {remote} {url}")
    r = _net(["ls-remote", url])
    if r.returncode != 0:
        raise _net_fail("Сервер недоступен или нет доступа", r)
    added = not cur
    if added:
        git(root, "remote", "add", remote, url)
    out = {"remote": remote, "url": url, "branch": branch, "remote_added": added, "pushed": False,
           "uncommitted": len(git(root, "status", "--porcelain", check=False).splitlines())}
    if not push:
        return out
    if _uses_lfs(root):
        git(root, "lfs", "install", "--local")
    r = _net(["push", "-u", remote, branch], cwd=root)
    if r.returncode != 0:
        raise _net_fail("Отправка не удалась", r)
    if git(root, "tag", "--list", check=False):
        r = _net(["push", remote, "--tags"], cwd=root)
        if r.returncode != 0:
            raise _net_fail("Ветка отправлена, теги нет", r)
    out["pushed"] = True
    out["lfs"] = _uses_lfs(root)
    if out["uncommitted"]:
        out["note"] = f"Не закоммичено файлов: {out['uncommitted']}. На сервер ушло только закоммиченное."
    return out


def clone_project(url: str, path: str, allow_nested: bool = False) -> dict:
    """Скопировать проект с сервера в папку, подтянуть файлы LFS, сделать проект активным."""
    if not have_git():
        raise ProjectError("Не найден git. Установите git (winget install Git.Git) и перезапустите приложение.")
    url = url.strip()
    if not url:
        raise ProjectError("Нужен адрес репозитория на сервере")
    dest = Path(path).expanduser().resolve()
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ProjectError(f"Папка не пуста: {dest}. Выберите новую, например C:\\Projects\\<имя>.")
    top = _toplevel(dest)
    if top is not None and not allow_nested:
        raise ProjectError(f"Папка внутри другого git-репозитория ({top}). Выберите папку вне него.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = _net(["clone", url, str(dest)], cwd=dest.parent)
    if r.returncode != 0:
        raise _net_fail("Не удалось скопировать проект", r)
    notes = []
    if _uses_lfs(dest):
        if have_lfs():
            git(dest, "lfs", "install", "--local")
            r = _net(["lfs", "pull"], cwd=dest)
            if r.returncode != 0:
                notes.append(f"Файлы LFS не скачались: {(r.stderr or r.stdout).strip()}. Повторите: git lfs pull")
        else:
            notes.append("git-lfs не найден: большие файлы остались заглушками. Установите git-lfs и выполните git lfs pull.")
    if not (dest / projectref.MARKER).is_file():
        raise ProjectError(f"Скопировано в {dest}, но это не проект cad-project: нет {projectref.MARKER}.")
    ident = _ensure_identity(dest)
    if ident:
        notes.append(ident)
    projectref.set_active(dest)
    return {"root": str(dest), "sources": verify_sources(dest), "notes": notes, "status": status(dest)}


# ------------------------------------------------------------------------------ статус

def _remote_info(root: Path) -> dict:
    url = git(root, "remote", "get-url", "origin", check=False)
    if not url:
        return {"url": None}
    info = {"url": url}
    ab = git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD", check=False).split()
    if len(ab) == 2:
        info["behind"], info["ahead"] = int(ab[0]), int(ab[1])
    return info


def status(root: Path) -> dict:
    p = _read(root)
    ch = git(root, "status", "--porcelain", check=False).splitlines()
    nxt = next((s for s in p["stages"] if s["status"] != "done"), None)
    return {
        "project": {k: p[k] for k in ("name", "kind", "crs", "units", "created")},
        "root": str(root),
        "stages": [f"{s['id']}: {s['status']}" for s in p["stages"]],
        "next_stage": nxt["id"] if nxt else None,
        "git": {"branch": git(root, "branch", "--show-current", check=False), "changed": len(ch),
                "changed_sample": [l[3:] for l in ch[:8]], "last": log(root, 5), "remote": _remote_info(root)},
        "sources": len(list_sources(root)),
        "journal_last": journal_tail(root, 3),
        "skills": p.get("skills", []),
        "lfs": have_lfs(),
    }

"""Запуск MCP-сервера cad-project: при первом старте создаёт venv и ставит зависимости.

stdout принадлежит MCP-протоколу, поэтому весь вывод pip уходит в stderr или в лог.

Установка занимает дольше, чем Claude Code ждёт подключения сервера, поэтому на первом старте она
идёт отдельным процессом, а пока отвечает лёгкая заглушка MCP (только стандартная библиотека)
с одним инструментом `status`.
"""
import json
import os
import subprocess
import sys
import time
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or Path.home() / ".claude" / "cad-project")
VENV = DATA / "venv"
STAMP = VENV / ".requirements-installed"
REQ = HERE / "requirements.txt"
LOCK = DATA / "install.lock"
LOG = DATA / "install.log"
LOCK_STALE_SECONDS = 15 * 60


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def deps_ready() -> bool:
    return STAMP.exists() and STAMP.read_text() == REQ.read_text()


def ensure_env() -> None:
    if deps_ready():
        return
    DATA.mkdir(parents=True, exist_ok=True)
    if not venv_python().exists():
        print("cad-project: создаю виртуальное окружение...", file=sys.stderr)
        venv.create(VENV, with_pip=True)
    print("cad-project: устанавливаю зависимости...", file=sys.stderr)
    subprocess.run(
        [str(venv_python()), "-m", "pip", "install", "--quiet", "--default-timeout=120", "--retries", "10",
         "-r", str(REQ)],
        check=True,
        stdout=sys.stderr,
    )
    STAMP.write_text(REQ.read_text())


def install_running() -> bool:
    try:
        return time.time() - LOCK.stat().st_mtime < LOCK_STALE_SECONDS
    except FileNotFoundError:
        return False


def install_main() -> int:
    """Тело фонового процесса установки (`launch.py --install`)."""
    DATA.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    try:
        ensure_env()
        return 0
    except Exception as exc:
        print(f"cad-project: установка не удалась: {exc}", file=sys.stderr)
        return 1
    finally:
        LOCK.unlink(missing_ok=True)


def start_install() -> None:
    if install_running():
        return
    DATA.mkdir(parents=True, exist_ok=True)
    LOCK.write_text("starting")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with open(LOG, "w", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--install"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )


def status_text() -> str:
    if deps_ready():
        return "Зависимости установлены. Перезапустите сервер cad-project (или приложение), чтобы подключить инструменты."
    if install_running():
        return "Идёт установка зависимостей cad-project (mcp). Подождите пару минут и вызовите status снова."
    tail = LOG.read_text(encoding="utf-8", errors="replace")[-1500:] if LOG.exists() else ""
    return f"Установка зависимостей не завершена и не запущена. Лог ({LOG}):\n{tail}"


def serve_stub() -> None:
    """Минимальный MCP поверх stdio: JSON-RPC, по одному сообщению в строке."""
    tool = {
        "name": "status",
        "description": "Состояние установки зависимостей cad-project",
        "inputSchema": {"type": "object", "properties": {}},
    }
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            continue
        if method == "initialize":
            version = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cad-project", "version": "bootstrap"},
            }
        elif method == "tools/list":
            result = {"tools": [tool]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": status_text()}]}
        elif method == "ping":
            result = {}
        else:
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}}
            print(json.dumps(reply), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}), flush=True)


if __name__ == "__main__":
    if "--install" in sys.argv:
        sys.exit(install_main())
    if deps_ready():
        sys.exit(subprocess.call([str(venv_python()), str(HERE / "project_server.py")]))
    start_install()
    serve_stub()

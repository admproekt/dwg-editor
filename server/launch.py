"""Запуск MCP-сервера: при первом старте создаёт venv и ставит зависимости.

stdout принадлежит MCP-протоколу, поэтому весь вывод pip уходит в stderr.
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or Path.home() / ".claude" / "dwg-editor")
VENV = DATA / "venv"
STAMP = VENV / ".requirements-installed"
REQ = HERE / "requirements.txt"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_env() -> None:
    if STAMP.exists() and STAMP.read_text() == REQ.read_text():
        return
    DATA.mkdir(parents=True, exist_ok=True)
    if not venv_python().exists():
        print("dwg-editor: создаю виртуальное окружение...", file=sys.stderr)
        venv.create(VENV, with_pip=True)
    print("dwg-editor: устанавливаю зависимости...", file=sys.stderr)
    subprocess.run(
        [str(venv_python()), "-m", "pip", "install", "--quiet", "-r", str(REQ)],
        check=True,
        stdout=sys.stderr,
    )
    STAMP.write_text(REQ.read_text())


if __name__ == "__main__":
    ensure_env()
    sys.exit(subprocess.call([str(venv_python()), str(HERE / "dwg_server.py")]))

"""MCP-сервер плагина 3d-editor: чтение IFC и просмотр модели в браузере.

Чтение и геометрия через IfcOpenShell. Просмотрщик это локальный HTTP-сервер внутри этого же процесса:
слушает только 127.0.0.1, требует случайный токен в адресе, ничего не пишет в IFC.
"""
from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as uel
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("3d-editor")

VIEWER_DIR = Path(__file__).resolve().parent / "viewer"

CLASS_COLORS = {
    "IfcWall": (0.78, 0.76, 0.72), "IfcWallStandardCase": (0.78, 0.76, 0.72), "IfcSlab": (0.62, 0.64, 0.68),
    "IfcBeam": (0.55, 0.60, 0.70), "IfcColumn": (0.60, 0.62, 0.66), "IfcFooting": (0.55, 0.55, 0.52),
    "IfcDoor": (0.60, 0.42, 0.25), "IfcWindow": (0.45, 0.70, 0.85), "IfcRoof": (0.70, 0.40, 0.35),
    "IfcStair": (0.70, 0.66, 0.55), "IfcRailing": (0.50, 0.50, 0.55), "IfcPlate": (0.70, 0.70, 0.40),
    "IfcMember": (0.55, 0.60, 0.70), "IfcCovering": (0.75, 0.75, 0.70), "IfcOpeningElement": (0.9, 0.5, 0.5),
}
DEFAULT_COLOR = (0.70, 0.70, 0.70)


# ------------------------------------------------------------------------------ состояние

@dataclass
class Model:
    path: Path
    file: Any
    meshes: Optional[list] = None  # кэш геометрии для просмотрщика


@dataclass
class ViewerState:
    selection: list = field(default_factory=list)
    selection_note: str = ""
    selection_time: float = 0.0
    messages: list = field(default_factory=list)
    highlight: list = field(default_factory=list)
    highlight_label: str = ""
    version: int = 0


MODEL: Optional[Model] = None
VIEWER = ViewerState()
_SERVER: Optional[ThreadingHTTPServer] = None
_TOKEN = ""
_LOCK = threading.RLock()


def _model() -> Model:
    if MODEL is None:
        raise RuntimeError("IFC не открыт. Сначала вызовите ifc_open(path).")
    return MODEL


def _out(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _storey_name(element) -> Optional[str]:
    try:
        c = uel.get_container(element)
        while c is not None and not c.is_a("IfcBuildingStorey"):
            c = uel.get_aggregate(c) if hasattr(uel, "get_aggregate") else None
        return c.Name if c is not None else None
    except Exception:
        return None


def _brief(e) -> dict:
    return {"global_id": e.GlobalId, "class": e.is_a(), "name": e.Name, "storey": _storey_name(e)}


def _find(global_id: str):
    m = _model()
    try:
        return m.file.by_guid(global_id)
    except Exception:
        raise KeyError(f"Элемент {global_id} не найден")


# ---------------------------------------------------------------------------- инструменты

@mcp.tool()
def ifc_open(path: str) -> str:
    """Открыть файл IFC и вернуть сводку: схема, проект, этажи, число элементов по классам.
    Открытая модель становится текущей (закрывает предыдущую)."""
    global MODEL
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"Файл не найден: {p}")
    if p.suffix.lower() != ".ifc":
        raise ValueError("Поддерживается только .ifc")
    f = ifcopenshell.open(str(p))
    with _LOCK:
        MODEL = Model(path=p.resolve(), file=f)
        VIEWER.__init__()
        VIEWER.version = 1
    return _out(_summary(MODEL))


def _summary(m: Model) -> dict:
    f = m.file
    projects = f.by_type("IfcProject")
    counts: dict[str, int] = {}
    for e in f.by_type("IfcProduct"):
        if e.is_a("IfcSpatialElement") or e.is_a("IfcOpeningElement"):
            continue
        counts[e.is_a()] = counts.get(e.is_a(), 0) + 1
    storeys = []
    for s in sorted(f.by_type("IfcBuildingStorey"), key=lambda s: (s.Elevation or 0)):
        n = len(uel.get_decomposition(s)) if hasattr(uel, "get_decomposition") else 0
        storeys.append({"name": s.Name, "elevation": s.Elevation, "elements": n})
    return {
        "file": str(m.path), "schema": f.schema,
        "project": projects[0].Name if projects else None,
        "sites": [s.Name for s in f.by_type("IfcSite")],
        "buildings": [b.Name for b in f.by_type("IfcBuilding")],
        "storeys": storeys,
        "elements_by_class": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "total_elements": sum(counts.values()),
    }


@mcp.tool()
def ifc_close() -> str:
    """Закрыть текущую модель."""
    global MODEL
    with _LOCK:
        MODEL = None
        VIEWER.__init__()
    return "Модель закрыта"


@mcp.tool()
def ifc_list_elements(ifc_class: Optional[str] = None, storey: Optional[str] = None,
                      name_contains: Optional[str] = None, limit: int = 100, offset: int = 0) -> str:
    """Элементы модели с GlobalId, классом, именем и этажом. Фильтры: ifc_class (например IfcWall,
    включая подклассы), storey (имя этажа), name_contains (подстрока в имени, без учёта регистра).
    Постранично через limit и offset."""
    m = _model()
    cls = ifc_class or "IfcProduct"
    items = []
    for e in m.file.by_type(cls):
        if e.is_a("IfcSpatialElement") or e.is_a("IfcOpeningElement"):
            continue
        if name_contains and name_contains.lower() not in (e.Name or "").lower():
            continue
        b = _brief(e)
        if storey and b["storey"] != storey:
            continue
        items.append(b)
    return _out({"total": len(items), "offset": offset, "elements": items[offset:offset + limit]})


@mcp.tool()
def ifc_get_element(global_id: str) -> str:
    """Все атрибуты элемента, его наборы свойств (Pset) и количества по GlobalId."""
    e = _find(global_id)
    attrs = {}
    for k, v in e.get_info(recursive=False).items():
        if k in ("id", "type"):
            continue
        attrs[k] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
    try:
        psets = uel.get_psets(e)
    except Exception:
        psets = {}
    return _out({"class": e.is_a(), "attributes": attrs, "psets": psets, "storey": _storey_name(e)})


# ---------------------------------------------------------------------------- геометрия

def _style_color(shape, cls: str) -> tuple:
    try:
        mats = shape.geometry.materials
        if mats:
            d = mats[0].diffuse
            c = (d.r(), d.g(), d.b()) if hasattr(d, "r") else tuple(d)
            if all(0 <= x <= 1 for x in c):
                return c
    except Exception:
        pass
    return CLASS_COLORS.get(cls, DEFAULT_COLOR)


def _build_meshes(m: Model) -> list:
    if m.meshes is not None:
        return m.meshes
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    meshes = []
    it = ifcopenshell.geom.iterator(settings, m.file, 1)
    if it.initialize():
        while True:
            shape = it.get()
            try:
                e = m.file.by_id(shape.id)
                g = shape.geometry
                verts = [round(float(v), 4) for v in g.verts]
                meshes.append({
                    "id": e.GlobalId, "cls": e.is_a(), "name": e.Name, "storey": _storey_name(e),
                    "color": [round(c, 3) for c in _style_color(shape, e.is_a())],
                    "v": verts, "f": list(g.faces),
                })
            except Exception:
                pass
            if not it.next():
                break
    m.meshes = meshes
    return meshes


# --------------------------------------------------------------------------- просмотрщик

class _Handler(BaseHTTPRequestHandler):
    server_version = "3d-editor"

    def log_message(self, *args):  # тишина: stdout принадлежит MCP
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        q = parse_qs(urlparse(self.path).query)
        return secrets.compare_digest(q.get("t", [""])[0], _TOKEN)

    def do_GET(self):
        url = urlparse(self.path)
        if not self._authorized():
            return self._send(403, b'{"error":"forbidden"}')
        try:
            if url.path == "/":
                return self._send(200, (VIEWER_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/model":
                m = _model()
                return self._send(200, json.dumps({"file": m.path.name, "elements": _build_meshes(m)},
                                                  ensure_ascii=False).encode("utf-8"))
            if url.path == "/api/state":
                with _LOCK:
                    body = {"version": VIEWER.version, "highlight": VIEWER.highlight,
                            "label": VIEWER.highlight_label}
                return self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"))
            if url.path.startswith("/api/element/"):
                gid = url.path.rsplit("/", 1)[1]
                e = _find(gid)
                try:
                    psets = uel.get_psets(e)
                except Exception:
                    psets = {}
                body = {"id": gid, "class": e.is_a(), "name": e.Name, "storey": _storey_name(e),
                        "description": getattr(e, "Description", None), "psets": psets}
                return self._send(200, json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"))
        except Exception as exc:  # ошибка модели не должна ронять сервер
            return self._send(500, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        url = urlparse(self.path)
        if not self._authorized():
            return self._send(403, b'{"error":"forbidden"}')
        try:
            size = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(size) or b"{}")
        except Exception:
            return self._send(400, b'{"error":"bad json"}')
        import time
        with _LOCK:
            if url.path == "/api/selection":
                VIEWER.selection = [str(x) for x in data.get("ids", [])][:2000]
                VIEWER.selection_time = time.time()
            elif url.path == "/api/message":
                text = str(data.get("text", "")).strip()
                if text:
                    VIEWER.messages.append({"text": text[:4000], "selection": list(VIEWER.selection),
                                            "time": time.time()})
            else:
                return self._send(404, b'{"error":"not found"}')
        self._send(200, b'{"ok":true}')


def _ensure_server() -> str:
    global _SERVER, _TOKEN
    with _LOCK:
        if _SERVER is None:
            _TOKEN = secrets.token_urlsafe(16)
            _SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{_SERVER.server_address[1]}/?t={_TOKEN}"


@mcp.tool()
def ifc_viewer_open() -> str:
    """Запустить локальный просмотрщик текущей модели и вернуть адрес. Откройте адрес во встроенном
    браузере Claude Desktop (инструмент preview_start с параметром url). В просмотрщике можно крутить
    модель, выделять элементы щелчком (Shift добавляет в выделение), видеть их свойства и писать
    замечания агенту. Сервер слушает только 127.0.0.1 и требует токен в адресе. Просмотрщик ничего
    не меняет в IFC."""
    m = _model()
    meshes = _build_meshes(m)
    url = _ensure_server()
    return _out({"url": url, "elements_with_geometry": len(meshes),
                 "note": "Откройте url во встроенном браузере. Выделение и замечания читайте через "
                         "viewer_get_selection и viewer_get_messages."})


@mcp.tool()
def viewer_get_selection() -> str:
    """Что выделено в просмотрщике сейчас: GlobalId, класс, имя, этаж."""
    m = _model()
    with _LOCK:
        ids = list(VIEWER.selection)
    out = []
    for gid in ids:
        try:
            out.append(_brief(m.file.by_guid(gid)))
        except Exception:
            out.append({"global_id": gid, "class": None, "name": None, "storey": None})
    return _out({"selected": out, "count": len(out)})


@mcp.tool()
def viewer_get_messages(clear: bool = False) -> str:
    """Замечания, которые пользователь написал агенту в просмотрщике, вместе с выделением на момент
    отправки. clear=true удаляет их после чтения."""
    with _LOCK:
        msgs = list(VIEWER.messages)
        if clear:
            VIEWER.messages.clear()
    return _out({"messages": msgs, "count": len(msgs)})


@mcp.tool()
def viewer_highlight(global_ids: list[str], label: str = "") -> str:
    """Подсветить элементы в просмотрщике (например, найденные по запросу). Пустой список снимает
    подсветку. label это подпись, которая покажется в панели."""
    m = _model()
    for gid in global_ids:
        m.file.by_guid(gid)  # проверка существования
    with _LOCK:
        VIEWER.highlight = list(global_ids)
        VIEWER.highlight_label = label
        VIEWER.version += 1
    return f"Подсвечено элементов: {len(global_ids)}"


if __name__ == "__main__":
    mcp.run()

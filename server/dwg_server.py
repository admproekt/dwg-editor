"""MCP-сервер для чтения и редактирования чертежей DWG/DXF.

DWG — закрытый формат, поэтому:
  чтение:   DWG -> DXF (ODA File Converter) -> ezdxf
  запись:   ezdxf -> DXF -> DWG (ODA File Converter)
DXF открывается и сохраняется напрямую, без ODA File Converter.
"""
from __future__ import annotations

import glob
import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import ezdxf
from ezdxf.addons import odafc
from ezdxf.math import Matrix44, Vec3
from mcp.server.fastmcp import FastMCP

import stretch as _stretch

mcp = FastMCP("dwg-editor")

Point = list[float]  # [x, y] или [x, y, z]

POINT_ATTRIBS = {"start", "end", "center", "insert", "location", "base", "defpoint", "text_midpoint"}
SIMPLE_ATTRIBS = {
    "layer", "color", "linetype", "lineweight", "ltscale", "radius", "start_angle", "end_angle",
    "height", "rotation", "text", "width", "invisible", "true_color", "transparency",
}


# --------------------------------------------------------------------------- ODA

def _configure_oda() -> None:
    """Находит ODAFileConverter и сообщает путь к нему ezdxf."""
    path = os.environ.get("ODA_FILE_CONVERTER")
    if not path and os.name == "nt":
        for pattern in (r"C:\Program Files\ODA\ODAFileConverter*\ODAFileConverter.exe",
                        r"C:\Program Files (x86)\ODA\ODAFileConverter*\ODAFileConverter.exe"):
            found = sorted(glob.glob(pattern))
            if found:
                path = found[-1]
                break
    if not path:
        path = shutil.which("ODAFileConverter")
    if path:
        key = "win_exec_path" if os.name == "nt" else "unix_exec_path"
        ezdxf.options.set("odafc-addon", key, path)


_configure_oda()

ODA_HINT = (
    "Для DWG нужен ODA File Converter (бесплатно: https://www.opendesign.com/guestfiles/oda_file_converter). "
    "Установите его или укажите путь в переменной среды ODA_FILE_CONVERTER."
)


def _require_oda() -> None:
    if not odafc.is_installed():
        raise RuntimeError(ODA_HINT)


# ------------------------------------------------------------------------- сессии

@dataclass
class Session:
    doc: Any
    source: Path
    dirty: bool = False


SESSIONS: dict[str, Session] = {}
CURRENT: Optional[str] = None


def _key(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _session(drawing: Optional[str]) -> Session:
    key = _key(drawing) if drawing else CURRENT
    if not key or key not in SESSIONS:
        raise RuntimeError("Чертёж не открыт. Сначала вызовите open_drawing(path).")
    return SESSIONS[key]


def _pt(p: Point) -> Vec3:
    if not 2 <= len(p) <= 3:
        raise ValueError(f"Точка должна быть [x, y] или [x, y, z], получено: {p}")
    return Vec3(*p)


def _entity(s: Session, handle: str):
    e = s.doc.entitydb.get(handle.upper())
    if e is None or not e.is_alive:
        raise KeyError(f"Объект с handle={handle} не найден")
    return e


def _attribs(layer: Optional[str], color: Optional[int]) -> dict:
    d: dict[str, Any] = {}
    if layer:
        d["layer"] = layer
    if color is not None:
        d["color"] = color
    return d


def _ensure_layer(s: Session, layer: Optional[str]) -> None:
    if layer and layer not in s.doc.layers:
        s.doc.layers.add(layer)


def _describe(e) -> dict:
    d: dict[str, Any] = {"handle": e.dxf.handle, "type": e.dxftype(), "layer": e.dxf.get("layer", "0")}
    t = e.dxftype()
    if t == "LINE":
        d.update(start=list(e.dxf.start), end=list(e.dxf.end))
    elif t == "CIRCLE":
        d.update(center=list(e.dxf.center), radius=e.dxf.radius)
    elif t == "ARC":
        d.update(center=list(e.dxf.center), radius=e.dxf.radius,
                 start_angle=e.dxf.start_angle, end_angle=e.dxf.end_angle)
    elif t == "LWPOLYLINE":
        d.update(points=[list(p[:2]) for p in e.get_points()], closed=e.closed)
    elif t == "TEXT":
        d.update(text=e.dxf.text, insert=list(e.dxf.insert), height=e.dxf.height)
    elif t == "MTEXT":
        d.update(text=e.text, insert=list(e.dxf.insert), char_height=e.dxf.char_height)
    elif t == "INSERT":
        d.update(block=e.dxf.name, insert=list(e.dxf.insert),
                 attribs={a.dxf.tag: a.dxf.text for a in e.attribs})
    elif t == "DIMENSION":
        d.update(measurement=e.dxf.get("actual_measurement"), text=e.dxf.get("text", ""))
    return d


def _out(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _check_changes(changes: dict[str, Any]) -> None:
    for k in changes:
        if k not in POINT_ATTRIBS and k not in SIMPLE_ATTRIBS:
            raise ValueError(f"Атрибут {k!r} не разрешён. См. описание инструмента.")


def _apply_changes(s: Session, e, changes: dict[str, Any]) -> None:
    for k, v in changes.items():
        if k in POINT_ATTRIBS:
            e.dxf.set(k, _pt(v))
        else:
            if k == "layer":
                _ensure_layer(s, v)
            if k == "text" and e.dxftype() == "MTEXT":
                e.text = v
            else:
                e.dxf.set(k, v)


# ----------------------------------------------------------------- файлы и сведения

@mcp.tool()
def open_drawing(path: str) -> str:
    """Открыть чертёж .dwg или .dxf. DWG конвертируется через ODA File Converter.
    Возвращает краткую сводку. Открытый чертёж становится текущим."""
    global CURRENT
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"Файл не найден: {p}")
    ext = p.suffix.lower()
    if ext == ".dwg":
        _require_oda()
        doc = odafc.readfile(str(p))
    elif ext == ".dxf":
        doc = ezdxf.readfile(str(p))
    else:
        raise ValueError("Поддерживаются только .dwg и .dxf")
    key = _key(p)
    SESSIONS[key] = Session(doc=doc, source=Path(key))
    CURRENT = key
    return drawing_info(key)


@mcp.tool()
def new_drawing(path: str, units: str = "mm") -> str:
    """Создать пустой чертёж (R2018). Файл на диск пишется только при save_drawing.
    units: mm, cm, m, in, ft."""
    global CURRENT
    from ezdxf import units as u
    table = {"mm": u.MM, "cm": u.CM, "m": u.M, "in": u.IN, "ft": u.FT}
    if units not in table:
        raise ValueError(f"units должен быть одним из {list(table)}")
    doc = ezdxf.new("R2018", setup=True)
    doc.units = table[units]
    key = _key(path)
    SESSIONS[key] = Session(doc=doc, source=Path(key), dirty=True)
    CURRENT = key
    return drawing_info(key)


@mcp.tool()
def drawing_info(drawing: Optional[str] = None) -> str:
    """Сводка по чертежу: версия, единицы, границы, слои, количество объектов по типам."""
    s = _session(drawing)
    msp = s.doc.modelspace()
    counts: dict[str, int] = {}
    for e in msp:
        counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
    try:
        from ezdxf import bbox
        box = bbox.extents(msp)
        extents = {"min": list(box.extmin), "max": list(box.extmax)} if box.has_data else None
    except Exception:
        extents = None
    return _out({
        "file": str(s.source),
        "dxf_version": s.doc.dxfversion,
        "units": ezdxf.units.decode(s.doc.units),
        "unsaved_changes": s.dirty,
        "layers": [l.dxf.name for l in s.doc.layers],
        "layouts": s.doc.layout_names(),
        "blocks": [b.name for b in s.doc.blocks if not b.name.startswith("*")],
        "modelspace_entities": counts,
        "extents": extents,
    })


@mcp.tool()
def list_layers(drawing: Optional[str] = None) -> str:
    """Список слоёв с цветом, типом линии и состоянием (выкл./заморожен/заблокирован)."""
    s = _session(drawing)
    return _out([{
        "name": l.dxf.name, "color": l.color, "linetype": l.dxf.linetype,
        "on": l.is_on(), "frozen": l.is_frozen(), "locked": l.is_locked(),
    } for l in s.doc.layers])


@mcp.tool()
def list_entities(layer: Optional[str] = None, entity_type: Optional[str] = None,
                  limit: int = 100, offset: int = 0, drawing: Optional[str] = None) -> str:
    """Объекты модели с handle и геометрией. Фильтры: layer, entity_type (LINE, CIRCLE, TEXT, INSERT...).
    Постранично через limit/offset."""
    s = _session(drawing)
    items = [e for e in s.doc.modelspace()
             if (not layer or e.dxf.get("layer") == layer)
             and (not entity_type or e.dxftype() == entity_type.upper())]
    return _out({"total": len(items), "offset": offset,
                 "entities": [_describe(e) for e in items[offset:offset + limit]]})


@mcp.tool()
def get_entity(handle: str, drawing: Optional[str] = None) -> str:
    """Все DXF-атрибуты одного объекта по handle."""
    e = _entity(_session(drawing), handle)
    attrs = {k: (list(v) if hasattr(v, "__iter__") and not isinstance(v, str) else v)
             for k, v in e.dxfattribs().items()}
    return _out({"described": _describe(e), "dxf_attribs": attrs})


@mcp.tool()
def render_preview(output_png: Optional[str] = None, dpi: int = 150,
                   drawing: Optional[str] = None) -> str:
    """Отрисовать модель в PNG и вернуть путь. Откройте PNG инструментом Read, чтобы увидеть чертёж."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    s = _session(drawing)
    out = Path(output_png) if output_png else Path(tempfile.gettempdir()) / f"{s.source.stem}_preview.png"
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    Frontend(RenderContext(s.doc), MatplotlibBackend(ax)).draw_layout(s.doc.modelspace(), finalize=True)
    fig.savefig(out, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(out)


# ----------------------------------------------------------------------------- движки

def _autocad_info() -> dict[str, Any]:
    """Что есть на этом ПК из AutoCAD. Только обнаружение, ничего не запускается."""
    info: dict[str, Any] = {"core_console": None, "running": False, "com_package": False}
    if os.name != "nt":
        return info
    found = sorted(glob.glob(r"C:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe"))
    info["core_console"] = found[-1] if found else None
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq acad.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10).stdout
        info["running"] = "acad.exe" in out.lower()
    except Exception:
        pass
    import importlib.util
    info["com_package"] = importlib.util.find_spec("win32com") is not None
    return info


@mcp.tool()
def engine_status() -> str:
    """Какие движки правки чертежей доступны на этом ПК. Сейчас реализован только ezdxf;
    живой AutoCAD, AutoCAD Core Console и Autodesk Platform Services только обнаруживаются
    и пока не подключены (см. docs/PLAN-engines-and-viewer.md)."""
    ac = _autocad_info()
    return _out({
        "engines": {
            "ezdxf": {"implemented": True, "available": True, "version": ezdxf.version,
                      "oda_file_converter": odafc.is_installed()},
            "autocad_live": {"implemented": False, "autocad_running": ac["running"],
                             "com_python_package": ac["com_package"]},
            "autocad_core_console": {"implemented": False, "path": ac["core_console"]},
            "autodesk_platform_services": {
                "implemented": False,
                "credentials_set": bool(os.environ.get("APS_CLIENT_ID") and os.environ.get("APS_CLIENT_SECRET")),
            },
        },
        "recommendation": "Используйте ezdxf. Остальные движки пока не подключены: объекты, "
                          "которые требуют AutoCAD (динамические блоки, прокси-объекты), "
                          "правьте осторожно и предупреждайте пользователя об ограничениях.",
    })


# ------------------------------------------------------------------------ создание

@mcp.tool()
def add_layer(name: str, color: int = 7, linetype: str = "Continuous",
              drawing: Optional[str] = None) -> str:
    """Создать слой (color — индекс AutoCAD ACI 1..255)."""
    s = _session(drawing)
    if name in s.doc.layers:
        raise ValueError(f"Слой {name} уже существует")
    s.doc.layers.add(name, color=color, linetype=linetype)
    s.dirty = True
    return f"Слой {name} создан"


@mcp.tool()
def add_line(start: Point, end: Point, layer: Optional[str] = None, color: Optional[int] = None,
             drawing: Optional[str] = None) -> str:
    """Добавить отрезок."""
    s = _session(drawing)
    _ensure_layer(s, layer)
    e = s.doc.modelspace().add_line(_pt(start), _pt(end), dxfattribs=_attribs(layer, color))
    s.dirty = True
    return _out(_describe(e))


@mcp.tool()
def add_circle(center: Point, radius: float, layer: Optional[str] = None, color: Optional[int] = None,
               drawing: Optional[str] = None) -> str:
    """Добавить окружность."""
    s = _session(drawing)
    _ensure_layer(s, layer)
    e = s.doc.modelspace().add_circle(_pt(center), radius, dxfattribs=_attribs(layer, color))
    s.dirty = True
    return _out(_describe(e))


@mcp.tool()
def add_arc(center: Point, radius: float, start_angle: float, end_angle: float,
            layer: Optional[str] = None, color: Optional[int] = None,
            drawing: Optional[str] = None) -> str:
    """Добавить дугу. Углы в градусах, против часовой стрелки."""
    s = _session(drawing)
    _ensure_layer(s, layer)
    e = s.doc.modelspace().add_arc(_pt(center), radius, start_angle, end_angle,
                                   dxfattribs=_attribs(layer, color))
    s.dirty = True
    return _out(_describe(e))


@mcp.tool()
def add_polyline(points: list[Point], closed: bool = False, layer: Optional[str] = None,
                 color: Optional[int] = None, drawing: Optional[str] = None) -> str:
    """Добавить 2D-полилинию по списку точек [[x, y], ...]."""
    s = _session(drawing)
    _ensure_layer(s, layer)
    e = s.doc.modelspace().add_lwpolyline([tuple(p[:2]) for p in points], close=closed,
                                          dxfattribs=_attribs(layer, color))
    s.dirty = True
    return _out(_describe(e))


@mcp.tool()
def add_text(text: str, insert: Point, height: float = 2.5, rotation: float = 0.0,
             layer: Optional[str] = None, color: Optional[int] = None,
             drawing: Optional[str] = None) -> str:
    """Добавить однострочный текст."""
    s = _session(drawing)
    _ensure_layer(s, layer)
    attribs = {"height": height, "rotation": rotation, **_attribs(layer, color)}
    e = s.doc.modelspace().add_text(text, dxfattribs=attribs)
    e.set_placement(tuple(_pt(insert)))
    s.dirty = True
    return _out(_describe(e))


@mcp.tool()
def add_mtext(text: str, insert: Point, char_height: float = 2.5, width: float = 0.0,
              layer: Optional[str] = None, color: Optional[int] = None,
              drawing: Optional[str] = None) -> str:
    """Добавить многострочный текст (перенос строки — \\P)."""
    s = _session(drawing)
    _ensure_layer(s, layer)
    attribs = {"char_height": char_height, **_attribs(layer, color)}
    if width:
        attribs["width"] = width
    e = s.doc.modelspace().add_mtext(text, dxfattribs=attribs)
    e.dxf.insert = _pt(insert)
    s.dirty = True
    return _out(_describe(e))


@mcp.tool()
def add_linear_dimension(p1: Point, p2: Point, offset: float = 5.0, layer: Optional[str] = None,
                         drawing: Optional[str] = None) -> str:
    """Добавить линейный размер между p1 и p2, выровненный по отрезку p1-p2.
    offset — расстояние размерной линии от отрезка (знак задаёт сторону)."""
    s = _session(drawing)
    _ensure_layer(s, layer)
    a, b = _pt(p1), _pt(p2)
    mid = (a + b) / 2
    angle = (b - a).angle_deg
    n = (b - a).normalize().rotate_deg(90) * offset
    dim = s.doc.modelspace().add_linear_dim(
        base=tuple(mid + n)[:2], p1=tuple(a)[:2], p2=tuple(b)[:2], angle=angle,
        dxfattribs=_attribs(layer, None))
    dim.render()
    s.dirty = True
    return _out(_describe(dim.dimension))


# --------------------------------------------------------------------- редактирование

@mcp.tool()
def modify_entity(handle: str, changes: dict[str, Any], drawing: Optional[str] = None) -> str:
    """Изменить атрибуты объекта. changes, например:
    {"layer": "Стены", "color": 1}, {"radius": 25}, {"start": [0,0], "end": [10,5]},
    {"text": "Новый текст", "height": 3.5}. Для MTEXT текст задаётся ключом "text".
    Разрешены: layer, color, linetype, lineweight, ltscale, radius, start_angle, end_angle, height,
    rotation, text, width, invisible, true_color, transparency и точки start/end/center/insert."""
    s = _session(drawing)
    e = _entity(s, handle)
    _check_changes(changes)
    _apply_changes(s, e, changes)
    s.dirty = True
    return _out(_describe(e))


@mcp.tool()
def move_entities(handles: list[str], dx: float, dy: float, dz: float = 0.0,
                  drawing: Optional[str] = None) -> str:
    """Сдвинуть объекты на (dx, dy, dz)."""
    s = _session(drawing)
    for h in handles:
        _entity(s, h).translate(dx, dy, dz)
    s.dirty = True
    return f"Сдвинуто объектов: {len(handles)}"


@mcp.tool()
def delete_entities(handles: list[str], drawing: Optional[str] = None) -> str:
    """Удалить объекты по handle."""
    s = _session(drawing)
    msp = s.doc.modelspace()
    for h in handles:
        msp.delete_entity(_entity(s, h))
    s.dirty = True
    return f"Удалено объектов: {len(handles)}"


@mcp.tool()
def replace_text(find: str, replace: str, layer: Optional[str] = None,
                 drawing: Optional[str] = None) -> str:
    """Заменить подстроку во всех TEXT, MTEXT и атрибутах блоков (штамп, спецификации)."""
    s = _session(drawing)
    changed = 0

    def scan(entities):
        nonlocal changed
        for e in entities:
            t = e.dxftype()
            if layer and e.dxf.get("layer") != layer:
                continue
            if t in ("TEXT", "ATTRIB") and find in e.dxf.text:
                e.dxf.text = e.dxf.text.replace(find, replace)
                changed += 1
            elif t == "MTEXT" and find in e.text:
                e.text = e.text.replace(find, replace)
                changed += 1
            elif t == "INSERT":
                scan(e.attribs)

    scan(s.doc.modelspace())
    if changed:
        s.dirty = True
    return f"Заменено в {changed} объектах"


# ------------------------------------------------------------------- слои (свойства)

PROTECTED_LAYERS = {"0", "defpoints"}


def _layer(s: Session, name: str):
    if name not in s.doc.layers:
        raise KeyError(f"Слой {name} не найден")
    return s.doc.layers.get(name)


def _layer_users(s: Session, name: str) -> list:
    return [e for e in s.doc.entitydb.values()
            if e.is_alive and e.dxf.hasattr("layer") and e.dxf.layer == name]


@mcp.tool()
def set_layer_properties(name: str, color: Optional[int] = None, linetype: Optional[str] = None,
                         lineweight: Optional[int] = None, on: Optional[bool] = None,
                         frozen: Optional[bool] = None, locked: Optional[bool] = None,
                         new_name: Optional[str] = None, drawing: Optional[str] = None) -> str:
    """Изменить свойства существующего слоя: color (ACI 1..255), linetype (должен быть в чертеже),
    lineweight (сотые доли мм, например 25 = 0,25 мм), on/frozen/locked, new_name (переименование;
    все объекты слоя переносятся на новое имя). Слои 0 и Defpoints переименовать нельзя.
    Передавайте только то, что нужно изменить."""
    s = _session(drawing)
    layer = _layer(s, name)
    if linetype is not None and linetype not in s.doc.linetypes:
        raise ValueError(f"Тип линии {linetype} не определён в чертеже")
    if new_name is not None and new_name != name:
        if name.lower() in PROTECTED_LAYERS:
            raise ValueError(f"Слой {name} переименовать нельзя")
        if new_name in s.doc.layers:
            raise ValueError(f"Слой {new_name} уже существует")
    if color is not None:
        layer.color = color
    if linetype is not None:
        layer.dxf.linetype = linetype
    if lineweight is not None:
        layer.dxf.lineweight = lineweight
    if on is not None:
        layer.on() if on else layer.off()
    if frozen is not None:
        layer.freeze() if frozen else layer.thaw()
    if locked is not None:
        layer.lock() if locked else layer.unlock()
    if new_name is not None and new_name != name:
        users = _layer_users(s, name)
        layer.rename(new_name)
        for e in users:
            e.dxf.layer = new_name
    s.dirty = True
    final = new_name or name
    l = s.doc.layers.get(final)
    return _out({"name": final, "color": l.color, "linetype": l.dxf.linetype,
                 "on": l.is_on(), "frozen": l.is_frozen(), "locked": l.is_locked()})


@mcp.tool()
def delete_layer(name: str, drawing: Optional[str] = None) -> str:
    """Удалить пустой слой. Слои 0 и Defpoints и текущий слой удалить нельзя; если на слое есть
    объекты, инструмент откажет (сначала перенесите или удалите объекты)."""
    s = _session(drawing)
    _layer(s, name)
    if name.lower() in PROTECTED_LAYERS:
        raise ValueError(f"Слой {name} удалить нельзя")
    if name == s.doc.header.get("$CLAYER"):
        raise ValueError(f"Слой {name} текущий, удалить нельзя")
    users = _layer_users(s, name)
    if users:
        raise RuntimeError(f"На слое {name} {len(users)} объектов: перенесите их или удалите, потом повторите")
    s.doc.layers.remove(name)
    s.dirty = True
    return f"Слой {name} удалён"


# ------------------------------------------------------------ копирование и преобразования

def _entities(s: Session, handles: list[str]) -> list:
    if not handles:
        raise ValueError("Список handle пуст")
    return [_entity(s, h) for h in handles]


def _transform_all(s: Session, handles: list[str], m: Matrix44) -> str:
    ents = _entities(s, handles)  # сначала проверяем все handle
    for e in ents:
        try:
            e.transform(m)
        except Exception as exc:
            raise RuntimeError(f"Объект {e.dxf.handle} ({e.dxftype()}) не удалось преобразовать: {exc}. "
                               "Часть объектов могла быть уже преобразована.") from exc
    s.dirty = True
    return f"Преобразовано объектов: {len(ents)}"


@mcp.tool()
def copy_entities(handles: list[str], dx: float, dy: float, dz: float = 0.0,
                  drawing: Optional[str] = None) -> str:
    """Скопировать объекты со сдвигом (dx, dy, dz). Возвращает handle новых объектов
    в том же порядке, что и исходные."""
    s = _session(drawing)
    ents = _entities(s, handles)
    msp = s.doc.modelspace()
    new = []
    for e in ents:
        c = e.copy()
        (e.get_layout() or msp).add_entity(c)
        c.translate(dx, dy, dz)
        new.append(c)
    s.dirty = True
    return _out({"copied": len(new), "new_handles": [c.dxf.handle for c in new]})


@mcp.tool()
def rotate_entities(handles: list[str], angle: float, center: Point = (0.0, 0.0),
                    drawing: Optional[str] = None) -> str:
    """Повернуть объекты вокруг точки center на angle градусов против часовой стрелки."""
    s = _session(drawing)
    c = _pt(list(center))
    m = Matrix44.chain(Matrix44.translate(-c.x, -c.y, -c.z), Matrix44.z_rotate(math.radians(angle)),
                       Matrix44.translate(c.x, c.y, c.z))
    return _transform_all(s, handles, m)


@mcp.tool()
def scale_entities(handles: list[str], factor: float, center: Point = (0.0, 0.0),
                   drawing: Optional[str] = None) -> str:
    """Равномерно масштабировать объекты относительно точки center (factor > 0)."""
    if factor <= 0:
        raise ValueError("factor должен быть больше нуля")
    s = _session(drawing)
    c = _pt(list(center))
    m = Matrix44.chain(Matrix44.translate(-c.x, -c.y, -c.z), Matrix44.scale(factor, factor, factor),
                       Matrix44.translate(c.x, c.y, c.z))
    return _transform_all(s, handles, m)


@mcp.tool()
def mirror_entities(handles: list[str], p1: Point, p2: Point, keep_original: bool = False,
                    drawing: Optional[str] = None) -> str:
    """Отразить объекты относительно линии p1-p2. keep_original=true оставляет исходные объекты
    и отражает их копии (возвращает handle копий). Тексты при отражении могут оказаться перевёрнутыми
    (угол поворота зеркалится, как в ezdxf): проверяйте превью и при необходимости поправьте
    rotation через modify_entity."""
    s = _session(drawing)
    a, b = _pt(p1), _pt(p2)
    if a.isclose(b):
        raise ValueError("Точки p1 и p2 совпадают, линия отражения не задана")
    ang = (b - a).angle
    m = Matrix44.chain(Matrix44.translate(-a.x, -a.y, -a.z), Matrix44.z_rotate(-ang),
                       Matrix44.scale(1, -1, 1), Matrix44.z_rotate(ang),
                       Matrix44.translate(a.x, a.y, a.z))
    ents = _entities(s, handles)
    if not keep_original:
        return _transform_all(s, handles, m)
    msp = s.doc.modelspace()
    copies = []
    for e in ents:
        c = e.copy()
        (e.get_layout() or msp).add_entity(c)
        copies.append(c)
    _transform_all(s, [c.dxf.handle for c in copies], m)
    return _out({"copied": len(copies), "new_handles": [c.dxf.handle for c in copies]})


# ---------------------------------------------------------------- растяжение и вырезание

def _window(w: list[float], name: str) -> tuple[float, float, float, float]:
    if len(w) != 4 or w[0] >= w[2] or w[1] >= w[3]:
        raise ValueError(f"{name} должно быть [x0, y0, x1, y1], где x0 < x1 и y0 < y1")
    return (float(w[0]), float(w[1]), float(w[2]), float(w[3]))


@mcp.tool()
def stretch_entities(window: list[float], dx: float, dy: float, dz: float = 0.0,
                     exclude_handles: Optional[list[str]] = None, drawing: Optional[str] = None) -> str:
    """Растянуть чертёж как STRETCH в AutoCAD: точки внутри окна window = [x0, y0, x1, y1] сдвигаются
    на (dx, dy, dz). Объект целиком в окне переносится, пересекающий границу растягивается (сдвигаются
    только его вершины в окне), остальные не меняются. Размеры пересчитываются, число на размере
    обновляется. exclude_handles: объекты, которые трогать нельзя. Поддержаны LINE, LWPOLYLINE, POLYLINE,
    ARC, CIRCLE, POINT, TEXT, MTEXT, INSERT, HATCH, DIMENSION, MULTILEADER; остальное пропускается и
    перечисляется в skipped_types. Дуга, пересекающая границу окна, вызывает ошибку."""
    s = _session(drawing)
    res = _stretch.stretch(s.doc, _window(window, "window"), dx, dy, dz, exclude=exclude_handles or [])
    s.dirty = True
    return _out(res)


@mcp.tool()
def close_gap(region: list[float], axis: str, lo: float, hi: float,
              exclude_handles: Optional[list[str]] = None, delete_inside: bool = True,
              drawing: Optional[str] = None) -> str:
    """Вырезать из области region = [x0, y0, x1, y1] полосу lo..hi по оси axis ('x' или 'y') и сомкнуть
    края: обе части сдвигаются к полосе на (hi-lo)/2, пересекающие полосу объекты сокращаются на hi-lo,
    объекты целиком внутри полосы удаляются (delete_inside). Так сужают плиту, ничего не растягивая в
    середине: сначала проверьте, что в полосе нет нужного (список deleted после вызова), и исключите
    такие объекты в exclude_handles. Объекты, частично попавшие в полосу, не меняются и возвращаются в
    ambiguous: правьте их вручную. Выноска, остриё которой вне полосы, переносится целиком."""
    s = _session(drawing)
    res = _stretch.close_gap(s.doc, _window(region, "region"), axis, lo, hi,
                             exclude=exclude_handles or [], delete_inside=delete_inside)
    s.dirty = True
    return _out(res)


# ------------------------------------------------------------------- пакетные операции

@mcp.tool()
def modify_entities(items: list[dict[str, Any]], drawing: Optional[str] = None) -> str:
    """Изменить много объектов за один вызов. items: [{"handle": "2D3", "changes": {...}}, ...],
    changes те же, что у modify_entity. Все handle и ключи проверяются заранее: при ошибке
    чертёж не меняется."""
    s = _session(drawing)
    prepared = []
    for it in items:
        h, ch = it.get("handle"), it.get("changes")
        if not h or not isinstance(ch, dict):
            raise ValueError(f"Элемент должен содержать handle и changes: {it}")
        _check_changes(ch)
        prepared.append((_entity(s, h), ch))
    for e, ch in prepared:
        _apply_changes(s, e, ch)
    s.dirty = True
    return f"Изменено объектов: {len(prepared)}"


@mcp.tool()
def move_entities_each(moves: list[dict[str, Any]], drawing: Optional[str] = None) -> str:
    """Сдвинуть много объектов, каждый на своё смещение. moves:
    [{"handle": "2D3", "dx": 1.5, "dy": 0, "dz": 0}, ...] (dz необязателен). Все handle и числа
    проверяются заранее: при ошибке чертёж не меняется."""
    s = _session(drawing)
    prepared = []
    for mv in moves:
        try:
            h = mv["handle"]
            vec = (float(mv["dx"]), float(mv["dy"]), float(mv.get("dz", 0.0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Элемент должен содержать handle, dx, dy (числа): {mv}") from exc
        prepared.append((_entity(s, h), vec))
    for e, vec in prepared:
        e.translate(*vec)
    s.dirty = True
    return f"Сдвинуто объектов: {len(prepared)}"


# --------------------------------------------------------------------------- сохранение

@mcp.tool()
def save_drawing(output_path: Optional[str] = None, dwg_version: str = "R2018",
                 drawing: Optional[str] = None) -> str:
    """Сохранить чертёж. Без output_path перезаписывает исходный файл (для DWG рядом создаётся .bak).
    Формат определяется расширением .dwg/.dxf. dwg_version: R2000, R2004, R2007, R2010, R2013, R2018."""
    s = _session(drawing)
    out = Path(output_path).expanduser() if output_path else s.source
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.resolve() == s.source and not out.with_suffix(out.suffix + ".bak").exists():
        shutil.copy2(out, out.with_suffix(out.suffix + ".bak"))
    ext = out.suffix.lower()
    if ext == ".dxf":
        s.doc.saveas(str(out))
    elif ext == ".dwg":
        _require_oda()
        odafc.export_dwg(s.doc, str(out), version=dwg_version, replace=True)
    else:
        raise ValueError("Расширение должно быть .dwg или .dxf")
    s.dirty = False
    return f"Сохранено: {out}"


@mcp.tool()
def close_drawing(drawing: Optional[str] = None, discard_changes: bool = False) -> str:
    """Закрыть чертёж. Если есть несохранённые правки, нужен discard_changes=true."""
    global CURRENT
    s = _session(drawing)
    if s.dirty and not discard_changes:
        raise RuntimeError("Есть несохранённые изменения: вызовите save_drawing или передайте discard_changes=true")
    key = str(s.source)
    SESSIONS.pop(key, None)
    if CURRENT == key:
        CURRENT = next(iter(SESSIONS), None)
    return f"Закрыт: {key}"


if __name__ == "__main__":
    mcp.run()

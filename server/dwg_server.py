"""MCP-сервер для чтения и редактирования чертежей DWG/DXF.

DWG — закрытый формат, поэтому:
  чтение:   DWG -> DXF (ODA File Converter) -> ezdxf
  запись:   ezdxf -> DXF -> DWG (ODA File Converter)
DXF открывается и сохраняется напрямую, без ODA File Converter.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import ezdxf
from ezdxf.addons import odafc
from ezdxf.math import Vec3
from mcp.server.fastmcp import FastMCP

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
    for k, v in changes.items():
        if k in POINT_ATTRIBS:
            e.dxf.set(k, _pt(v))
        elif k in SIMPLE_ATTRIBS:
            if k == "layer":
                _ensure_layer(s, v)
            if k == "text" and e.dxftype() == "MTEXT":
                e.text = v
            else:
                e.dxf.set(k, v)
        else:
            raise ValueError(f"Атрибут {k!r} не разрешён. См. описание инструмента.")
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

"""Растяжение объектов в стиле AutoCAD STRETCH для документов ezdxf.

Правило то же, что в AutoCAD: точки внутри окна сдвигаются на (dx, dy, dz), точки снаружи остаются.
Объект целиком внутри окна просто переносится, объект, пересекающий границу окна, растягивается:
сдвигаются только его вершины внутри окна.

Поддержаны LINE, LWPOLYLINE, POLYLINE, ARC, CIRCLE, POINT, TEXT, MTEXT, INSERT, HATCH, DIMENSION
(с геометрическим блоком и числом на размере) и MULTILEADER. Остальные типы пропускаются и
перечисляются в результате.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from ezdxf.entities.boundary_paths import ArcEdge, EdgePath, LineEdge, PolylinePath
from ezdxf.math import Vec2, Vec3

Window = tuple[float, float, float, float]  # x0, y0, x1, y1

_SKIPPED_NOTE = "тип не поддержан, объект не менялся"


class StretchError(ValueError):
    pass


def _inside(w: Window, x: float, y: float) -> bool:
    return w[0] <= x <= w[2] and w[1] <= y <= w[3]


def _flipped(e) -> bool:
    ext = e.dxf.get("extrusion", (0, 0, 1))
    return tuple(round(c, 9) for c in ext) != (0.0, 0.0, 1.0)


def _wcs_point(e, p) -> Vec3:
    """Точка объекта в мировых координатах (учитывает зеркальную систему OCS)."""
    return Vec3(e.ocs().to_wcs(p)) if _flipped(e) else Vec3(p)


class _Mover:
    def __init__(self, window: Window, dx: float, dy: float, dz: float):
        self.w = window
        self.d = Vec3(dx, dy, dz)

    def inside(self, p) -> bool:
        return _inside(self.w, p[0], p[1])

    def point(self, p) -> Vec3:
        v = Vec3(p)
        return v + self.d if self.inside(v) else v

    def moves(self, p) -> bool:
        return self.inside(p)


# ----------------------------------------------------------------------------- типы

def _line(e, mv: _Mover) -> bool:
    a, b = mv.moves(e.dxf.start), mv.moves(e.dxf.end)
    if a:
        e.dxf.start = mv.point(e.dxf.start)
    if b:
        e.dxf.end = mv.point(e.dxf.end)
    return a or b


def _lwpolyline(e, mv: _Mover) -> bool:
    if _flipped(e):
        raise StretchError(f"Полилиния {e.dxf.handle} в зеркальной системе координат, не поддержано")
    pts = list(e.get_points("xyseb"))
    changed = False
    out = []
    for x, y, sw, ew, bulge in pts:
        if mv.inside((x, y)):
            x, y = x + mv.d.x, y + mv.d.y
            changed = True
        out.append((x, y, sw, ew, bulge))
    if changed:
        e.set_points(out, format="xyseb")
    return changed


def _polyline(e, mv: _Mover) -> bool:
    changed = False
    for v in e.vertices:
        if mv.inside(v.dxf.location):
            v.dxf.location = mv.point(v.dxf.location)
            changed = True
    return changed


def _arc(e, mv: _Mover) -> bool:
    flags = {mv.moves(_wcs_point(e, e.dxf.center)), mv.moves(e.start_point), mv.moves(e.end_point)}
    if flags == {True}:
        e.translate(*mv.d)
        return True
    if flags == {False}:
        return False
    raise StretchError(f"Дуга {e.dxf.handle} пересекает границу окна, растянуть её нельзя")


def _anchor(e, mv: _Mover) -> bool:
    if mv.moves(_wcs_point(e, e.dxf.insert)):
        e.translate(*mv.d)
        return True
    return False


def _point(e, mv: _Mover) -> bool:
    if mv.moves(e.dxf.location):
        e.translate(*mv.d)
        return True
    return False


def _circle(e, mv: _Mover) -> bool:
    if mv.moves(_wcs_point(e, e.dxf.center)):
        e.translate(*mv.d)
        return True
    return False


def _hatch(e, mv: _Mover) -> bool:
    changed = False
    for path in e.paths:
        if isinstance(path, PolylinePath):
            new = []
            for v in path.vertices:
                x, y = v[0], v[1]
                if mv.inside((x, y)):
                    x, y = x + mv.d.x, y + mv.d.y
                    changed = True
                new.append((x, y) + tuple(v[2:]))
            path.vertices = new
        elif isinstance(path, EdgePath):
            for edge in path.edges:
                if isinstance(edge, LineEdge):
                    if mv.inside(edge.start):
                        edge.start = Vec2(edge.start) + Vec2(mv.d.x, mv.d.y)
                        changed = True
                    if mv.inside(edge.end):
                        edge.end = Vec2(edge.end) + Vec2(mv.d.x, mv.d.y)
                        changed = True
                elif isinstance(edge, ArcEdge):
                    if mv.inside(edge.center):
                        edge.center = Vec2(edge.center) + Vec2(mv.d.x, mv.d.y)
                        changed = True
                else:
                    raise StretchError(f"Штриховка {e.dxf.handle}: тип границы {type(edge).__name__} не поддержан")
    seeds = list(e.seeds)
    if seeds:
        e.seeds = [(s[0] + mv.d.x, s[1] + mv.d.y) if mv.inside(s) else (s[0], s[1]) for s in seeds]
    return changed


def _mleader(e, mv: _Mover) -> bool:
    ctx = e.context
    pts = []
    for leader in ctx.leaders:
        pts.append(leader.last_leader_point)
        for line in leader.lines:
            pts.extend(line.vertices)
    if ctx.mtext is not None:
        pts.append(ctx.mtext.insert)
    flags = {mv.moves(p) for p in pts}
    if flags == {False}:
        return False
    if flags == {True}:
        e.translate(*mv.d)
        return True
    changed = False
    for leader in ctx.leaders:
        if mv.moves(leader.last_leader_point):
            leader.last_leader_point = mv.point(leader.last_leader_point)
            changed = True
        for line in leader.lines:
            line.vertices = [mv.point(v) for v in line.vertices]
            changed = True
    if ctx.mtext is not None and mv.moves(ctx.mtext.insert):
        ctx.mtext.insert = mv.point(ctx.mtext.insert)
        changed = True
    return changed


# ----------------------------------------------------------------------------- размеры

def _dim_measurement(dim) -> Optional[float]:
    p2, p3 = dim.dxf.get("defpoint2"), dim.dxf.get("defpoint3")
    if p2 is None or p3 is None:
        return None
    dtype = dim.dimtype
    if dtype == 0:  # линейный с углом
        import math
        a = math.radians(dim.dxf.get("angle", 0.0))
        d = Vec3(p3) - Vec3(p2)
        return abs(d.x * math.cos(a) + d.y * math.sin(a))
    if dtype == 1:  # выровненный
        return (Vec3(p3) - Vec3(p2)).magnitude
    return None


_BLOCK_POINT_ATTRS = ("start", "end", "center", "location")


def _stretch_block_entity(e, mv: _Mover, shift: Vec3) -> None:
    t = e.dxftype()
    if t == "LINE":
        _line(e, mv)
    elif t == "LWPOLYLINE":
        _lwpolyline(e, mv)
    elif t == "SOLID":
        for name in ("vtx0", "vtx1", "vtx2", "vtx3"):
            if e.dxf.hasattr(name) and mv.moves(e.dxf.get(name)):
                e.dxf.set(name, mv.point(e.dxf.get(name)))
    elif t in ("TEXT", "MTEXT"):
        if shift.magnitude > 0:
            e.translate(*shift)
    elif t == "INSERT":
        _anchor(e, mv)
    elif t == "CIRCLE":
        _circle(e, mv)
    elif t == "ARC":
        _arc(e, mv)


def _replace_number(text: str, old: str, new: str) -> str:
    return re.sub(rf"(?<!\d){re.escape(old)}(?!\d)", new, text, count=1)


def _display(dim, value: float) -> str:
    """Число так, как его покажет размер: с учётом масштаба измерений (dimlfac) и знаков (dimdec)."""
    lfac, dec = 1.0, 0
    try:
        from ezdxf.entities.dimstyleoverride import DimStyleOverride
        ov = DimStyleOverride(dim)
        lfac = float(ov.get("dimlfac", 1.0))
        dec = int(ov.get("dimdec", 0))
    except Exception:
        pass
    return f"{value * lfac:.{max(dec, 0)}f}"


def _dimension(e, mv: _Mover, doc) -> bool:
    keys = [k for k in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5", "text_midpoint")
            if e.dxf.get(k) is not None]
    before = {k: Vec3(e.dxf.get(k)) for k in keys}
    old_meas = _dim_measurement(e)
    changed = False
    for k in keys:
        if mv.moves(before[k]):
            e.dxf.set(k, mv.point(before[k]))
            changed = True
    if not changed:
        return False
    # сдвиг подписи: среднее смещение определяющих точек
    shifts = [Vec3(e.dxf.get(k)) - before[k] for k in ("defpoint2", "defpoint3") if k in before]
    shift = sum(shifts, Vec3()) / len(shifts) if shifts else Vec3()
    if "text_midpoint" in before:
        e.dxf.text_midpoint = before["text_midpoint"] + shift
    new_meas = _dim_measurement(e)
    if new_meas is not None:
        e.dxf.actual_measurement = new_meas
    name = e.dxf.get("geometry")
    if name and name in doc.blocks:
        for be in doc.blocks.get(name):
            if be.dxftype() in ("MTEXT", "TEXT"):
                if old_meas is not None and new_meas is not None and abs(old_meas - new_meas) > 1e-9:
                    txt = be.text if be.dxftype() == "MTEXT" else be.dxf.text
                    new_txt = _replace_number(txt, _display(e, old_meas), _display(e, new_meas))
                    if be.dxftype() == "MTEXT":
                        be.text = new_txt
                    else:
                        be.dxf.text = new_txt
            _stretch_block_entity(be, mv, shift)
    return True


_HANDLERS = {
    "LINE": _line, "LWPOLYLINE": _lwpolyline, "POLYLINE": _polyline, "ARC": _arc, "CIRCLE": _circle,
    "POINT": _point, "TEXT": _anchor, "MTEXT": _anchor, "INSERT": _anchor, "HATCH": _hatch,
    "MULTILEADER": _mleader,
}


def stretch(doc, window: Window, dx: float, dy: float, dz: float = 0.0,
            exclude: Iterable[str] = (), handles: Optional[Iterable[str]] = None) -> dict:
    """Растянуть пространство модели. exclude: handle объектов, которые трогать нельзя;
    handles: если задан, обрабатываются только эти объекты. Возвращает счётчики по типам."""
    mv = _Mover(window, dx, dy, dz)
    excl = {h.upper() for h in exclude}
    only = {h.upper() for h in handles} if handles is not None else None
    moved: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for e in list(doc.modelspace()):
        h = e.dxf.handle.upper()
        if h in excl or (only is not None and h not in only):
            continue
        t = e.dxftype()
        if t == "DIMENSION":
            ok = _dimension(e, mv, doc)
        elif t in _HANDLERS:
            ok = _HANDLERS[t](e, mv)
        else:
            skipped[t] = skipped.get(t, 0) + 1
            continue
        if ok:
            moved[t] = moved.get(t, 0) + 1
    return {"changed": moved, "skipped_types": skipped}


def shrink_symmetric(doc, region: Window, axis: str, center: float, amount: float,
                     exclude: Iterable[str] = (), handles: Optional[Iterable[str]] = None) -> dict:
    """Сжать область на amount симметрично относительно линии axis=center (axis: 'x' или 'y'):
    часть выше (правее) center сдвигается на amount/2 к центру, часть ниже (левее) тоже.
    Объекты, пересекающие центр, сокращаются на amount."""
    if axis not in ("x", "y"):
        raise ValueError("axis должен быть 'x' или 'y'")
    half = amount / 2
    x0, y0, x1, y1 = region
    eps = 1e-6
    if axis == "y":
        upper, lower = (x0, center, x1, y1), (x0, y0, x1, center - eps)
        r1 = stretch(doc, upper, 0, -half, exclude=exclude, handles=handles)
        r2 = stretch(doc, lower, 0, half, exclude=exclude, handles=handles)
    else:
        upper, lower = (center, y0, x1, y1), (x0, y0, center - eps, y1)
        r1 = stretch(doc, upper, -half, 0, exclude=exclude, handles=handles)
        r2 = stretch(doc, lower, half, 0, exclude=exclude, handles=handles)
    merged: dict[str, int] = {}
    for r in (r1, r2):
        for k, v in r["changed"].items():
            merged[k] = merged.get(k, 0) + v
    return {"changed": merged, "skipped_types": r1["skipped_types"]}


# ------------------------------------------------------------------ вырезание полосы

def _points(e) -> list:
    """Определяющие точки объекта для классификации относительно полосы."""
    t = e.dxftype()
    if t == "LINE":
        return [Vec3(e.dxf.start), Vec3(e.dxf.end)]
    if t == "LWPOLYLINE":
        return [Vec3(p[0], p[1]) for p in e.get_points("xy")]
    if t == "POLYLINE":
        return [Vec3(v.dxf.location) for v in e.vertices]
    if t in ("CIRCLE", "ARC"):
        return [_wcs_point(e, e.dxf.center)]
    if t == "POINT":
        return [Vec3(e.dxf.location)]
    if t in ("TEXT", "MTEXT", "INSERT"):
        return [_wcs_point(e, e.dxf.insert)]
    if t == "HATCH":
        pts = []
        for path in e.paths:
            if isinstance(path, PolylinePath):
                pts.extend(Vec3(v[0], v[1]) for v in path.vertices)
            elif isinstance(path, EdgePath):
                for edge in path.edges:
                    if isinstance(edge, LineEdge):
                        pts.extend([Vec3(edge.start), Vec3(edge.end)])
                    elif isinstance(edge, ArcEdge):
                        pts.append(Vec3(edge.center))
        return pts
    if t == "DIMENSION":
        return [Vec3(e.dxf.get(k)) for k in ("defpoint", "defpoint2", "defpoint3", "defpoint4")
                if e.dxf.get(k) is not None]
    if t == "MULTILEADER":
        ctx = e.context
        pts = [Vec3(v) for leader in ctx.leaders for line in leader.lines for v in line.vertices]
        if ctx.mtext is not None:
            pts.append(Vec3(ctx.mtext.insert))
        return pts
    return []


def close_gap(doc, region: Window, axis: str, lo: float, hi: float,
              exclude: Iterable[str] = (), handles: Optional[Iterable[str]] = None,
              delete_inside: bool = True) -> dict:
    """Вырезать полосу lo..hi по оси axis ('x' или 'y') и сомкнуть края: часть до lo сдвигается на
    (hi-lo)/2 к полосе, часть после hi тоже, объекты, пересекающие полосу, сокращаются на hi-lo.
    Объекты целиком внутри полосы удаляются (delete_inside). Объекты, у которых часть точек в полосе,
    а часть снаружи, не меняются и возвращаются в ambiguous: их нужно поправить вручную."""
    if axis not in ("x", "y"):
        raise ValueError("axis должен быть 'x' или 'y'")
    if hi <= lo:
        raise ValueError("hi должен быть больше lo")
    half = (hi - lo) / 2
    excl = {h.upper() for h in exclude}
    only = {h.upper() for h in handles} if handles is not None else None
    x0, y0, x1, y1 = region
    i = 0 if axis == "x" else 1
    deleted, ambiguous, tip_moved = [], [], []
    msp = doc.modelspace()
    for e in list(msp):
        h = e.dxf.handle.upper()
        if h in excl or (only is not None and h not in only):
            continue
        pts = _points(e)
        if not pts or not all(_inside(region, p.x, p.y) for p in pts):
            continue
        coords = [p[i] for p in pts]
        n_in = sum(1 for c in coords if lo < c < hi)
        if n_in == len(coords):
            if delete_inside:
                msp.delete_entity(e)
                deleted.append(h)
            continue
        if n_in:
            if e.dxftype() == "MULTILEADER":
                # выноска едет целиком вместе с тем, на что указывает остриё стрелки
                tip = _points(e)[0][i]
                if not lo < tip < hi:
                    e.translate(*((half if tip <= lo else -half) if i == 0 else 0,
                                  (half if tip <= lo else -half) if i == 1 else 0, 0))
                    tip_moved.append(h)
                    continue
            ambiguous.append(h)
    amb = {h for h in ambiguous}
    skip = excl | amb | set(tip_moved)
    if axis == "y":
        left, right = (x0, y0, x1, lo), (x0, hi, x1, y1)
        r1 = stretch(doc, left, 0, half, exclude=skip, handles=handles)
        r2 = stretch(doc, right, 0, -half, exclude=skip, handles=handles)
    else:
        left, right = (x0, y0, lo, y1), (hi, y0, x1, y1)
        r1 = stretch(doc, left, half, 0, exclude=skip, handles=handles)
        r2 = stretch(doc, right, -half, 0, exclude=skip, handles=handles)
    merged: dict[str, int] = {}
    for r in (r1, r2):
        for k, v in r["changed"].items():
            merged[k] = merged.get(k, 0) + v
    return {"changed": merged, "deleted": deleted, "moved_by_tip": tip_moved, "ambiguous": sorted(amb),
            "skipped_types": r1["skipped_types"]}

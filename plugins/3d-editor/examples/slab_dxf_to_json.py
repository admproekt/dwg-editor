"""Шаг 1: достать геометрию фундаментной плиты из DXF (план и разрез 1-1) в JSON (метры).

Запуск интерпретатором с ezdxf (окружение плагина dwg-editor):
    python slab_dxf_to_json.py plita.dxf slab.json
DWG сначала переведите в DXF (ODA File Converter) или откройте через плагин dwg-editor.
Слои и размеры взяты из чертежа «СТАРАЯ ПЛИТА»: план в АСО-Фундамент и слое 0, разрез 1-1 там же.
"""
import json
import sys

import ezdxf

PLAN = (1169500, 1241000, 1187000, 1251500)     # окно плана в координатах чертежа, мм
SECTION = (1190500, 1237500, 1200500, 1241000)  # окно разреза 1-1


def pts(e):
    if e.dxftype() == "LWPOLYLINE":
        return [(p[0], p[1]) for p in e.get_points()]
    if e.dxftype() == "LINE":
        return [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
    return []


def bbox(p):
    xs, ys = [q[0] for q in p], [q[1] for q in p]
    return (min(xs), min(ys), max(xs), max(ys))


def inside(b, w):
    return w[0] <= b[0] and b[2] <= w[2] and w[1] <= b[1] and b[3] <= w[3]


def main(src, dst):
    doc = ezdxf.readfile(src)
    msp = doc.modelspace()
    plan = []
    for e in msp.query("LWPOLYLINE"):
        p = pts(e)
        if p and inside(bbox(p), PLAN) and len(p) in (4, 5):
            plan.append((e.dxf.layer, bbox(p), e.dxf.handle))
    # плита: прямоугольник на слое 0, самый большой
    slab = max((b for lay, b, _ in plan if lay == "0"), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    ox, oy = slab[0], slab[1]
    rel = lambda b: [round((b[0] - ox) / 1000, 4), round((b[1] - oy) / 1000, 4),
                     round((b[2] - ox) / 1000, 4), round((b[3] - oy) / 1000, 4)]
    found = [(lay, b) for lay, b, _ in plan if lay == "АСО-Фундамент"]
    # внутреннее поле между краевыми балками: самый большой прямоугольник на АСО-Фундамент
    inner = max((b for _, b in found), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]) if b[3] - b[1] < 4700 else 0)
    # поперечные балки: узкие прямоугольники на слое АСО-Фундамент длиной по полю
    inner_h = inner[3] - inner[1]
    mids = [b for _, b in found if abs((b[3] - b[1]) - inner_h) < 1 and (b[2] - b[0]) < 500]
    plates = []
    for lay, b, _ in plan:
        if lay == "0" and abs((b[2] - b[0]) - 200) < 1 and abs((b[3] - b[1]) - 200) < 1:
            plates.append(b)
    # разрез 1-1: толщины
    sec = []
    for e in msp:
        if e.dxf.layer == "АСО-Фундамент" and e.dxftype() == "LWPOLYLINE":
            p = pts(e)
            if p and inside(bbox(p), SECTION):
                sec.append(bbox(p))
    slab_sec = max(sec, key=lambda b: b[2] - b[0])
    t_slab = slab_sec[3] - slab_sec[1]
    hatch = [e for e in msp.query("HATCH") if e.dxf.layer == "АСО-Размеры"]
    prep_bay = None
    for h in hatch:
        v = [q for path in h.paths for q in getattr(path, "vertices", [])]
        if v and inside((min(q[0] for q in v), min(q[1] for q in v), max(q[0] for q in v), max(q[1] for q in v)), SECTION):
            prep_bay = max(q[1] for q in v) - min(q[1] for q in v)
    prep_strip = None
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "АСО-Размеры":
            p = pts(e)
            if p and inside(bbox(p), SECTION) and 99 < bbox(p)[3] - bbox(p)[1] < 101 and (bbox(p)[2] - bbox(p)[0]) > 5000:
                prep_strip = bbox(p)
    beam_depth = slab_sec[1] - prep_strip[3] if prep_strip else 300.0
    data = {
        "units": "m", "source": src, "drawing_origin_mm": [ox, oy],
        "slab": rel(slab), "inner_field": rel(inner),
        "interior_beams": sorted(rel(b) for b in mids),
        "plates": sorted(rel(b) for b in plates),
        "thickness": {"slab": t_slab / 1000, "beam_below_slab": beam_depth / 1000,
                      "prep_under_bays": (prep_bay or 100) / 1000, "prep_under_beams": 0.1,
                      "plate": 0.01, "prep_margin": 0.1, "prep_strip_width": 0.6},
        "notes": {"slab": "разрез 1-1, АСО-Фундамент", "beam_below_slab": "разрез 1-1: низ плиты минус верх подготовки под балкой",
                  "prep": "разрез 1-1, штриховка и полоса под балкой", "plate": "ПРИНЯТО 10 мм, в чертеже не указана"},
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: (v if not isinstance(v, list) or len(v) < 6 else f"{len(v)} шт.") for k, v in data.items()},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

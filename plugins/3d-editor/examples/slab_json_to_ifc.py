"""Шаг 2: собрать IFC фундаментной плиты из JSON шага 1 (slab_dxf_to_json.py).

Запуск интерпретатором из окружения плагина 3d-editor (IfcOpenShell):
    python slab_json_to_ifc.py slab.json plita.ifc
Состав модели: плита, краевые и поперечные балки (выступ под плитой), подготовка В7.5 (под пролётами
и под балками), закладные детали МН118-1. Верх плиты принят за отметку 0,00.
Единицы: метры. Арматура не моделируется.
"""
import json
import sys

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.material
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import numpy as np


def build(data: dict, out: str) -> dict:
    t = data["thickness"]
    sx0, sy0, sx1, sy1 = data["slab"]
    ix0, iy0, ix1, iy1 = data["inner_field"]
    z_top = 0.0
    z_slab0 = z_top - t["slab"]
    z_beam0 = z_slab0 - t["beam_below_slab"]
    z_bay0 = z_slab0 - t["prep_under_bays"]
    z_strip0 = z_beam0 - t["prep_under_beams"]

    m = ifcopenshell.api.project.create_file(version="IFC4")
    proj = ifcopenshell.api.root.create_entity(m, ifc_class="IfcProject", name="Фундаментная плита Фм1")
    ifcopenshell.api.unit.assign_unit(m)
    ctx = ifcopenshell.api.context.add_context(m, context_type="Model")
    body = ifcopenshell.api.context.add_context(m, context_type="Model", context_identifier="Body",
                                                target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.root.create_entity(m, ifc_class="IfcSite", name="Площадка")
    bld = ifcopenshell.api.root.create_entity(m, ifc_class="IfcBuilding", name="КПП")
    st = ifcopenshell.api.root.create_entity(m, ifc_class="IfcBuildingStorey", name="Фундамент")
    st.Elevation = z_top
    ifcopenshell.api.aggregate.assign_object(m, products=[site], relating_object=proj)
    ifcopenshell.api.aggregate.assign_object(m, products=[bld], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(m, products=[st], relating_object=bld)

    mats = {
        "b25": ifcopenshell.api.material.add_material(m, name="Бетон B25 W6 F100", category="concrete"),
        "b75": ifcopenshell.api.material.add_material(m, name="Бетон B7.5", category="concrete"),
        "steel": ifcopenshell.api.material.add_material(m, name="Сталь", category="steel"),
    }
    created = {"IfcSlab": 0, "IfcBeam": 0, "IfcPlate": 0}

    def box(ifc_class, name, rect, z0, height, material, predefined=None, props=None, pset="Pset_Фундамент"):
        x0, y0, x1, y1 = rect
        e = ifcopenshell.api.root.create_entity(m, ifc_class=ifc_class, name=name)
        if predefined:
            e.PredefinedType = predefined
        rep = ifcopenshell.api.geometry.add_slab_representation(
            m, context=body, depth=height, polyline=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        ifcopenshell.api.geometry.assign_representation(m, product=e, representation=rep)
        mx = np.eye(4)
        mx[2, 3] = z0
        ifcopenshell.api.geometry.edit_object_placement(m, product=e, matrix=mx)
        ifcopenshell.api.spatial.assign_container(m, products=[e], relating_structure=st)
        ifcopenshell.api.material.assign_material(m, products=[e], type="IfcMaterial", material=mats[material])
        if props:
            ps = ifcopenshell.api.pset.add_pset(m, product=e, name=pset)
            ifcopenshell.api.pset.edit_pset(m, pset=ps, properties=props)
        created[ifc_class] += 1
        return e

    concrete = {"Класс бетона": "B25", "Водонепроницаемость": "W6", "Морозостойкость": "F100"}
    box("IfcSlab", "Плита Фм1", data["slab"], z_slab0, t["slab"], "b25", "BASESLAB",
        {**concrete, "Толщина, мм": round(t["slab"] * 1000), "Размер в плане, мм": f"{round((sx1 - sx0) * 1000)}x{round((sy1 - sy0) * 1000)}"})
    bw = iy0 - sy0  # ширина краевой балки
    for k, (y0, y1) in enumerate(((sy0, iy0), (iy1, sy1)), 1):
        box("IfcBeam", f"Балка краевая {k}", [sx0, y0, sx1, y1], z_beam0, t["beam_below_slab"], "b25", "BEAM",
            {**concrete, "Ширина, мм": round(bw * 1000), "Выступ под плитой, мм": round(t["beam_below_slab"] * 1000)})
    end_beams = [[sx0, iy0, ix0, iy1], [ix1, iy0, sx1, iy1]]
    trans = sorted(end_beams + [b for b in data["interior_beams"]])
    for k, r in enumerate(trans, 1):
        box("IfcBeam", f"Балка поперечная {k}", r, z_beam0, t["beam_below_slab"], "b25", "BEAM",
            {**concrete, "Ширина, мм": round((r[2] - r[0]) * 1000), "Длина, мм": round((r[3] - r[1]) * 1000),
             "Выступ под плитой, мм": round(t["beam_below_slab"] * 1000)})
    # подготовка под пролётами между поперечными балками
    for k in range(len(trans) - 1):
        x0, x1 = trans[k][2], trans[k + 1][0]
        box("IfcSlab", f"Подготовка B7.5 под пролётом {k + 1}", [x0, iy0, x1, iy1], z_bay0, t["prep_under_bays"],
            "b75", "BASESLAB", {"Класс бетона": "B7.5", "Толщина, мм": round(t["prep_under_bays"] * 1000)})
    # подготовка под балками: полосы шириной 0,6 м (балка 0,4 м и по 0,1 м с каждой стороны)
    mg, w = t["prep_margin"], t["prep_strip_width"]
    prep_props = {"Класс бетона": "B7.5", "Толщина, мм": round(t["prep_under_beams"] * 1000)}
    edge_strips = ([sx0 - mg, sy0 - mg, sx1 + mg, iy0 + mg], [sx0 - mg, iy1 - mg, sx1 + mg, sy1 + mg])
    for k, r in enumerate(edge_strips, 1):
        box("IfcSlab", f"Подготовка B7.5 под краевой балкой {k}", r, z_strip0, t["prep_under_beams"], "b75",
            "BASESLAB", prep_props)
    for k, r in enumerate(trans, 1):
        cx = (r[0] + r[2]) / 2
        box("IfcSlab", f"Подготовка B7.5 под поперечной балкой {k}", [cx - w / 2, iy0 + mg, cx + w / 2, iy1 - mg],
            z_strip0, t["prep_under_beams"], "b75", "BASESLAB", prep_props)
    # закладные детали МН118-1 (верх на отметке 0,00)
    cols = sorted({round((p[0] + p[2]) / 2, 3) for p in data["plates"]})
    rows = sorted({round((p[1] + p[3]) / 2, 3) for p in data["plates"]})
    for p in data["plates"]:
        ci = cols.index(round((p[0] + p[2]) / 2, 3)) + 1
        ri = rows.index(round((p[1] + p[3]) / 2, 3)) + 1
        box("IfcPlate", f"МН118-1 (колонна {ci}, ряд {ri})", p, z_top - t["plate"], t["plate"], "steel",
            "SHEET", {"Марка": "МН118-1", "Колонна": ci, "Ряд": ri, "Толщина, мм": round(t["plate"] * 1000)},
            pset="Pset_Закладная")
    m.write(out)
    return created


if __name__ == "__main__":
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    print(build(data, sys.argv[2]))

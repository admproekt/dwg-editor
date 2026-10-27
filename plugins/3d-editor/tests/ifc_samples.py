"""Синтетическая модель IFC для тестов: плита 12,36 x 5,4 x 0,3 м и четыре стены на двух этажах."""
import math

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import numpy as np


def _matrix(x, y, z, angle_deg=0.0):
    a = math.radians(angle_deg)
    m = np.eye(4)
    m[0, 0], m[0, 1], m[1, 0], m[1, 1] = math.cos(a), -math.sin(a), math.sin(a), math.cos(a)
    m[:3, 3] = (x, y, z)
    return m


def build_sample(path: str) -> str:
    m = ifcopenshell.api.project.create_file(version="IFC4")
    proj = ifcopenshell.api.root.create_entity(m, ifc_class="IfcProject", name="Тестовый проект")
    ifcopenshell.api.unit.assign_unit(m)
    ctx = ifcopenshell.api.context.add_context(m, context_type="Model")
    body = ifcopenshell.api.context.add_context(m, context_type="Model", context_identifier="Body",
                                                target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.root.create_entity(m, ifc_class="IfcSite", name="Площадка")
    bld = ifcopenshell.api.root.create_entity(m, ifc_class="IfcBuilding", name="Здание")
    ifcopenshell.api.aggregate.assign_object(m, products=[site], relating_object=proj)
    ifcopenshell.api.aggregate.assign_object(m, products=[bld], relating_object=site)
    storeys = []
    for i, (name, elev) in enumerate((("Этаж 1", 0.3), ("Этаж 2", 3.3))):
        st = ifcopenshell.api.root.create_entity(m, ifc_class="IfcBuildingStorey", name=name)
        st.Elevation = elev
        ifcopenshell.api.aggregate.assign_object(m, products=[st], relating_object=bld)
        storeys.append(st)
    slab = ifcopenshell.api.root.create_entity(m, ifc_class="IfcSlab", name="Плита")
    rep = ifcopenshell.api.geometry.add_slab_representation(
        m, context=body, depth=0.3, polyline=[(0.0, 0.0), (12.36, 0.0), (12.36, 5.4), (0.0, 5.4)])
    ifcopenshell.api.geometry.assign_representation(m, product=slab, representation=rep)
    ifcopenshell.api.geometry.edit_object_placement(m, product=slab, matrix=_matrix(0, 0, 0))
    ifcopenshell.api.spatial.assign_container(m, products=[slab], relating_structure=storeys[0])
    walls = ((12.36, 0, 0, 0), (5.4, 12.36, 0, 90), (12.36, 12.36, 5.4, 180), (5.4, 0, 5.4, 270))
    for k, (length, x, y, ang) in enumerate(walls):
        w = ifcopenshell.api.root.create_entity(m, ifc_class="IfcWall", name=f"Стена {k + 1}")
        r = ifcopenshell.api.geometry.add_wall_representation(m, context=body, length=length, height=3.0,
                                                              thickness=0.2)
        ifcopenshell.api.geometry.assign_representation(m, product=w, representation=r)
        ifcopenshell.api.geometry.edit_object_placement(m, product=w, matrix=_matrix(x, y, 0.3, ang))
        ifcopenshell.api.spatial.assign_container(m, products=[w], relating_structure=storeys[0])
        ps = ifcopenshell.api.pset.add_pset(m, product=w, name="Pset_WallCommon")
        ifcopenshell.api.pset.edit_pset(m, pset=ps, properties={"IsExternal": True, "FireRating": "EI60"})
    m.write(path)
    return path

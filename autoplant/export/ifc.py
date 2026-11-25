"""IFC4 export (equipment, pipe segments, elbows, valves) via IfcOpenShell."""
from __future__ import annotations
import math
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.shape_builder
Vector = tuple
from ..model import Project, Route
from .. import rack as rackmod
from ..catalog import od, elbow_takeoff, valve_ftf, wall

EQ_CLASS = {"vessel_vertical": "IfcTank", "vessel_horizontal": "IfcTank", "tank": "IfcTank",
            "column": "IfcTank", "pump": "IfcPump", "exchanger": "IfcHeatExchanger"}
VTYPE = {"gate": "ISOLATING", "globe": "REGULATING", "check": "CHECK", "ball": "ISOLATING"}


def _unit(v):
    L = math.sqrt(sum(c * c for c in v))
    return tuple(c / L for c in v)


def _perp(u):
    ref = (0, 0, 1) if abs(u[2]) < 0.9 else (1, 0, 0)
    c = (u[1] * ref[2] - u[2] * ref[1], u[2] * ref[0] - u[0] * ref[2], u[0] * ref[1] - u[1] * ref[0])
    return _unit(c)


def write_ifc(prj: Project, routes: dict[str, Route], path: str):
    f = ifcopenshell.api.run("project.create_file", version="IFC4")
    proj = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name=prj.name)
    ifcopenshell.api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "MILLIMETERS"})
    ctx = ifcopenshell.api.run("context.add_context", f, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    bld = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Plant")
    ifcopenshell.api.run("aggregate.assign_object", f, products=[site], relating_object=proj)
    ifcopenshell.api.run("aggregate.assign_object", f, products=[bld], relating_object=site)
    sb = ifcopenshell.util.shape_builder.ShapeBuilder(f)

    def make(cls, name, items, predefined=None, props=None, pset="AutoPlant"):
        el = ifcopenshell.api.run("root.create_entity", f, ifc_class=cls, name=name)
        if predefined:
            try:
                el.PredefinedType = predefined
            except Exception:
                pass
        rep = f.createIfcShapeRepresentation(body, "Body", "SweptSolid", items)
        ifcopenshell.api.run("geometry.assign_representation", f, product=el, representation=rep)
        ifcopenshell.api.run("spatial.assign_container", f, products=[el], relating_structure=bld)
        if props:
            ps = ifcopenshell.api.run("pset.add_pset", f, product=el, name=pset)
            ifcopenshell.api.run("pset.edit_pset", f, pset=ps, properties=props)
        return el

    def cyl(p, u, length, radius):
        prof = sb.circle(radius=radius)
        return sb.extrude(prof, magnitude=length, position=Vector(p),
                          position_z_axis=Vector(u), position_x_axis=Vector(_perp(u)))

    for e in prj.equipment:
        lo, hi = e.box()
        sx, sy, sz = e.size
        if e.shape == "cylinder_v":
            item = cyl((e.position[0], e.position[1], lo[2]), (0, 0, 1), sz, sx / 2)
        elif e.shape == "cylinder_h":
            item = cyl((lo[0], e.position[1], lo[2] + sz / 2), (1, 0, 0), sx, sy / 2)
        else:
            prof = sb.rectangle(size=(sx, sy))
            item = sb.extrude(prof, magnitude=sz, position=Vector((e.position[0], e.position[1], lo[2])))
        make(EQ_CLASS.get(e.kind, "IfcBuildingElementProxy"), e.id, [item],
             props={"Kind": e.kind, "NozzleCount": len(e.nozzles)})

    for name, lo, hi in rackmod.boxes(prj):
        sx, sy, sz = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
        prof = sb.rectangle(size=(sx, sy))
        item = sb.extrude(prof, magnitude=sz, position=((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]))
        make("IfcColumn" if "-C" in name else "IfcBeam", name, [item], props={"Role": "pipe rack steel"})

    by_id = {l.id: l for l in prj.lines}
    for lid, rt in routes.items():
        if rt.status != "routed":
            continue
        ln = by_id[lid]
        r = od(ln.nps) / 2
        props = {"LineNumber": ln.id, "NPS": ln.nps, "Spec": ln.spec, "Service": ln.service,
                 "OD_mm": od(ln.nps), "Wall_mm": wall(ln.nps)}
        pts = rt.points
        n = len(pts) - 1
        tk = elbow_takeoff(ln.nps)
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            L = math.dist(a, b)
            u = _unit(tuple(b[k] - a[k] for k in range(3)))
            s0 = tk if i > 0 else 0.0
            s1 = tk if i < n - 1 else 0.0
            if L - s0 - s1 <= 1:
                continue
            start = tuple(a[k] + u[k] * s0 for k in range(3))
            make("IfcPipeSegment", f"{lid}-S{i + 1}", [cyl(start, u, L - s0 - s1, r)],
                 predefined="RIGIDSEGMENT", props=props)
        for i in range(1, n):   # elbows: sphere-ish stand-in block at the bend centre
            c = pts[i]
            prof = sb.circle(radius=r * 1.05)
            item = sb.extrude(prof, magnitude=r * 2.1, position=Vector((c[0], c[1], c[2] - r * 1.05)))
            make("IfcPipeFitting", f"{lid}-EL{i}", [item], predefined="BEND", props=props)
        for vt, p in rt.valve_positions:
            L = valve_ftf(ln.nps)
            prof = sb.rectangle(size=(r * 3, r * 3))
            item = sb.extrude(prof, magnitude=L, position=Vector((p[0] - r * 1.5, p[1] - r * 1.5, p[2] - r * 1.5)))
            el = make("IfcValve", f"{lid}-{vt.upper()}", [item], props={**props, "ValveType": vt})
            try:
                el.PredefinedType = VTYPE.get(vt, "NOTDEFINED")
            except Exception:
                pass
    f.write(path)

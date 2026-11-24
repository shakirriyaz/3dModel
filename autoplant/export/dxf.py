"""DXF plan + elevation (X-Z) of equipment and routed pipe centrelines."""
from __future__ import annotations
import math
import ezdxf
from ..model import Project, Route
from ..catalog import od


def write_dxf(prj: Project, routes: dict[str, Route], path: str, stamp: str):
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    doc.layers.add("EQUIPMENT", color=7)
    doc.layers.add("TEXT", color=2)
    doc.layers.add("TITLE", color=1)
    ys = [e.box()[0][1] for e in prj.equipment]
    ye = [e.box()[1][1] for e in prj.equipment]
    zmax = max(e.box()[1][2] for e in prj.equipment)
    off = (min(ys) - 5000) - (zmax + 2000)     # elevation drawn below plan: y' = z + off
    msp.add_text(f"{prj.name} - {stamp}", dxfattribs={"layer": "TITLE", "height": 400}).set_placement(
        (min(e.box()[0][0] for e in prj.equipment), max(ye) + 2500))
    for e in prj.equipment:
        lo, hi = e.box()
        if e.shape == "cylinder_v":
            msp.add_circle((e.position[0], e.position[1]), e.size[0] / 2, dxfattribs={"layer": "EQUIPMENT"})
        else:
            msp.add_lwpolyline([(lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1])], close=True,
                               dxfattribs={"layer": "EQUIPMENT"})
        msp.add_lwpolyline([(lo[0], lo[2] + off), (hi[0], lo[2] + off), (hi[0], hi[2] + off), (lo[0], hi[2] + off)],
                           close=True, dxfattribs={"layer": "EQUIPMENT"})
        msp.add_text(e.id, dxfattribs={"layer": "TEXT", "height": 300}).set_placement(
            (e.position[0], e.position[1]), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
        for n in e.nozzles:
            p = e.nozzle_pos(n)
            msp.add_circle((p[0], p[1]), 100, dxfattribs={"layer": "EQUIPMENT"})
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is None or rt.status != "routed":
            continue
        layer = "LINE-" + ln.id
        doc.layers.add(layer, color=3 + (hash(ln.id) % 5))
        w = max(1, int(od(ln.nps)))  # lineweight hint (1/100 mm), capped by DXF set
        for a, b in zip(rt.points, rt.points[1:]):
            msp.add_line((a[0], a[1]), (b[0], b[1]), dxfattribs={"layer": layer})
            msp.add_line((a[0], a[2] + off), (b[0], b[2] + off), dxfattribs={"layer": layer})
        mid = rt.points[len(rt.points) // 2]
        msp.add_text(f"{ln.id} {ln.nps}\"-{ln.spec}", dxfattribs={"layer": "TEXT", "height": 200}).set_placement((mid[0], mid[1] + 150))
    doc.saveas(path)

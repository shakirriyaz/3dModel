"""Standalone STEP writer: scene.json -> AP214 STEP with real solids and names (OpenCASCADE via the
`cadquery-ocp` wheel). Runs in its OWN Python environment (no autoplant imports) so OCP never shares
a process with IfcOpenShell. Usage:  python step_worker.py scene.json out.step   |   --check
"""
import json
import math
import sys


def main():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeTorus
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder
    from OCP.IFSelect import IFSelect_RetDone

    if sys.argv[1] == "--check":
        print("ok")
        return
    scene = json.load(open(sys.argv[1]))
    out = sys.argv[2]

    def unit(v):
        L = math.sqrt(sum(c * c for c in v))
        return tuple(c / L for c in v)

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    def cyl(p, d, length, r):
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*p), gp_Dir(*d)), r, length).Shape()

    def box(lo, hi):
        return BRepPrimAPI_MakeBox(gp_Pnt(*lo), gp_Pnt(*hi)).Shape()

    parts = []   # (name, shape)
    for e in scene["equipment"]:
        lo, hi = e["lo"], e["hi"]
        if e["shape"] == "cylinder_v":
            shp = cyl(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]), (0, 0, 1), hi[2] - lo[2], (hi[0] - lo[0]) / 2)
        elif e["shape"] == "cylinder_h":
            shp = cyl((lo[0], (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2), (1, 0, 0), hi[0] - lo[0], (hi[1] - lo[1]) / 2)
        else:
            shp = box(lo, hi)
        parts.append((e["id"], shp))
    for s in scene.get("steel", []):
        parts.append((s["id"], box(s["lo"], s["hi"])))
    for ln in scene["lines"]:
        pts, r = ln["points"], ln["r"]
        tk = 1.5 * ln["nps"] * 25.4
        n = len(pts) - 1
        for i in range(n):
            a, b = pts[i], pts[i + 1]
            L = math.dist(a, b)
            u = unit([b[k] - a[k] for k in range(3)])
            s0 = tk if i > 0 else 0.0
            s1 = tk if i < n - 1 else 0.0
            if L - s0 - s1 > 1:
                parts.append((f"{ln['id']}-S{i + 1}", cyl([a[k] + u[k] * s0 for k in range(3)], u, L - s0 - s1, r)))
        for i in range(1, n):
            c = pts[i]
            u = unit([pts[i][k] - pts[i - 1][k] for k in range(3)])
            v = unit([pts[i + 1][k] - pts[i][k] for k in range(3)])
            ax = cross(u, v)
            if max(abs(x) for x in ax) < 1e-9:
                continue
            ax = unit(ax)
            centre = [c[k] - u[k] * tk + v[k] * tk for k in range(3)]
            xdir = [-v[k] for k in range(3)]
            parts.append((f"{ln['id']}-EL{i}", BRepPrimAPI_MakeTorus(
                gp_Ax2(gp_Pnt(*centre), gp_Dir(*ax), gp_Dir(*xdir)), tk, r, math.pi / 2).Shape()))
        for vt, p in ln.get("valves", []):
            parts.append((f"{ln['id']}-{vt.upper()}", box([p[0] - r * 1.5, p[1] - r * 1.5, p[2] - r * 1.5],
                                                         [p[0] + r * 1.5, p[1] + r * 1.5, p[2] + r * 1.5])))

    # XDE document so every solid carries its tag name in the STEP file
    try:
        from OCP.TDocStd import TDocStd_Document
        from OCP.XCAFApp import XCAFApp_Application
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDataStd import TDataStd_Name
        from OCP.STEPCAFControl import STEPCAFControl_Writer
        app = XCAFApp_Application.GetApplication_s()
        doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
        app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
        st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        for name, shp in parts:
            lab = st.AddShape(shp, False)
            TDataStd_Name.Set_s(lab, TCollection_ExtendedString(name))
        w = STEPCAFControl_Writer()
        w.Transfer(doc)
        ok = w.Write(out) == IFSelect_RetDone
    except ImportError:
        from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
        comp, bld = TopoDS_Compound(), BRep_Builder()
        bld.MakeCompound(comp)
        for _, shp in parts:
            bld.Add(comp, shp)
        w = STEPControl_Writer()
        w.Transfer(comp, STEPControl_AsIs)
        ok = w.Write(out) == IFSelect_RetDone
    if not ok:
        sys.exit("STEP write failed")
    print(len(parts), "solids")


if __name__ == "__main__":
    main()

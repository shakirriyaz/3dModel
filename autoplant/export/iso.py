"""Isometric outputs (SVG per line, one multi-sheet PDF, DXF per line) from the drawing engine."""
from __future__ import annotations
import os
from ..model import Project, Route
from ..drawing import iso as isomod


def _sheets(prj: Project, routes: dict[str, Route], stamp: str):
    std = prj.drawing
    lines = [l for l in prj.lines if routes.get(l.id) is not None and routes[l.id].status == "routed"]
    ok = "PASSED" in stamp
    for n, ln in enumerate(lines, 1):
        yield ln, isomod.build_iso(prj, ln, routes[ln.id], std, ok, stamp, n, len(lines))


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def write_svgs(prj: Project, routes: dict[str, Route], outdir: str, stamp: str) -> list[str]:
    names = []
    for ln, sh in _sheets(prj, routes, stamp):
        n = f"{ln.id}.svg"
        with open(os.path.join(outdir, n), "w") as f:
            f.write(sh.to_svg())
        names.append(n)
    return names


def write_pdf(prj: Project, routes: dict[str, Route], bom_rows, path: str, stamp: str):
    from reportlab.pdfgen import canvas
    W, H = prj.drawing.paper("iso")
    c = canvas.Canvas(path, pagesize=(W * 72 / 25.4, H * 72 / 25.4))
    c.setTitle(f"{prj.name} - piping isometrics")
    for _, sh in _sheets(prj, routes, stamp):
        sh.to_pdf(c, prj.drawing.font)
        c.showPage()
    c.save()


def write_dxfs(prj: Project, routes: dict[str, Route], outdir: str, stamp: str) -> list[str]:
    import ezdxf
    names = []
    for ln, sh in _sheets(prj, routes, stamp):
        doc = ezdxf.new("R2010", setup=True)
        doc.units = ezdxf.units.MM
        sh.to_dxf(doc.modelspace())
        n = f"{_safe(ln.id)}.dxf"
        doc.saveas(os.path.join(outdir, n))
        names.append(n)
    return names


def write_ga_pdf(prj: Project, routes: dict[str, Route], path: str, stamp: str):
    from reportlab.pdfgen import canvas
    from ..drawing import ga
    std = prj.drawing
    W, H = std.paper("ga")
    c = canvas.Canvas(path, pagesize=(W * 72 / 25.4, H * 72 / 25.4))
    c.setTitle(f"{prj.name} - general arrangement")
    views = list(ga.VIEWS)
    for n, v in enumerate(views, 1):
        ga.build_ga(prj, routes, std, "PASSED" in stamp, stamp, v, n, len(views)).to_pdf(c, std.font)
        c.showPage()
    c.save()


def write_ga_dxf(prj: Project, routes: dict[str, Route], path: str, stamp: str):
    import ezdxf
    from ..drawing import ga
    std = prj.drawing
    W, H = std.paper("ga")
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    for n, v in enumerate(ga.VIEWS):
        ga.build_ga(prj, routes, std, "PASSED" in stamp, stamp, v, n + 1, len(ga.VIEWS)).to_dxf(doc.modelspace(), offset=(n * (W + 50), 0))
    doc.saveas(path)

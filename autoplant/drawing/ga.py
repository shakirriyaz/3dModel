"""General arrangement sheets (plan + two elevations) at a standard scale, with grid, tags, line numbers,
north arrow, scale bar and the same title block as the isometrics."""
from __future__ import annotations
import math
from ..model import Project, Route
from .. import rack as rackmod
from .canvas import Sheet
from .standard import DrawingStandard
from . import titleblock

SCALES = [20, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000]
VIEWS = {"plan": ("PLAN", 0, 1), "elev_x": ("ELEVATION LOOKING NORTH", 0, 2), "elev_y": ("ELEVATION LOOKING WEST", 1, 2)}


def _extent(prj, routes, ia, ib):
    lo, hi = [1e18, 1e18], [-1e18, -1e18]

    def add(p):
        for k, i in enumerate((ia, ib)):
            lo[k], hi[k] = min(lo[k], p[i]), max(hi[k], p[i])
    for e in prj.equipment:
        a, b = e.box()
        add(a), add(b)
    for r in routes.values():
        for p in r.points:
            add(p)
    for _, a, b in rackmod.boxes(prj):
        add(a), add(b)
    return lo, hi


def build_ga(prj: Project, routes: dict[str, Route], std: DrawingStandard, ok: bool, stamp: str, view: str, sheet_no: int, sheets: int) -> Sheet:
    title, ia, ib = VIEWS[view]
    W, H = std.paper("ga")
    sh = Sheet(W, H)
    inner = titleblock.frame(sh, std)
    x0, y0, x1, y1 = inner
    tb = titleblock.title_block(sh, std, inner, {
        "title": f"GENERAL ARRANGEMENT - {title}", "line": "-", "spec": "-", "service": "", "size": "", "project": prj.name[:34],
        "from": "", "to": "", "doc_no": std.doc_number_ga.format(project=(std.project_no or "PRJ"), view=view.upper(), sheet=sheet_no),
        "sheet": sheet_no, "sheets": sheets}, ok, stamp)
    area = (x0 + 12, tb[3] + 10, x1 - 12, y1 - 12)
    lo, hi = _extent(prj, routes, ia, ib)
    wd, ht = hi[0] - lo[0], hi[1] - lo[1]
    aw, ah = area[2] - area[0], area[3] - area[1]
    scale = next((s for s in SCALES if wd / s <= aw and ht / s <= ah), SCALES[-1])
    ox = area[0] + (aw - wd / scale) / 2
    oy = area[1] + (ah - ht / scale) / 2
    T = lambda p: (ox + (p[ia] - lo[0]) / scale, oy + (p[ib] - lo[1]) / scale)
    labels = []

    # grid (every 5 or 10 m so there are 6..20 lines)
    step = next((s for s in (2000, 5000, 10000, 20000, 50000) if wd / s <= 14), 100000)
    gx0 = math.floor(lo[0] / step) * step
    gy0 = math.floor(lo[1] / step) * step
    xs = [gx0 + i * step for i in range(int((hi[0] - gx0) / step) + 2)]
    ys = [gy0 + i * step for i in range(int((hi[1] - gy0) / step) + 2)]
    for i, gx in enumerate(xs):
        a, b = T_xy(T, ia, ib, gx, lo[1]), T_xy(T, ia, ib, gx, hi[1])
        sh.line([(a[0], a[1] - 4), (b[0], b[1] + 4)], std.w_thin * 0.6, "grey", dash=True)
        sh.circle(a[0], a[1] - 8, 3.0, std.w_thin)
        sh.text(a[0], a[1] - 9.0, "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i % 26], 2.6, anchor="c")
    for j, gy in enumerate(ys):
        a, b = T_xy(T, ia, ib, lo[0], gy), T_xy(T, ia, ib, hi[0], gy)
        sh.line([(a[0] - 4, a[1]), (b[0] + 4, b[1])], std.w_thin * 0.6, "grey", dash=True)
        sh.circle(a[0] - 8, a[1], 3.0, std.w_thin)
        sh.text(a[0] - 8, a[1] - 0.9, str(j + 1) if view == "plan" else f"{gy / 1000:g}", 2.2 if view != "plan" else 2.6, anchor="c")

    # rack steel
    for name, a, b in rackmod.boxes(prj):
        p0, p1 = T(a), T(b)
        sh.rect(min(p0[0], p1[0]), min(p0[1], p1[1]), abs(p1[0] - p0[0]) or 0.3, abs(p1[1] - p0[1]) or 0.3, std.w_thin * 0.7, None, "grey", "THIN")

    # equipment
    for e in prj.equipment:
        lo3, hi3 = e.box()
        p0, p1 = T(lo3), T(hi3)
        xa, xb, ya, yb = min(p0[0], p1[0]), max(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[1], p1[1])
        if view == "plan" and e.shape == "cylinder_v":
            sh.circle((xa + xb) / 2, (ya + yb) / 2, (xb - xa) / 2, std.w_pipe * 0.8)
        elif e.shape in ("cylinder_h", "cylinder_v") and view != "plan":
            sh.rect(xa, ya, xb - xa, yb - ya, std.w_pipe * 0.8)
            if e.shape == "cylinder_v":
                sh.line([(xa + (xb - xa) * 0.15, ya), (xa + (xb - xa) * 0.15, yb)], std.w_thin)
        else:
            sh.rect(xa, ya, xb - xa, yb - ya, std.w_pipe * 0.8)
        cxm, cym = (xa + xb) / 2, (ya + yb) / 2
        sh.text(cxm, cym - 1.0, e.id[-18:], std.text_h, anchor="c", bold=True)
        sh.text(cxm, cym - 4.2, e.kind.replace("_", " ").upper(), std.text_h * 0.7, anchor="c", color="grey")
        for n in e.nozzles:
            q = T(e.nozzle_pos(n))
            sh.circle(q[0], q[1], 0.9, std.w_thin, "white")

    # piping
    by_id = {l.id: l for l in prj.lines}
    for lid, r in routes.items():
        if r.status != "routed":
            continue
        ln = by_id[lid]
        dm = prj.dims(ln)
        w = max(0.3, min(1.0, dm.od / 250.0 + 0.3))
        pts = [T(p) for p in r.points]
        sh.line(pts, w, "black", layer="PIPE")
        # label on the longest visible leg
        legs = sorted(((math.dist(a, b), a, b) for a, b in zip(pts, pts[1:])), reverse=True)
        if legs and legs[0][0] > 6:
            L, a, b = legs[0]
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            ang = ang - 180 if ang > 90 else (ang + 180 if ang < -90 else ang)
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            nrm = (-math.sin(math.radians(ang)), math.cos(math.radians(ang)))
            txt = f'{lid[-16:]}  {ln.nps:g}"-{ln.spec}'
            if len(txt) * std.text_h * 0.5 < L:
                sh.text(m[0] + nrm[0] * 1.2, m[1] + nrm[1] * 1.2, txt, std.text_h * 0.8, rot=ang, anchor="c")
            u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
            sh.arrow((m[0] - u[0] * 3, m[1] - u[1] * 3), (m[0] + u[0] * 2, m[1] + u[1] * 2), 2.6, std.w_thin)

    # north arrow (plan) / elevation datum (elevations)
    ax, ay = area[2] - 14, area[3] - 14
    if view == "plan":
        sh.circle(ax, ay, 9, std.w_thin)
        sh.arrow((ax, ay - 8), (ax, ay + 8), 5, std.w_thin)
        sh.text(ax - 1.4, ay + 10.5, "N", std.text_h * 1.3, bold=True)
    else:
        for z in range(0, int(hi[1]) + 2000, 2000):
            pz = T((0, 0, z))[1] if ib == 2 else None
            if pz is not None and area[1] <= pz <= area[3]:
                sh.line([(area[0] - 10, pz), (area[0] - 3, pz)], std.w_thin)
                sh.text(area[0] - 11, pz + 0.8, f"EL +{z}", std.dim_h * 0.9, anchor="r")
    # scale bar + scale text
    bx, by = area[0] + 4, area[1] - 5
    bar = 10000 / scale if scale <= 500 else 50000 / scale
    cap = "10 m" if scale <= 500 else "50 m"
    sh.line([(bx, by), (bx + bar, by)], std.w_pipe)
    for t in (0, 0.5, 1):
        sh.line([(bx + bar * t, by - 1.2), (bx + bar * t, by + 1.2)], std.w_thin)
    sh.text(bx + bar + 2, by - 1, cap, std.text_h * 0.9)
    sh.text(bx, by + 3.5, f"SCALE 1:{scale}", std.text_h, bold=True)
    titleblock.notes_block(sh, std, x1 - 100 if False else area[0] + 70, area[1] - 1, 140, None)
    return sh


def T_xy(T, ia, ib, a, b):
    p = [0.0, 0.0, 0.0]
    p[ia], p[ib] = a, b
    return T(p)

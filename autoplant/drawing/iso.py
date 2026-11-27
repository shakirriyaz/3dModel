"""Fabrication isometric sheet for one routed line (conventional piping-iso content, driven by a DrawingStandard)."""
from __future__ import annotations
import math
from ..model import Project, Route, split_ref
from .. import bom as bommod
from .canvas import Sheet
from .standard import DrawingStandard
from . import spools, titleblock

C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))
AXV = {0: (C30, S30), 1: (-C30, S30), 2: (0.0, 1.0)}          # screen direction of +X, +Y(north), +Z
KIND_ORDER = ["pipe", "elbow", "branch", "flange", "gasket", "bolting", "valve"]


def _proj(p):
    return (p[0] - 0) * C30 - p[1] * C30, (p[0] + p[1]) * S30 + p[2]


def _axis(a, b):
    d = [abs(b[i] - a[i]) for i in range(3)]
    return d.index(max(d))


class Occupied:
    def __init__(self):
        self.boxes = []

    def add(self, b):
        self.boxes.append(b)

    def hit(self, b, pad=0.6):
        x0, y0, x1, y1 = b
        return any(x0 - pad < q[2] and q[0] < x1 + pad and y0 - pad < q[3] and q[1] < y1 + pad for q in self.boxes)


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) if L2 else 0.0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def items_for_line(prj: Project, ln, rt: Route) -> tuple[list[dict], dict]:
    """Numbered item list for the sheet + lookup (kind, code|desc) -> item no."""
    comps = bommod.components(prj, {ln.id: rt})
    agg, order = {}, []
    for c in comps:
        key = (c["kind"], c["code"] or c["description"], c["unit"])
        if key not in agg:
            agg[key] = {"kind": c["kind"], "code": c["code"], "description": c["description"], "nps": c["nps"], "unit": c["unit"], "qty": 0.0, "key": key}
            order.append(key)
        agg[key]["qty"] += c["qty"]
    items = sorted((agg[k] for k in order), key=lambda a: KIND_ORDER.index(a["kind"]))
    for i, it in enumerate(items, 1):
        it["no"] = i
    return items, {it["key"]: it["no"] for it in items}


def build_iso(prj: Project, ln, rt: Route, std: DrawingStandard, ok: bool, stamp: str, sheet_no: int = 1, sheets: int = 1) -> Sheet:
    W, H = std.paper("iso")
    sh = Sheet(W, H)
    inner = titleblock.frame(sh, std)
    x0, y0, x1, y1 = inner
    pts = rt.points
    fab = spools.breakdown(prj, ln, rt, std)
    items, itemno = items_for_line(prj, ln, rt)
    dm = prj.dims(ln)
    spec = prj.spec_view(ln.spec).spec
    rating = spec.rating if spec else ""

    # ----------------------------------------------------------- title block + regions
    from_ref = ln.from_
    to_ref = "PLANT BOUNDARY" if ln.is_stub else (f"TEE INTO {ln.branch_of}" if ln.branch_of else ln.to)
    tb = titleblock.title_block(sh, std, inner, {
        "title": "PIPING ISOMETRIC", "line": ln.id, "spec": f"{ln.spec}" + (f"  ({spec.material})" if spec and spec.material else ""),
        "service": ln.service, "size": f'{ln.nps:g}" {rating}'.strip(), "project": prj.name[:34], "from": from_ref[-34:], "to": to_ref[-34:],
        "doc_no": std.doc_number_iso.format(project=(std.project_no or "PRJ"), line=ln.id[-24:].lstrip("-_ "), sheet=sheet_no), "sheet": sheet_no, "sheets": sheets}, ok, stamp)
    strip_top = tb[3] + 2.0
    rc_w = 98.0 if (x1 - x0) > 380 else 0.0
    draw = (x0 + 3, strip_top + 2, x1 - rc_w - 3, y1 - 3)

    # ----------------------------------------------------------- lists (bottom-left strip + right column)
    mat_rows = [[it["no"], f'{it["qty"]:.2f} m' if it["unit"] == "m" else f'{it["qty"]:.0f} {it["unit"]}', f'{it["nps"]:g}"', it["description"], it["code"]] for it in items]
    ytab, _ = titleblock.table(sh, std, x0 + 3, strip_top + 1, ["ITEM", "QTY", "NPS", "DESCRIPTION", "COMMODITY CODE"], [10, 17, 10, 70, 38], mat_rows,
                               title="MATERIAL LIST", row_h=4.6, max_rows=max(int((strip_top - y0 - 11) // 4.6), 3))
    cut_rows = [[f'P{p["no"]}', f'{p["length"]:.0f}', spools_tag(fab, p["spool"])] for p in fab["pieces"]]
    avail_w = (tb[0] - 3) - (x0 + 3 + 145 + 3)
    if avail_w > 44:
        titleblock.table(sh, std, x0 + 3 + 145 + 3, strip_top + 1, ["PIECE", "CUT LEN mm", "SPOOL"], [15, 22, 14], cut_rows, title="CUT LIST",
                         row_h=4.6, max_rows=max(int((strip_top - y0 - 11) // 4.6), 3))
    if rc_w:
        rx = x1 - rc_w + 2
        over = [s["tag"] for s in fab["spools"] if s.get("over_limit")]
        extra = ([f"SPOOL {', '.join(over)} EXCEEDS {std.spool_max_length:.0f} MM - NO FEASIBLE FIELD WELD LOCATION (CHECK ROUTING)."] if over else [])
        yb = titleblock.notes_block(sh, std, rx, y1 - 2, rc_w - 5, extra)
        w_rows = [[w["tag"], "FIELD" if w["field"] else "SHOP", w["joint"], w["size"]] for w in fab["welds"]]
        yb, _ = titleblock.table(sh, std, rx, yb - 3, ["WELD", "TYPE", "JOINT", "SIZE"], [14, 14, 40, 14], w_rows, title="WELD LIST", row_h=4.4,
                                 max_rows=max(int((yb - (strip_top + 40)) // 4.4) - 4, 3))
        if std.show_spools:
            s_rows = [[s["tag"], f'{s["length"]:.0f}', f'{s["cut_m"]:.2f}', f'{s["weight_kg"]:.0f}', s["welds"]] for s in fab["spools"]]
            titleblock.table(sh, std, rx, yb - 4, ["SPOOL", "LEN mm", "PIPE m", "kg", "WELDS"], [16, 20, 20, 14, 18], s_rows, title="SPOOLS", row_h=4.4, max_rows=8)

    # ----------------------------------------------------------- projection fit
    sp = [_proj(p) for p in pts]
    pad = 30.0
    bx0, by0 = min(q[0] for q in sp), min(q[1] for q in sp)
    bw, bh = max(q[0] for q in sp) - bx0, max(q[1] for q in sp) - by0
    aw, ah = draw[2] - draw[0] - 2 * pad, draw[3] - draw[1] - 2 * pad
    scale = min(aw / bw if bw > 1 else 1e9, ah / bh if bh > 1 else 1e9, 0.1)
    ox = draw[0] + pad + (aw - bw * scale) / 2 - bx0 * scale
    oy = draw[1] + pad + (ah - bh * scale) / 2 - by0 * scale
    P = lambda p: (ox + _proj(p)[0] * scale, oy + _proj(p)[1] * scale)
    S = [P(p) for p in pts]
    cx_, cy_ = sum(q[0] for q in S) / len(S), sum(q[1] for q in S) / len(S)
    occ = Occupied()
    for a, b in zip(S, S[1:]):
        occ.add((min(a[0], b[0]) - 1.5, min(a[1], b[1]) - 1.5, max(a[0], b[0]) + 1.5, max(a[1], b[1]) + 1.5))

    # ----------------------------------------------------------- pipe
    sh.line(S, std.w_pipe * 1.5, layer="PIPE")
    # connection symbols at the ends
    sh.circle(*S[0], 1.6, std.w_thin, "white")
    if not ln.branch_of:
        sh.circle(*S[-1], 1.6, std.w_thin, "white")

    def unit(a, b):
        L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        return ((b[0] - a[0]) / L, (b[1] - a[1]) / L)

    def pos_s(s):
        return P(spools.point_at(pts, fab["cum"], s))

    def leg_dir_at(s):
        i = next((k for k in range(len(pts) - 1) if s <= fab["cum"][k + 1] + 1e-6), len(pts) - 2)
        return unit(S[i], S[i + 1])

    # flow arrows
    if std.show_flow_arrows:
        for i, (a, b) in enumerate(zip(S, S[1:])):
            Ls = math.dist(a, b)
            if Ls > 22 or (Ls == max(math.dist(p, q) for p, q in zip(S, S[1:])) and Ls > 8):
                m = (a[0] + (b[0] - a[0]) * 0.55, a[1] + (b[1] - a[1]) * 0.55)
                u = unit(a, b)
                sh.arrow((m[0] - u[0] * 4, m[1] - u[1] * 4), (m[0] + u[0] * 2.5, m[1] + u[1] * 2.5), 3.2, std.w_thin, layer="SYMBOL")
                occ.add((m[0] - 4, m[1] - 4, m[0] + 4, m[1] + 4))

    # valves
    for vt, p3 in rt.valve_positions:
        c = P(p3)
        i = min(range(len(pts) - 1), key=lambda k: spools._dist_seg(p3, pts[k], pts[k + 1]))
        u = unit(S[i], S[i + 1])
        n = (-u[1], u[0])
        ln_v = max(8.0, min(12.0, dm.ftf(vt) * scale))
        wv = 5.0
        tri = lambda sgn: [(c[0] + sgn * u[0] * ln_v / 2 + n[0] * wv / 2, c[1] + sgn * u[1] * ln_v / 2 + n[1] * wv / 2),
                           (c[0] + sgn * u[0] * ln_v / 2 - n[0] * wv / 2, c[1] + sgn * u[1] * ln_v / 2 - n[1] * wv / 2), c]
        if vt == "check":
            sh.poly(tri(1), std.w_thin, "white", layer="SYMBOL")
            sh.line([(c[0] - u[0] * ln_v / 2 + n[0] * wv / 2, c[1] - u[1] * ln_v / 2 + n[1] * wv / 2), (c[0] - u[0] * ln_v / 2 - n[0] * wv / 2, c[1] - u[1] * ln_v / 2 - n[1] * wv / 2)], std.w_pipe, layer="SYMBOL")
        else:
            sh.poly(tri(1), std.w_thin, "white", layer="SYMBOL")
            sh.poly(tri(-1), std.w_thin, "white", layer="SYMBOL")
            if vt in ("globe", "ball"):
                sh.circle(c[0], c[1], 1.1, std.w_thin, "black" if vt == "ball" else "white", layer="SYMBOL")
        # actuator stem (hand-wheel symbol) perpendicular, upwards on screen
        up = (0.0, 1.0)
        sh.line([c, (c[0] + up[0] * 6.5, c[1] + up[1] * 6.5)], std.w_thin, layer="SYMBOL")
        sh.line([(c[0] - 2.2, c[1] + 6.5), (c[0] + 2.2, c[1] + 6.5)], std.w_pipe, layer="SYMBOL")
        occ.add((c[0] - 7, c[1] - 7, c[0] + 7, c[1] + 8))
        lbl = vt.upper() + " VALVE"
        lw = len(lbl) * std.text_h * 0.72 * 0.55
        for dx, dy, anc in ((3.5, 5.0, "l"), (-3.5, 5.0, "r"), (3.5, -7.0, "l"), (-3.5, -7.0, "r")):
            tx, ty = c[0] + dx, c[1] + dy
            box = (tx - (lw if anc == "r" else 0), ty - 0.5, tx + (lw if anc == "l" else 0), ty + std.text_h * 0.8)
            if not occ.hit(box, 0.2):
                sh.text(tx, ty, lbl, std.text_h * 0.72, anchor=anc)
                occ.add(box)
                break

    # bolted flange joints
    for j in fab["joints"]:
        c = pos_s(j["s"])
        u = leg_dir_at(j["s"])
        n = (-u[1], u[0])
        for off in (-0.55, 0.55):
            sh.line([(c[0] + u[0] * off + n[0] * 2.1, c[1] + u[1] * off + n[1] * 2.1), (c[0] + u[0] * off - n[0] * 2.1, c[1] + u[1] * off - n[1] * 2.1)], std.w_pipe * 0.8, layer="SYMBOL")

    # welds
    if std.show_welds:
        for w in fab["welds"]:
            c = P(w["pos"])
            u = leg_dir_at(w["s"])
            n = (-u[1], u[0])
            sh.circle(c[0], c[1], 1.0, std.w_thin, "black", layer="WELD")
            if w["field"]:
                tip = (c[0] + n[0] * 4.5, c[1] + n[1] * 4.5)
                sh.line([c, tip], std.w_thin, layer="WELD")
                sh.poly([tip, (tip[0] + u[0] * 2.8 + n[0] * 0.4, tip[1] + u[1] * 2.8 + n[1] * 0.4), (tip[0] + n[0] * 2.2, tip[1] + n[1] * 2.2)], std.w_thin, "black", layer="WELD")
            lbl = (len(w["tag"]) * std.text_h * 0.5)
            for k, sgn in enumerate((1, -1, 2, -2)):
                tx, ty = c[0] + n[0] * 3.2 * sgn + u[0] * 0.5, c[1] + n[1] * 3.2 * sgn + u[1] * 0.5
                box = (tx - 0.5, ty - 0.5, tx + lbl, ty + std.text_h * 0.75)
                if not occ.hit(box, 0.2):
                    sh.text(tx, ty, w["tag"], std.text_h * 0.7, layer="WELD", color="red" if w["field"] else "black")
                    occ.add(box)
                    break

    # spool boxes
    if std.show_spools and len(fab["spools"]) > 1:
        for s in fab["spools"]:
            c = P(s["pos"])
            for r in (8, 12, 16):
                for ang in (90, -90, 30, 150, -30, -150):
                    bx = c[0] + r * math.cos(math.radians(ang))
                    by = c[1] + r * math.sin(math.radians(ang))
                    box = (bx - 5, by - 2.8, bx + 5, by + 2.8)
                    if not occ.hit(box, 0.5):
                        sh.rect(bx - 5, by - 2.8, 10, 5.6, std.w_thin, "white", layer="SYMBOL")
                        sh.text(bx, by - 1.0, s["tag"], std.text_h, anchor="c", bold=True)
                        sh.line([c, (bx - (bx - c[0]) * 0.0, by - 2.8 if by > c[1] else by + 2.8)], std.w_thin, dash=True, layer="SYMBOL")
                        occ.add(box)
                        break
                else:
                    continue
                break

    # dimensions
    if std.show_dimensions:
        for i, (a3, b3) in enumerate(zip(pts, pts[1:])):
            L3 = math.dist(a3, b3)
            a, b = S[i], S[i + 1]
            if L3 < std.min_dim_leg or math.dist(a, b) < 3.0:
                continue
            ax = _axis(a3, b3)
            u = unit(a, b)
            placed = False
            axes = [k for k in (0, 1, 2) if k != ax]
            cands = []
            for dist in (9.0, 15.0, 21.0, 28.0, 36.0, 46.0):
                for k in axes:
                    for sgn in (1, -1):
                        v = (AXV[k][0] * sgn, AXV[k][1] * sgn)
                        away = ((a[0] + b[0]) / 2 + v[0] * dist - cx_) ** 2 + ((a[1] + b[1]) / 2 + v[1] * dist - cy_) ** 2
                        cands.append((dist, -away, v))
            cands.sort(key=lambda t: (t[0], t[1]))
            for dist, _, v in cands:
                a2, b2 = (a[0] + v[0] * dist, a[1] + v[1] * dist), (b[0] + v[0] * dist, b[1] + v[1] * dist)
                txt = f"{L3:.0f}"
                tw = len(txt) * std.dim_h * 0.55
                m = ((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2)
                box = (min(a2[0], b2[0]) - 1, min(a2[1], b2[1]) - 1, max(a2[0], b2[0]) + 1, max(a2[1], b2[1]) + 1 + std.dim_h)
                if not (box[0] > draw[0] and box[2] < draw[2] and box[1] > draw[1] and box[3] < draw[3]):
                    continue
                samples = [(a2[0] + (b2[0] - a2[0]) * t / 6, a2[1] + (b2[1] - a2[1]) * t / 6) for t in range(7)]
                crosses = any(_seg_dist(sp_, S[j], S[j + 1]) < 2.2 for sp_ in samples for j in range(len(S) - 1) if j != i)
                if crosses or occ.hit(box, 0.2):
                    continue
                placed = True
                break
            if not placed:
                dist, _, v = cands[0]
                a2, b2 = (a[0] + v[0] * dist, a[1] + v[1] * dist), (b[0] + v[0] * dist, b[1] + v[1] * dist)
                m = ((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2)
                box = (min(a2[0], b2[0]) - 1, min(a2[1], b2[1]) - 1, max(a2[0], b2[0]) + 1, max(a2[1], b2[1]) + 1 + std.dim_h)
            occ.add(box)
            for p0, p1 in ((a, a2), (b, b2)):
                e = unit(p0, p1)
                sh.line([(p0[0] + e[0] * 1.0, p0[1] + e[1] * 1.0), (p1[0] + e[0] * 1.8, p1[1] + e[1] * 1.8)], std.w_thin, layer="DIM")
            long_enough = math.dist(a2, b2) > 14
            if long_enough:
                sh.arrow(((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2), a2, 2.2, std.w_thin)
                sh.arrow(((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2), b2, 2.2, std.w_thin)
                sh.line([a2, b2], std.w_thin, layer="DIM")
            else:
                sh.line([(a2[0] - u[0] * 4, a2[1] - u[1] * 4), (b2[0] + u[0] * 4, b2[1] + u[1] * 4)], std.w_thin, layer="DIM")
                sh.arrow((a2[0] - u[0] * 4, a2[1] - u[1] * 4), a2, 2.0, std.w_thin)
                sh.arrow((b2[0] + u[0] * 4, b2[1] + u[1] * 4), b2, 2.0, std.w_thin)
            ang = math.degrees(math.atan2(b2[1] - a2[1], b2[0] - a2[0]))
            ang = ang - 180 if ang > 90 else (ang + 180 if ang < -90 else ang)
            nrm = (-math.sin(math.radians(ang)), math.cos(math.radians(ang)))
            sh.text(m[0] + nrm[0] * 0.9, m[1] + nrm[1] * 0.9, f"{L3:.0f}", std.dim_h, rot=ang, anchor="c", layer="DIM")

    # elevation call-outs at ends of vertical legs
    last_el = None
    for i, (a3, b3) in enumerate(zip(pts, pts[1:])):
        if _axis(a3, b3) == 2:
            for q3, qs in ((a3, S[i]), (b3, S[i + 1])):
                if last_el is not None and abs(last_el - q3[2]) < 1:
                    continue
                last_el = q3[2]
                tx, ty = qs[0] + 3.0, qs[1] - 1.0
                box = (tx, ty, tx + 22, ty + 3)
                if not occ.hit(box, 0.2):
                    sh.text(tx, ty, f"EL {q3[2]:+.0f}", std.dim_h, layer="DIM")
                    occ.add(box)

    # coordinates + connection captions at the ends
    if std.show_coordinates:
        for idx, (q3, qs, ref, other) in enumerate(((pts[0], S[0], from_ref, S[1]), (pts[-1], S[-1], to_ref, S[-2]))):
            u = unit(other, qs)                      # outward direction at the end
            for r in (8, 13, 18):
                tx, ty = qs[0] + u[0] * r, qs[1] + u[1] * r
                anc = "l" if u[0] >= -0.2 else "r"
                lines = [f"E {q3[0]:.0f}", f"N {q3[1]:.0f}", f"EL {q3[2]:.0f}"]
                wtxt = 9 * std.dim_h * 0.55
                wcap = 50.0
                box = (tx - (wcap if anc == "r" else 0), ty - 2 * 3.2, tx + (wcap if anc == "l" else 0), ty + 3.5)
                inside_ = box[0] > draw[0] and box[2] < draw[2] and box[1] > draw[1] and box[3] < draw[3]
                if (inside_ and not occ.hit(box, 0.3)) or r == 18:
                    if not inside_:                      # last resort: pull the caption inside the drawing area
                        tx = min(max(tx, draw[0] + (wcap if anc == "r" else 2)), draw[2] - (wcap if anc == "l" else 2))
                        ty = min(max(ty, draw[1] + 12), draw[3] - 6)
                    sh.text(tx, ty + 0.5, ("FROM " if idx == 0 else "TO ") + ref.replace(".", " / ")[-30:], std.dim_h * 0.9, anchor=anc, bold=True)
                    for k, t in enumerate(lines):
                        sh.text(tx, ty - 3.2 * (k + 1) + 0.5, t, std.dim_h * 0.9, anchor=anc)
                    occ.add((tx - (46 if anc == "r" else 0), ty - 11, tx + (46 if anc == "l" else 0), ty + 4))
                    break

    # item balloons
    if std.show_balloons:
        anchors = []
        longest = max(fab["pieces"], key=lambda p: p["length"]) if fab["pieces"] else None

        def item(kind, code_desc_lookup=None):
            return next((it for it in items if it["kind"] == kind), None)

        if longest:
            anchors.append((item("pipe"), P(spools.point_at(pts, fab["cum"], (longest["s0"] + longest["s1"]) / 2))))
        els = [c for c in fab["comps"] if c[0] == "elbow"]
        if els and item("elbow"):
            anchors.append((item("elbow"), S[els[len(els) // 2][4]["corner"]]))
        if ln.branch_of and item("branch"):
            anchors.append((item("branch"), S[-1]))
        for k, it_kind in enumerate(("flange", "gasket", "bolting")):
            if item(it_kind):
                anchors.append((item(it_kind), P(spools.point_at(pts, fab["cum"], 0.0 if k == 0 else 1e-3 + (k * 40)))))
        for vt, p3 in rt.valve_positions:
            it = next((it for it in items if it["kind"] == "valve" and vt.title() in it["description"]), item("valve"))
            anchors.append((it, P(p3)))
        have = set()
        for it, a in anchors + [(it, a) for it in items for a in [S[0]] if False]:
            if it is None:
                continue
            done = False
            for r in (10, 15, 20, 26, 34, 44):
                for ang in (60, 120, 30, 150, 90, -60, -120, 0, 180, -90, 45, 135, -45, -135):
                    bx, by = a[0] + r * math.cos(math.radians(ang)), a[1] + r * math.sin(math.radians(ang))
                    box = (bx - 3.4, by - 3.4, bx + 3.4, by + 3.4)
                    if box[0] > draw[0] and box[2] < draw[2] and box[1] > draw[1] and box[3] < draw[3] and not occ.hit(box, 0.4):
                        u = unit(a, (bx, by))
                        sh.line([a, (bx - u[0] * 3.2, by - u[1] * 3.2)], std.w_thin, layer="BALLOON")
                        sh.circle(bx, by, 3.2, std.w_thin, "white", layer="BALLOON")
                        sh.text(bx, by - 1.0, str(it["no"]), std.text_h * 0.95, anchor="c", bold=True, layer="BALLOON")
                        occ.add(box)
                        done = True
                        have.add(it["no"])
                        break
                if done:
                    break
        # fallback: an item without a balloon (crowded sheet) is placed anywhere free, then anywhere in the drawing area
        for it in items:
            if it["no"] in have:
                continue
            a = next((q for i2, q in anchors if i2 is not None and i2["no"] == it["no"]), S[len(S) // 2])
            spots = [(a[0] + r * math.cos(math.radians(g)), a[1] + r * math.sin(math.radians(g))) for r in (30, 40, 52, 64) for g in range(0, 360, 20)]
            inside = [(x, y) for x, y in spots if draw[0] + 4 < x < draw[2] - 4 and draw[1] + 4 < y < draw[3] - 4]
            free_ = [(x, y) for x, y in inside if not occ.hit((x - 3.4, y - 3.4, x + 3.4, y + 3.4), 0.4)]
            bx, by = (free_ or inside or [(a[0] + 12, a[1] + 12)])[0]
            u = unit(a, (bx, by))
            sh.line([a, (bx - u[0] * 3.2, by - u[1] * 3.2)], std.w_thin, layer="BALLOON")
            sh.circle(bx, by, 3.2, std.w_thin, "white", layer="BALLOON")
            sh.text(bx, by - 1.0, str(it["no"]), std.text_h * 0.95, anchor="c", bold=True, layer="BALLOON")
            occ.add((bx - 3.4, by - 3.4, bx + 3.4, by + 3.4))

    # branch: dashed header through the tap
    if ln.branch_of:
        hdr = next((l for l in prj.lines if l.id == ln.branch_of), None)
        t = S[-1]
        sh.circle(t[0], t[1], 2.4, std.w_thin, None, layer="SYMBOL")
        sh.text(t[0] + 3.5, t[1] - 5.5, f"TEE ON {ln.branch_of[-24:]}", std.dim_h * 0.9, layer="TEXT")

    # north arrow, title, drawing-area caption
    if std.show_north:
        nx_, ny_ = draw[0] + 16, draw[3] - 14
        u = AXV[1]
        sh.circle(nx_, ny_, 8, std.w_thin, None)
        sh.arrow((nx_ - u[0] * 7, ny_ - u[1] * 7), (nx_ + u[0] * 7, ny_ + u[1] * 7), 4.0, std.w_thin)
        sh.text(nx_ + u[0] * 9.5 - 1.2, ny_ + u[1] * 9.5 + 0.8, "N", std.text_h * 1.2, bold=True)
        for k, lab in ((0, "E"), (2, "UP")):
            v = AXV[k]
            sh.line([(nx_, ny_), (nx_ + v[0] * 6, ny_ + v[1] * 6)], std.w_thin)
            sh.text(nx_ + v[0] * 8 - (0 if k else 0), ny_ + v[1] * 8, lab, std.text_h * 0.8)
    sh.text(draw[0] + 36, draw[3] - 6, f"LINE {ln.id}", std.title_h, bold=True)
    sh.text(draw[0] + 36, draw[3] - 11.5, f'{ln.nps:g}" {ln.spec}  {ln.service}'.strip(), std.text_h)
    return sh


def spools_tag(fab, n):
    return next((s["tag"] for s in fab["spools"] if s["n"] == n), str(n))

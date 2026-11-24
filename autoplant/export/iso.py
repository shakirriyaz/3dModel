"""Piping isometrics (SVG per line + one PDF). Standard isometric projection, leg dimensions,
fitting symbols, line data block and per-line material list. Drawn from the routed model."""
from __future__ import annotations
import math, os
from ..model import Project, Route
from ..catalog import od, wall, elbow_takeoff

C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))


def _proj(p):
    x, y, z = p
    return ((x - y) * C30, (x + y) * S30 + z)    # up = +


def _layout(pts, w, h, margin=60, top=120):
    q = [_proj(p) for p in pts]
    xs, ys = [a for a, _ in q], [b for _, b in q]
    sx = (w - 2 * margin) / max(max(xs) - min(xs), 1)
    sy = (h - margin - top) / max(max(ys) - min(ys), 1)
    s = min(sx, sy)
    ox = margin + ((w - 2 * margin) - (max(xs) - min(xs)) * s) / 2
    return [(ox + (a - min(xs)) * s, h - margin - (b - min(ys)) * s) for a, b in q], s


def _legs(rt):
    return [(a, b, math.dist(a, b)) for a, b in zip(rt.points, rt.points[1:])]


def _svg(prj, ln, rt, stamp, W=1100, H=800):
    sp, s = _layout(rt.points, W, H)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Arial">',
         f'<rect width="{W}" height="{H}" fill="white" stroke="black"/>',
         f'<text x="20" y="32" font-size="20" font-weight="bold">ISOMETRIC {ln.id}</text>',
         f'<text x="20" y="56" font-size="13">{prj.name} | NPS {ln.nps} | Spec {ln.spec} | {ln.service} | '
         f'OD {od(ln.nps)} x {wall(ln.nps)} mm</text>',
         f'<text x="20" y="78" font-size="13" fill="{"#060" if "PASSED" in stamp else "#b00"}">{stamp}</text>',
         f'<text x="{W - 130}" y="40" font-size="12">N ↗ (+Y)  ISO view</text>']
    pl = " ".join(f"{x:.1f},{y:.1f}" for x, y in sp)
    o.append(f'<polyline points="{pl}" fill="none" stroke="black" stroke-width="3" stroke-linejoin="miter"/>')
    for i, (a, b, L) in enumerate(_legs(rt)):
        (x1, y1), (x2, y2) = sp[i], sp[i + 1]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        o.append(f'<text x="{mx + 6:.1f}" y="{my - 6:.1f}" font-size="11" fill="#036">{L:.0f}</text>')
    for i in range(1, len(sp) - 1):
        o.append(f'<circle cx="{sp[i][0]:.1f}" cy="{sp[i][1]:.1f}" r="4" fill="white" stroke="black"/>')
    for vt, p in rt.valve_positions:
        (vx, vy), = _layout([p] + rt.points, W, H)[0][:1]
        o.append(f'<path d="M{vx - 8:.1f},{vy - 8:.1f} L{vx + 8:.1f},{vy + 8:.1f} L{vx + 8:.1f},{vy - 8:.1f} '
                 f'L{vx - 8:.1f},{vy + 8:.1f} Z" fill="#fc0" stroke="black"/>'
                 f'<text x="{vx + 10:.1f}" y="{vy - 10:.1f}" font-size="10">{vt}</text>')
    o.append(f'<text x="{sp[0][0] - 4:.1f}" y="{sp[0][1] - 10:.1f}" font-size="11">{ln.from_}</text>')
    o.append(f'<text x="{sp[-1][0] - 4:.1f}" y="{sp[-1][1] + 18:.1f}" font-size="11">{ln.to}</text>')
    o.append("</svg>")
    return "\n".join(o)


def write_svgs(prj: Project, routes: dict[str, Route], outdir: str, stamp: str) -> list[str]:
    names = []
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is None or rt.status != "routed":
            continue
        n = f"{ln.id}.svg"
        with open(os.path.join(outdir, n), "w") as f:
            f.write(_svg(prj, ln, rt, stamp))
        names.append(n)
    return names


def write_pdf(prj: Project, routes: dict[str, Route], bom_rows: list[dict], path: str, stamp: str):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A3, landscape
    W, H = landscape(A3)
    c = canvas.Canvas(path, pagesize=(W, H))
    bom = {r["line"]: r for r in bom_rows}
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is None or rt.status != "routed":
            continue
        c.rect(20, 20, W - 40, H - 40)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(40, H - 55, f"ISOMETRIC {ln.id}")
        c.setFont("Helvetica", 11)
        c.drawString(40, H - 75, f"{prj.name} | NPS {ln.nps} | Spec {ln.spec} | {ln.service} | OD {od(ln.nps)} x {wall(ln.nps)} mm")
        c.setFillColorRGB(0, 0.4, 0) if "PASSED" in stamp else c.setFillColorRGB(0.7, 0, 0)
        c.drawString(40, H - 92, stamp)
        c.setFillColorRGB(0, 0, 0)
        sp, s = _layout(rt.points, W - 360, H - 80, margin=70, top=110)
        sp = [(x + 20, H - y) for x, y in sp]
        c.setLineWidth(2.5)
        p = c.beginPath()
        p.moveTo(*sp[0])
        for q in sp[1:]:
            p.lineTo(*q)
        c.drawPath(p, stroke=1, fill=0)
        c.setFont("Helvetica", 9)
        c.setLineWidth(0.5)
        for i, (a, b, L) in enumerate(_legs(rt)):
            c.drawString((sp[i][0] + sp[i + 1][0]) / 2 + 4, (sp[i][1] + sp[i + 1][1]) / 2 + 4, f"{L:.0f}")
        c.drawString(sp[0][0], sp[0][1] + 8, ln.from_)
        c.drawString(sp[-1][0], sp[-1][1] - 14, ln.to)
        # material list
        b = bom.get(ln.id, {})
        x0, y0 = W - 320, H - 120
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x0, y0, "MATERIAL LIST")
        c.setFont("Helvetica", 10)
        rows = [("Pipe cut length", f"{b.get('pipe_cut_length_m', '')} m"), ("90° LR elbows", b.get("elbows_90_lr", "")),
                ("WN flanges", b.get("flanges_wn", "")), ("Gaskets", b.get("gaskets", "")),
                ("Bolt sets", b.get("bolt_sets", "")), ("Valves", b.get("valves", "") or "-"),
                ("Weight (pipe)", f"{b.get('weight_kg', '')} kg")]
        for i, (k, v) in enumerate(rows):
            c.drawString(x0, y0 - 18 * (i + 1), f"{k}: {v}")
        c.showPage()
    c.save()

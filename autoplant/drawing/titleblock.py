"""Sheet frame, title block, revision table, notes and QC status stamp (all driven by the DrawingStandard)."""
from __future__ import annotations
from .canvas import Sheet
from .standard import DrawingStandard


def frame(sh: Sheet, std: DrawingStandard):
    """Outer + inner frame; returns the inner box (x0, y0, x1, y1)."""
    m, g = std.margin, std.frame_gap
    sh.rect(m, m, sh.w - 2 * m, sh.h - 2 * m, std.w_thin)
    x0, y0, x1, y1 = m + g, m + g, sh.w - m - g, sh.h - m - g
    sh.rect(x0, y0, x1 - x0, y1 - y0, std.w_frame)
    # zone marks on the outer frame (A..H across, 1..6 down), as on standard drawing sheets
    nx, ny = 8, 6
    for i in range(1, nx):
        x = x0 + (x1 - x0) * i / nx
        sh.line([(x, y1), (x, y1 + g)], std.w_thin)
        sh.line([(x, y0), (x, y0 - g)], std.w_thin)
    for j in range(1, ny):
        y = y0 + (y1 - y0) * j / ny
        sh.line([(x0, y), (x0 - g, y)], std.w_thin)
        sh.line([(x1, y), (x1 + g, y)], std.w_thin)
    for i in range(nx):
        xc = x0 + (x1 - x0) * (i + .5) / nx
        sh.text(xc, y1 + 1.2, "ABCDEFGH"[i], 2.0, anchor="c")
    for j in range(ny):
        yc = y0 + (y1 - y0) * (j + .5) / ny
        sh.text(x0 - g + 1.0, yc - 0.7, str(ny - j), 2.0, anchor="l")
    return x0, y0, x1, y1


def title_block(sh: Sheet, std: DrawingStandard, inner, d: dict, status_ok: bool, stamp: str, w=None, h=None):
    """Bottom-right title block. `d` keys: title, line, spec, service, project, doc_no, sheet, sheets, from, to, extra(list of (label,value)).
    Returns its box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = inner
    w = w or min(180.0, (x1 - x0) * 0.46)
    h = h or 56.0
    bx0, by0 = x1 - w, y0
    sh.rect(bx0, by0, w, h, std.w_frame)
    rows = [6.0, 6.0, 6.0, 6.0, 6.0, 6.0, 6.0, 6.0, 8.0]          # from the top down (sum 56)
    k = h / sum(rows)
    ys, y = [], by0 + h
    for r in rows:
        y -= r * k
        ys.append(y)
    # horizontal rules
    for y in ys[:-1]:
        sh.line([(bx0, y), (bx0 + w, y)], std.w_thin)
    th, lh = std.text_h, 1.7
    half = bx0 + w * 0.5

    def cell(x, ytop, label, value, bold=False, size=None):
        sh.text(x + 1.2, ytop - 2.0, label, lh, color="grey")
        sh.text(x + 1.2, ytop - 5.0 * k, str(value), size or th, bold=bold)

    top = by0 + h
    cell(bx0, top, "COMPANY", std.company, True, 3.2)
    cell(bx0, top - rows[0] * k, "PROJECT", f"{d.get('project','')}  {std.project_no}".strip())
    cell(bx0, top - sum(rows[:2]) * k, "TITLE", d["title"], True, std.title_h * 0.8)
    cell(bx0, top - sum(rows[:3]) * k, "LINE NO", d.get("line", ""), True)
    sh.line([(half, top - sum(rows[:3]) * k), (half, top - sum(rows[:4]) * k)], std.w_thin)
    cell(half, top - sum(rows[:3]) * k, "PIPING CLASS / SPEC", d.get("spec", ""))
    cell(bx0, top - sum(rows[:4]) * k, "SERVICE", d.get("service", "") or "-")
    sh.line([(half, top - sum(rows[:4]) * k), (half, top - sum(rows[:5]) * k)], std.w_thin)
    cell(half, top - sum(rows[:4]) * k, "SIZE / RATING", d.get("size", ""))
    cell(bx0, top - sum(rows[:5]) * k, "FROM", d.get("from", ""))
    sh.line([(half, top - sum(rows[:5]) * k), (half, top - sum(rows[:6]) * k)], std.w_thin)
    cell(half, top - sum(rows[:5]) * k, "TO", d.get("to", ""))
    # signatures + dates
    third = w / 4
    for i, (lab, val) in enumerate((("DRAWN", std.drawn_by), ("CHECKED", std.checked_by), ("APPROVED", std.approved_by), ("DATE", std.today()))):
        x = bx0 + i * third
        if i:
            sh.line([(x, top - sum(rows[:6]) * k), (x, top - sum(rows[:7]) * k)], std.w_thin)
        cell(x, top - sum(rows[:6]) * k, lab, val, size=th * 0.9)
    # revision + units + sheet
    sh.line([(bx0 + w * 0.12, top - sum(rows[:7]) * k), (bx0 + w * 0.12, by0)], std.w_thin)
    sh.line([(bx0 + w * 0.7, top - sum(rows[:7]) * k), (bx0 + w * 0.7, by0)], std.w_thin)
    cell(bx0, top - sum(rows[:7]) * k, "REV", std.revision, True)
    cell(bx0 + w * 0.12, top - sum(rows[:7]) * k, "REVISION NOTE / DOCUMENT NO", f"{std.revision_note}", size=th * 0.9)
    sh.text(bx0 + w * 0.12 + 1.2, by0 + 3.0 * k, d["doc_no"], th, bold=True)
    cell(bx0 + w * 0.7, top - sum(rows[:7]) * k, "SHEET", f"{d['sheet']} OF {d['sheets']}", True)
    sh.text(bx0 + w * 0.7 + 1.2, by0 + 3.0 * k, f"UNITS: {std.units}   N.T.S.", th * 0.9)
    # status stamp (above the block)
    sh.rect(bx0, by0 + h + 1.5, w, 7.0, std.w_frame, color="green" if status_ok else "red")
    sh.text(bx0 + 2.0, by0 + h + 3.6, stamp, 3.0, bold=True, color="green" if status_ok else "red")
    return bx0, by0, bx0 + w, by0 + h + 8.5


def notes_block(sh: Sheet, std: DrawingStandard, x, y_top, width, extra: list[str] | None = None):
    """Numbered general notes; returns the y below the block."""
    sh.text(x, y_top - 3.0, "NOTES", std.text_h, bold=True)
    y = y_top - 3.0 - 4.2
    for i, n in enumerate((std.notes or []) + (extra or []), 1):
        words, line = n.split(), ""
        first = True
        for w in words + [None]:
            if w is not None and len(line) + len(w) + 1 <= max(int(width / (std.text_h * 0.55)), 20):
                line = (line + " " + w).strip()
                continue
            sh.text(x, y, (f"{i}. " if first else "    ") + line, std.text_h * 0.85)
            y -= 3.6
            first, line = False, (w or "")
    return y


def table(sh: Sheet, std: DrawingStandard, x, y_top, headers, widths, rows, title=None, row_h=4.8, max_rows=None):
    """Simple ruled table. Returns (y_bottom, rows_drawn)."""
    y = y_top
    if title:
        sh.text(x, y - 3.2, title, std.text_h, bold=True)
        y -= 5.0
    total = sum(widths)
    sh.rect(x, y - row_h, total, row_h, std.w_thin, fill=None, layer="TABLE")
    cx = x
    for hd, w in zip(headers, widths):
        sh.text(cx + 1.0, y - row_h + 1.4, hd, std.text_h * 0.78, bold=True, layer="TABLE")
        cx += w
    y -= row_h
    n = 0
    for r in rows:
        if max_rows is not None and n >= max_rows:
            sh.text(x + 1.0, y - row_h + 1.4, f"... {len(rows) - n} more rows - see mto.csv", std.text_h * 0.75, color="grey", layer="TABLE")
            y -= row_h
            break
        sh.rect(x, y - row_h, total, row_h, std.w_thin, layer="TABLE")
        cx = x
        for v, w in zip(r, widths):
            s = str(v)
            cap = max(int((w - 1.5) / (std.text_h * 0.5)), 3)
            sh.text(cx + 1.0, y - row_h + 1.4, s if len(s) <= cap else s[:cap - 1] + "…", std.text_h * 0.78, layer="TABLE")
            cx += w
        y -= row_h
        n += 1
    cx = x
    for w in widths[:-1]:
        cx += w
        sh.line([(cx, y_top - (5.0 if title else 0)), (cx, y)], std.w_thin, layer="TABLE")
    return y, n

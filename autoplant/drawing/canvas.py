"""Sheet = a list of drawing primitives in paper millimetres (origin bottom-left, y up).
Renderers: SVG string, reportlab PDF canvas, ezdxf modelspace. One drawing description -> three formats."""
from __future__ import annotations
import html, math
from dataclasses import dataclass, field

PT = 72.0 / 25.4


@dataclass
class Sheet:
    w: float
    h: float
    ops: list = field(default_factory=list)

    # ---- primitives (layer is used by DXF; colour names: black, grey, red, green, blue)
    def line(self, pts, w=0.25, color="black", dash=False, layer="THIN"):
        self.ops.append(("line", [tuple(p) for p in pts], w, color, dash, layer))

    def text(self, x, y, s, h=2.5, rot=0.0, anchor="l", bold=False, color="black", layer="TEXT"):
        self.ops.append(("text", x, y, str(s), h, rot, anchor, bold, color, layer))

    def circle(self, x, y, r, w=0.25, fill=None, color="black", layer="SYMBOL"):
        self.ops.append(("circle", x, y, r, w, fill, color, layer))

    def poly(self, pts, w=0.25, fill=None, color="black", layer="SYMBOL"):
        self.ops.append(("poly", [tuple(p) for p in pts], w, fill, color, layer))

    def rect(self, x, y, w_, h_, w=0.25, fill=None, color="black", layer="FRAME"):
        self.poly([(x, y), (x + w_, y), (x + w_, y + h_), (x, y + h_)], w, fill, color, layer)

    # ---- helpers
    def arrow(self, a, b, size=2.5, w=0.25, color="black", layer="DIM"):
        """Filled arrow head at b pointing away from a."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1
        ux, uy = dx / L, dy / L
        px, py = -uy, ux
        self.poly([b, (b[0] - ux * size + px * size / 3, b[1] - uy * size + py * size / 3),
                   (b[0] - ux * size - px * size / 3, b[1] - uy * size - py * size / 3)], w, color, color, layer)

    # ---- renderers
    COL = {"black": "#000000", "grey": "#777777", "red": "#c62828", "green": "#1b7f3b", "blue": "#1f4fd8", "white": "#ffffff"}
    RGB = {"black": (0, 0, 0), "grey": (.47, .47, .47), "red": (.78, .16, .16), "green": (.11, .5, .23), "blue": (.12, .31, .85), "white": (1, 1, 1)}
    DXF_COLOR = {"black": 7, "grey": 8, "red": 1, "green": 3, "blue": 5, "white": 7}

    def to_svg(self) -> str:
        S = 3.7795  # px per mm
        o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w * S:.0f}" height="{self.h * S:.0f}" viewBox="0 0 {self.w:.2f} {self.h:.2f}" font-family="Helvetica,Arial,sans-serif">',
             f'<rect width="{self.w}" height="{self.h}" fill="white"/>']
        fy = lambda y: self.h - y
        for op in self.ops:
            k = op[0]
            if k == "line":
                _, pts, w, c, dash, _l = op
                d = ' stroke-dasharray="2 1.2"' if dash else ""
                o.append(f'<polyline points="{" ".join(f"{x:.2f},{fy(y):.2f}" for x, y in pts)}" fill="none" stroke="{self.COL[c]}" stroke-width="{w}"{d} stroke-linejoin="miter"/>')
            elif k == "text":
                _, x, y, s, h, rot, anc, bold, c, _l = op
                a = {"l": "start", "c": "middle", "r": "end"}[anc]
                tr = f' transform="rotate({-rot:.2f} {x:.2f} {fy(y):.2f})"' if rot else ""
                o.append(f'<text x="{x:.2f}" y="{fy(y):.2f}" font-size="{h}" text-anchor="{a}" fill="{self.COL[c]}"'
                         f'{" font-weight=\"bold\"" if bold else ""}{tr}>{html.escape(s)}</text>')
            elif k == "circle":
                _, x, y, r, w, fill, c, _l = op
                f = self.COL[fill] if fill else "none"
                o.append(f'<circle cx="{x:.2f}" cy="{fy(y):.2f}" r="{r:.2f}" fill="{f}" stroke="{self.COL[c]}" stroke-width="{w}"/>')
            elif k == "poly":
                _, pts, w, fill, c, _l = op
                f = self.COL[fill] if fill else "none"
                o.append(f'<polygon points="{" ".join(f"{x:.2f},{fy(y):.2f}" for x, y in pts)}" fill="{f}" stroke="{self.COL[c]}" stroke-width="{w}"/>')
        o.append("</svg>")
        return "\n".join(o)

    def to_pdf(self, c, font="Helvetica"):
        """Draw onto a reportlab canvas whose page is this sheet's size (then caller does showPage)."""
        for op in self.ops:
            k = op[0]
            if k == "line":
                _, pts, w, col, dash, _l = op
                c.setStrokeColorRGB(*self.RGB[col]); c.setLineWidth(w * PT); c.setDash(*( [2 * PT, 1.2 * PT] if dash else [] ))
                p = c.beginPath(); p.moveTo(pts[0][0] * PT, pts[0][1] * PT)
                for x, y in pts[1:]:
                    p.lineTo(x * PT, y * PT)
                c.drawPath(p, stroke=1, fill=0)
                c.setDash([])
            elif k == "text":
                _, x, y, s, h, rot, anc, bold, col, _l = op
                c.saveState(); c.setFillColorRGB(*self.RGB[col]); c.setFont(font + ("-Bold" if bold else ""), h * PT)
                c.translate(x * PT, y * PT); c.rotate(rot)
                {"l": c.drawString, "c": c.drawCentredString, "r": c.drawRightString}[anc](0, 0, s)
                c.restoreState()
            elif k == "circle":
                _, x, y, r, w, fill, col, _l = op
                c.setStrokeColorRGB(*self.RGB[col]); c.setLineWidth(w * PT)
                if fill:
                    c.setFillColorRGB(*self.RGB[fill])
                c.circle(x * PT, y * PT, r * PT, stroke=1, fill=1 if fill else 0)
            elif k == "poly":
                _, pts, w, fill, col, _l = op
                c.setStrokeColorRGB(*self.RGB[col]); c.setLineWidth(w * PT)
                if fill:
                    c.setFillColorRGB(*self.RGB[fill])
                p = c.beginPath(); p.moveTo(pts[0][0] * PT, pts[0][1] * PT)
                for x, y in pts[1:]:
                    p.lineTo(x * PT, y * PT)
                p.close(); c.drawPath(p, stroke=1, fill=1 if fill else 0)

    def to_dxf(self, msp, offset=(0.0, 0.0)):
        """Add to an ezdxf modelspace (units = paper mm)."""
        import ezdxf
        ox, oy = offset
        doc = msp.doc
        for name, col in (("FRAME", 7), ("THIN", 7), ("PIPE", 7), ("DIM", 3), ("TEXT", 2), ("SYMBOL", 7), ("WELD", 1), ("BALLOON", 4), ("TABLE", 8)):
            if name not in doc.layers:
                doc.layers.add(name, color=col)
        for op in self.ops:
            k = op[0]
            if k == "line":
                _, pts, w, col, dash, layer = op
                msp.add_lwpolyline([(x + ox, y + oy) for x, y in pts], dxfattribs={"layer": layer if layer in doc.layers else "THIN", "lineweight": int(w * 100), "color": self.DXF_COLOR[col]})
            elif k == "text":
                _, x, y, s, h, rot, anc, bold, col, layer = op
                t = msp.add_text(s, dxfattribs={"layer": layer if layer in doc.layers else "TEXT", "height": h, "rotation": rot, "color": self.DXF_COLOR[col]})
                al = {"l": ezdxf.enums.TextEntityAlignment.LEFT, "c": ezdxf.enums.TextEntityAlignment.CENTER, "r": ezdxf.enums.TextEntityAlignment.RIGHT}[anc]
                t.set_placement((x + ox, y + oy), align=al)
            elif k == "circle":
                _, x, y, r, w, fill, col, layer = op
                msp.add_circle((x + ox, y + oy), r, dxfattribs={"layer": layer if layer in doc.layers else "SYMBOL", "color": self.DXF_COLOR[col]})
                if fill:
                    h = msp.add_hatch(color=self.DXF_COLOR[fill]); h.paths.add_polyline_path([(x + ox + r * math.cos(a / 8 * 6.2832), y + oy + r * math.sin(a / 8 * 6.2832)) for a in range(8)], is_closed=True)
            elif k == "poly":
                _, pts, w, fill, col, layer = op
                msp.add_lwpolyline([(x + ox, y + oy) for x, y in pts], close=True, dxfattribs={"layer": layer if layer in doc.layers else "SYMBOL", "color": self.DXF_COLOR[col]})
                if fill and len(pts) >= 3:
                    h = msp.add_hatch(color=self.DXF_COLOR[fill]); h.paths.add_polyline_path([(x + ox, y + oy) for x, y in pts], is_closed=True)

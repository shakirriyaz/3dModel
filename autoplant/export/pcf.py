"""PCF (Piping Component File) export - one file per line, with SUPPORT records.

PCF is the long-established interchange format read by isometric generators (Isogen) and by stress tools (CAESAR II, AutoPIPE,
CAEPIPE import PCF). This writer follows the public descriptions of the format: header units, PIPELINE-REFERENCE, components with
END-POINT / CENTRE-POINT / BRANCH1-POINT, and SUPPORT with CO-ORDS. Always test the file with YOUR receiving tool and mapping
(skeys, support names and attribute mapping differ between installations)."""
from __future__ import annotations
import math, os
from ..model import Project, Route
from ..drawing import spools

HEADER = ["ISOGEN-FILES ISOGEN.FLS", "UNITS-BORE MM", "UNITS-CO-ORDS MM", "UNITS-WEIGHT KGS", "UNITS-BOLT-DIA MM", "UNITS-BOLT-LENGTH MM"]


def _p(p):
    return f"{p[0]:.1f} {p[1]:.1f} {p[2]:.1f}"


def line_pcf(prj: Project, ln, rt: Route) -> str:
    std = prj.drawing
    fab = spools.breakdown(prj, ln, rt, std)
    dm = prj.dims(ln)
    spec = prj.spec_view(ln.spec).spec
    bore = round(ln.nps * 25.4, 1)
    pts, cum = rt.points, fab["cum"]
    at = lambda s: spools.point_at(pts, cum, s)
    out = list(HEADER)
    out += [f"PIPELINE-REFERENCE {ln.id}", f"    PIPING-SPEC {ln.spec}", f"    MATERIAL-LIST {spec.material if spec else 'GENERIC'}",
            f"    FLUID {ln.service or 'UNSPECIFIED'}", f"    FROM {ln.from_}", f"    TO {'BOUNDARY' if ln.is_stub else ln.to}"]
    comps = []                                                  # (s_start, text)

    def block(s0, text):
        comps.append((s0, text))
    for p in fab["pieces"]:
        block(p["s0"], ["PIPE", f"    END-POINT {_p(p['p0'])} {bore}", f"    END-POINT {_p(p['p1'])} {bore}",
                        f"    ITEM-DESCRIPTION Pipe {ln.nps:g}in", f"    FABRICATION-ITEM"])
    for kind, sc, s0, s1, extra in fab["comps"]:
        if kind == "elbow":
            block(s0, ["ELBOW", f"    END-POINT {_p(at(s0))} {bore}", f"    END-POINT {_p(at(s1))} {bore}",
                       f"    CENTRE-POINT {_p(pts[extra['corner']])}", "    SKEY ELBW", f"    ITEM-DESCRIPTION 90 deg elbow {ln.nps:g}in"])
        elif kind == "valve":
            block(s0, ["VALVE", f"    END-POINT {_p(at(s0))} {bore}", f"    END-POINT {_p(at(s1))} {bore}",
                       f"    SKEY VV{extra['type'][:2].upper()}", f"    ITEM-DESCRIPTION {extra['type'].title()} valve {ln.nps:g}in"])
        elif kind == "flange":
            s_ = 0.0 if extra.get("end") == "start" else fab["length"]
            d = spools.point_at(pts, cum, min(s_ + 150, fab["length"])) if s_ == 0.0 else spools.point_at(pts, cum, max(s_ - 150, 0))
            block(s_, ["FLANGE", f"    END-POINT {_p(at(s_))} {bore}", f"    END-POINT {_p(d)} {bore}", "    SKEY FLWN", f"    ITEM-DESCRIPTION Weld neck flange {ln.nps:g}in"])
        elif kind == "tee":
            hdr = next((r for r in prj.lines if r.id == ln.branch_of), None)
            block(fab["length"], ["TEE", f"    CENTRE-POINT {_p(pts[-1])}", f"    BRANCH1-POINT {_p(pts[-1])} {bore}",
                                  f"    ITEM-DESCRIPTION Branch connection on {ln.branch_of}"])
    for sp in sorted((s for s in prj.supports if s.line == ln.id), key=lambda s: s.s):
        block(sp.s, ["SUPPORT", f"    CO-ORDS {_p(sp.pos)} 0", f"    ITEM-CODE {sp.code}", f"    ITEM-DESCRIPTION {sp.description or sp.kind}",
                     f"    COMPONENT-ATTRIBUTE1 {sp.kind.upper()}", f"    COMPONENT-ATTRIBUTE2 {sp.load_op_n:.0f}", f"    COMPONENT-ATTRIBUTE3 {sp.load_hydro_n:.0f}",
                     f"    COMPONENT-ATTRIBUTE4 {sp.mounting}"])
    for _, text in sorted(comps, key=lambda c: c[0]):
        out += text
    return "\n".join(out) + "\n"


def write_pcfs(prj: Project, routes: dict[str, Route], outdir: str) -> list[str]:
    names = []
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is None or rt.status != "routed":
            continue
        n = "".join(c if c.isalnum() or c in "-_" else "_" for c in ln.id) + ".pcf"
        with open(os.path.join(outdir, n), "w") as f:
            f.write(line_pcf(prj, ln, rt))
        names.append(n)
    return names

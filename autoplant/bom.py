"""Bill of materials per line + project material take-off (MTO), with components from the piping spec."""
from __future__ import annotations
import csv, io, math
from .model import Project, Route


def _line_components(prj: Project, ln, rt: Route, kids: list) -> list[dict]:
    """Every purchasable item on one line: pipe, elbows, tees/olets, flanges, gaskets, bolting, valves."""
    view, dm = prj.spec_view(ln.spec), prj.dims(ln)
    spec = view.spec
    nps = ln.nps
    pts = rt.points
    legs = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    bends = len(legs) - 1
    tk = dm.takeoff
    valve_len = sum(dm.ftf(v) for v, _ in rt.valve_positions)
    cut = max(sum(legs) - 2 * tk * bends - valve_len, 0.0)
    out = []

    def add(kind, code, desc, qty, unit, weight_each=None):
        out.append({"line": ln.id, "spec": ln.spec, "kind": kind, "nps": nps, "code": code, "description": desc,
                    "qty": round(qty, 3), "unit": unit, "weight_kg": None if weight_each is None else round(weight_each * qty, 2)})

    prow = view.pipe_row(nps)
    sched = (prow.schedule if prow else "") or ("SCH 40 (generic)" if spec is None else "")
    mat = spec.material if spec else ""
    add("pipe", prow.code if prow else "", (prow.description if prow and prow.description else f'Pipe {nps:g}" {sched} {mat}'.strip()),
        cut / 1000, "m", dm.weight_per_m)
    if bends:
        e = next((f for f in (spec.fittings if spec else []) if f.kind == "elbow90" and f.nps == nps), None)
        et = spec.elbow_type if spec else "LR"
        add("elbow", e.code if e else "", (e.description if e and e.description else f'Elbow 90° {et} BW {nps:g}" {mat}'.strip()), bends, "ea",
            e.weight_kg if e else None)
    for k in kids:
        br = view.branch_row(ln.nps, k.nps)
        typ = br.type if br else view.default_branch_type(ln.nps, k.nps)
        add("branch", br.code if br else "", (br.description if br and br.description else f'{typ.replace("_", " ").title()} {ln.nps:g}"x{k.nps:g}" {mat}'.strip()), 1, "ea")
    ends = 1 if ln.branch_of else 2
    fl = view.flange_row(nps)
    rating = (fl.rating if fl and fl.rating else (spec.rating if spec else ""))
    add("flange", fl.code if fl else "", (fl.description if fl and fl.description else f'Flange {fl.type if fl else "WN"} {nps:g}" {rating} {mat}'.strip()),
        ends, "ea", fl.weight_kg if fl else None)
    add("gasket", fl.gasket_code if fl else "", (fl.gasket_description if fl and fl.gasket_description else f'Gasket {nps:g}" {rating}'.strip()), ends, "ea")
    add("bolting", "", f'Bolt set {fl.bolt_size if fl and fl.bolt_size else ""} x{fl.bolts if fl and fl.bolts else "?"} {nps:g}" {rating}'.replace("  ", " ").strip(),
        ends, "set")
    for vt, _ in rt.valve_positions:
        v = view.valve_row(vt, nps)
        add("valve", v.code if v else "", (v.description if v and v.description else f'{vt.title()} valve {nps:g}" {v.rating if v and v.rating else rating}'.strip()),
            1, "ea", v.weight_kg if v else None)
    return out


def components(prj: Project, routes: dict[str, Route]) -> list[dict]:
    kids: dict[str, list] = {}
    for l in prj.lines:
        if l.branch_of:
            kids.setdefault(l.branch_of, []).append(l)
    out = []
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is not None and rt.status == "routed":
            out += _line_components(prj, ln, rt, kids.get(ln.id, []))
    return out


def build_mto(prj: Project, routes: dict[str, Route]) -> list[dict]:
    """Aggregate identical items across the project (same code or, without a code, same description)."""
    agg: dict[tuple, dict] = {}
    for c in components(prj, routes):
        key = (c["kind"], c["code"] or c["description"], c["unit"])
        a = agg.setdefault(key, {"kind": c["kind"], "code": c["code"], "description": c["description"], "nps": c["nps"],
                                 "unit": c["unit"], "qty": 0.0, "weight_kg": 0.0, "lines": set()})
        a["qty"] += c["qty"]
        a["weight_kg"] += c["weight_kg"] or 0.0
        a["lines"].add(c["line"])
    rows = []
    for a in sorted(agg.values(), key=lambda a: (["pipe", "elbow", "branch", "flange", "gasket", "bolting", "valve"].index(a["kind"]), a["nps"], a["code"])):
        rows.append({"kind": a["kind"], "code": a["code"], "description": a["description"], "nps": a["nps"], "unit": a["unit"],
                     "qty": round(a["qty"], 2) if a["unit"] == "m" else int(round(a["qty"])), "weight_kg": round(a["weight_kg"], 1),
                     "lines": len(a["lines"])})
    return rows


def build_bom(prj: Project, routes: dict[str, Route]) -> list[dict]:
    comps = components(prj, routes)
    by_line: dict[str, list] = {}
    for c in comps:
        by_line.setdefault(c["line"], []).append(c)
    rows = []
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is None or rt.status != "routed":
            continue
        cs = by_line.get(ln.id, [])
        dm = prj.dims(ln)
        spec = prj.spec_view(ln.spec).spec
        count = lambda k: int(round(sum(c["qty"] for c in cs if c["kind"] == k)))
        legs = [math.dist(a, b) for a, b in zip(rt.points, rt.points[1:])]
        pipe = next(c for c in cs if c["kind"] == "pipe")
        rows.append({"line": ln.id, "service": ln.service, "spec": ln.spec, "nps": ln.nps,
                     "material": spec.material if spec else "", "rating": spec.rating if spec else "",
                     "od_mm": dm.od, "wall_mm": dm.wall,
                     "route_length_m": round(sum(legs) / 1000, 3), "pipe_cut_length_m": pipe["qty"],
                     "elbows_90_lr": count("elbow"), "tees": count("branch"), "flanges_wn": count("flange"),
                     "gaskets": count("gasket"), "bolt_sets": count("bolting"),
                     "valves": ";".join(v for v, _ in rt.valve_positions),
                     "weight_kg": round(sum(c["weight_kg"] or 0 for c in cs if c["kind"] in ("pipe",)), 1)})
    return rows


def _csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def bom_csv(rows: list[dict]) -> str:
    return _csv(rows)


def mto_csv(rows: list[dict]) -> str:
    return _csv(rows)

from __future__ import annotations
import math, csv, io
from .model import Project, Route
from .catalog import od, wall, elbow_takeoff, valve_ftf, weight_per_m


def build_bom(prj: Project, routes: dict[str, Route]) -> list[dict]:
    rows = []
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is None or rt.status != "routed":
            continue
        pts = rt.points
        legs = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        bends = len(legs) - 1
        tk = elbow_takeoff(ln.nps)
        cut = sum(legs) - 2 * tk * bends - sum(valve_ftf(ln.nps) for _ in rt.valve_positions)
        cut = max(cut, 0.0)
        rows.append({"line": ln.id, "service": ln.service, "spec": ln.spec, "nps": ln.nps,
                     "od_mm": od(ln.nps), "wall_mm": wall(ln.nps),
                     "route_length_m": round(sum(legs) / 1000, 3), "pipe_cut_length_m": round(cut / 1000, 3),
                     "elbows_90_lr": bends, "flanges_wn": 2, "gaskets": 2, "bolt_sets": 2,
                     "valves": ";".join(v for v, _ in rt.valve_positions),
                     "weight_kg": round(cut / 1000 * weight_per_m(ln.nps), 1)})
    return rows


def bom_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()

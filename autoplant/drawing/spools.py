"""Fabrication breakdown of one routed line: components along the route, shop/field welds, bolted flange
joints, cut pieces and spools (field welds are introduced where the line exceeds the standard's max spool length)."""
from __future__ import annotations
import math
from ..model import Project, Route
from .standard import DrawingStandard

FLANGE_HUB = 120.0           # flange-to-pipe weld sits this far outside the valve face-to-face (mm)
MIN_BREAK_PIECE = 600.0      # a field break needs this much straight pipe


def _cum(points):
    s = [0.0]
    for a, b in zip(points, points[1:]):
        s.append(s[-1] + math.dist(a, b))
    return s


def point_at(points, cum, s):
    s = max(0.0, min(s, cum[-1]))
    for i in range(len(points) - 1):
        if s <= cum[i + 1] + 1e-9:
            L = cum[i + 1] - cum[i]
            t = (s - cum[i]) / L if L else 0.0
            return tuple(points[i][k] + (points[i + 1][k] - points[i][k]) * t for k in range(3))
    return tuple(points[-1])


def breakdown(prj: Project, ln, rt: Route, std: DrawingStandard) -> dict:
    pts = rt.points
    cum = _cum(pts)
    L = cum[-1]
    dm = prj.dims(ln)
    tk = dm.takeoff
    nodes = []          # (s, kind, label) kind: weld | joint
    comps = []          # (kind, s_center, s0, s1, extra)
    # start / end connections
    nodes.append((0.0, "weld", "flange"))
    nodes.append((0.0, "joint", "nozzle flange"))
    comps.append(("flange", 0.0, 0.0, 0.0, {"end": "start"}))
    if ln.is_stub:
        nodes += [(L, "weld", "flange"), (L, "joint", "boundary flange")]
        comps.append(("flange", L, L, L, {"end": "end"}))
    elif ln.branch_of:
        nodes.append((L, "weld", "branch connection"))
        comps.append(("tee", L, L, L, {}))
    else:
        nodes += [(L, "weld", "flange"), (L, "joint", "nozzle flange")]
        comps.append(("flange", L, L, L, {"end": "end"}))
    # elbows
    for i in range(1, len(pts) - 1):
        nodes += [(cum[i] - tk, "weld", "elbow"), (cum[i] + tk, "weld", "elbow")]
        comps.append(("elbow", cum[i], cum[i] - tk, cum[i] + tk, {"corner": i}))
    # valves (flanged): positions are 3D; locate along the route
    for vt, p in rt.valve_positions:
        best = min(range(len(pts) - 1), key=lambda i: _dist_seg(p, pts[i], pts[i + 1]))
        sv = cum[best] + math.dist(pts[best], p)
        f = dm.ftf(vt)
        nodes += [(sv - f / 2 - FLANGE_HUB, "weld", "valve flange"), (sv + f / 2 + FLANGE_HUB, "weld", "valve flange"),
                  (sv - f / 2, "joint", "valve flange"), (sv + f / 2, "joint", "valve flange")]
        comps.append(("valve", sv, sv - f / 2 - FLANGE_HUB, sv + f / 2 + FLANGE_HUB, {"type": vt}))
    # field welds at spool breaks: greedy from the start, each spool as even as the limit allows, with the
    # break placed anywhere along straight pipe (clear of elbows/valves/fittings) - never inside a fitting.
    breaks = []
    if std.show_spools and L > std.spool_max_length:
        node_s = sorted({round(n[0], 1) for n in nodes} | {0.0, round(L, 1)})
        zones = [(c[2], c[3]) for c in comps if c[0] in ("valve", "elbow")]
        free = []                                                   # feasible break intervals (straight pipe, >= margins)
        for a_, b_ in zip(node_s, node_s[1:]):
            lo_, hi_ = a_ + 300.0, b_ - 300.0
            if hi_ - lo_ < 0 or b_ - a_ < MIN_BREAK_PIECE:
                continue
            if any(z0 - 1 < (a_ + b_) / 2 < z1 + 1 for z0, z1 in zones):
                continue
            free.append((lo_, hi_))
        cur = 0.0
        while L - cur > std.spool_max_length + 1e-6:
            n_left = math.ceil((L - cur) / std.spool_max_length)
            target = min(cur + std.spool_max_length, cur + (L - cur) / n_left)
            ok_range = (cur + 300.0, cur + std.spool_max_length)
            best = None
            for lo_, hi_ in free:
                lo2, hi2 = max(lo_, ok_range[0]), min(hi_, ok_range[1])
                if lo2 <= hi2:
                    p = min(max(target, lo2), hi2)
                    if best is None or abs(p - target) < abs(best - target):
                        best = p
            if best is None:                                        # no feasible location inside the limit: nearest beyond it
                beyond = [(max(lo_, cur + 300.0), hi_) for lo_, hi_ in free if hi_ > cur + std.spool_max_length and hi_ >= cur + 300.0]
                if not beyond:
                    break
                best = min(p for p, _ in beyond)
            breaks.append(best)
            cur = best
        for bk in breaks:
            nodes.append((bk, "field", "spool break"))
    breaks.sort()
    bounds = [0.0] + breaks + [L]
    # number welds along the route (shop W, field FW)
    welds, shop_n, field_n = [], 0, 0
    for s, kind, label in sorted(nodes, key=lambda n: (n[0], n[1] != "weld")):
        if kind == "joint":
            continue
        if kind == "field":
            field_n += 1
            tag = f"{std.field_weld_prefix}{field_n}"
        else:
            shop_n += 1
            tag = std.weld_fmt.format(n=shop_n)
        welds.append({"tag": tag, "s": s, "pos": point_at(pts, cum, s), "field": kind == "field", "joint": label,
                      "size": f'{ln.nps:g}"', "spool": 1 + sum(1 for b in breaks if s > b + 1e-6)})
    joints = [{"s": s, "pos": point_at(pts, cum, s), "label": lab} for s, kind, lab in nodes if kind == "joint"]
    # cut pieces: straight pipe between consecutive welds (components themselves are not pipe)
    solid = sorted([(c[2], c[3]) for c in comps if c[0] in ("elbow", "valve")])
    edges = sorted({0.0, L, *[n[0] for n in nodes if n[0] not in ()]})
    pieces, no = [], 0
    pos_s = sorted({round(n[0], 3) for n in nodes} | {0.0, L})
    for a, b in zip(pos_s, pos_s[1:]):
        mid = (a + b) / 2
        if any(s0 - 1e-6 <= mid <= s1 + 1e-6 for s0, s1 in solid) or b - a < 1.0:
            continue
        no += 1
        pieces.append({"no": no, "s0": a, "s1": b, "length": round(b - a, 0), "spool": 1 + sum(1 for bk in breaks if mid > bk), "p0": point_at(pts, cum, a), "p1": point_at(pts, cum, b)})
    spools = []
    for i in range(len(bounds) - 1):
        ps = [p for p in pieces if p["spool"] == i + 1]
        cut = sum(p["length"] for p in ps)
        spools.append({"n": i + 1, "tag": std.spool_fmt.format(n=i + 1), "s0": bounds[i], "s1": bounds[i + 1], "length": round(bounds[i + 1] - bounds[i]),
                       "cut_m": round(cut / 1000, 2), "weight_kg": round(cut / 1000 * dm.weight_per_m, 1),
                       "welds": sum(1 for w in welds if w["spool"] == i + 1), "pos": point_at(pts, cum, (bounds[i] + bounds[i + 1]) / 2),
                       "over_limit": (bounds[i + 1] - bounds[i]) > std.spool_max_length + 1.0})
    return {"cum": cum, "length": L, "welds": welds, "joints": joints, "pieces": pieces, "spools": spools, "comps": comps, "breaks": breaks}


def _dist_seg(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    L2 = sum(c * c for c in ab) or 1.0
    t = max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / L2))
    q = [a[i] + ab[i] * t for i in range(3)]
    return math.dist(p, q)

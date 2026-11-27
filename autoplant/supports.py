"""Automatic pipe supports: span engine, rule-based placement, preliminary load take-down, catalogue selection, QC.

Design intent (see README): an automatic FIRST PASS that is span-checked and load-scheduled so that engineers review
and refine instead of starting from nothing. It is not a stress analysis: guide/anchor loads are friction-based
estimates, thermal loads are not computed, and every output says so.

Allowable span = min( beam formula with safety factor , published water-filled steel-pipe spacing ) unless the
imported piping class gives its own `max_span` (company table), which wins.
"""
from __future__ import annotations
import math
from typing import Optional
from .model import Project, Route, Support, QCItem
from .standards import Profile
from . import rack as rackmod
from .drawing.spools import _cum, point_at

G = 9.81
# Commonly published maximum spacing for standard-weight steel pipe, water-filled, no insulation (ft), ASME B31.1 / MSS style.
# 4" (14 ft) and 12" (23 ft) are confirmed by public sources cited in the README; the rest are the usual table values - verify.
REF_SPAN_FT = {0.5: 7, 0.75: 7, 1: 7, 1.5: 9, 2: 10, 2.5: 11, 3: 12, 4: 14, 6: 17, 8: 19, 10: 22, 12: 23, 14: 25, 16: 27, 18: 28, 20: 30, 24: 32}
HUB = 120.0
# generic placeholder support catalogue (used when the imported class has no `supports` table)
GENERIC = {"rest": (("SUP-REST", "Pipe shoe / rest"), ((4, 5.0), (8, 15.0), (12, 30.0), (24, 60.0))),
           "guide": (("SUP-GUIDE", "Pipe guide"), ((4, 5.0), (8, 15.0), (12, 30.0), (24, 60.0))),
           "anchor": (("SUP-ANCH", "Pipe anchor"), ((4, 10.0), (8, 30.0), (12, 60.0), (24, 120.0))),
           "riser_guide": (("SUP-RGD", "Riser guide / clamp"), ((4, 5.0), (8, 15.0), (12, 30.0), (24, 60.0)))}


def ref_span(nps: float) -> float:
    ks = sorted(REF_SPAN_FT)
    if nps <= ks[0]:
        return REF_SPAN_FT[ks[0]] * 304.8
    if nps >= ks[-1]:
        return REF_SPAN_FT[ks[-1]] * 304.8
    lo = max(k for k in ks if k <= nps)
    hi = min(k for k in ks if k >= nps)
    if lo == hi:
        return REF_SPAN_FT[lo] * 304.8
    t = (nps - lo) / (hi - lo)
    return (REF_SPAN_FT[lo] + t * (REF_SPAN_FT[hi] - REF_SPAN_FT[lo])) * 304.8


def density_for(service: str, std: Profile) -> float:
    s = (service or "").lower()
    for k, v in std.content_density.items():
        if k in s:
            return v
    return std.default_content_density


def weights(prj: Project, ln, std: Profile) -> dict:
    """Mass per metre (kg/m) of pipe, operating content, water (hydrotest) and insulation."""
    dm = prj.dims(ln)
    d, t = dm.od, dm.wall
    di = d - 2 * t
    ai = math.pi / 4 * di * di * 1e-6                       # m^2
    ins = 0.0
    if std.insulation_thickness_mm > 0:
        ins = math.pi / 4 * ((d + 2 * std.insulation_thickness_mm) ** 2 - d * d) * 1e-6 * std.insulation_density
    return {"pipe": dm.weight_per_m, "op": ai * density_for(ln.service, std), "hydro": ai * 1000.0, "ins": ins}


def allowable_span(prj: Project, ln, std: Profile) -> dict:
    dm = prj.dims(ln)
    d, t = dm.od, dm.wall
    di = d - 2 * t
    I = math.pi / 64 * (d ** 4 - di ** 4)
    Z = I / (d / 2)
    w = weights(prj, ln, std)
    kg = w["pipe"] + w["ins"] + (max(w["op"], w["hydro"]) if std.hydrotest_governs else w["op"])
    wn = kg * G / 1000.0                                    # N/mm
    L_stress = math.sqrt(10 * std.span_stress_mpa * Z / wn)  # continuous beam, M = wL^2/10
    L_defl = (384 * std.youngs_modulus_mpa * I * std.span_deflection_mm / (5 * wn)) ** 0.25
    calc = min(L_stress, L_defl) * std.span_safety
    view = prj.spec_view(ln.spec)
    override = view.max_span_override(ln.nps)
    if override:
        return {"span": override, "basis": "company span table", "calc": calc, "stress": L_stress, "deflection": L_defl, "kg_m": kg}
    span, basis = calc, "deflection" if L_defl <= L_stress else "stress"
    if std.span_use_reference_table and ref_span(ln.nps) < span:
        span, basis = ref_span(ln.nps), "published spacing table"
    return {"span": span, "basis": basis, "calc": calc, "stress": L_stress, "deflection": L_defl, "kg_m": kg}


def valve_weight(prj: Project, ln, vt: str) -> float:
    r = prj.spec_view(ln.spec).valve_row(vt, ln.nps)
    if r is not None and r.weight_kg:
        return r.weight_kg
    return 2.2 * ln.nps ** 1.9                               # rough flanged-valve estimate (kg)


# ------------------------------------------------------------------ geometry along a route
class Run:
    def __init__(self, prj, ln, rt, std):
        self.prj, self.ln, self.rt, self.std = prj, ln, rt, std
        self.pts = rt.points
        self.cum = _cum(self.pts)
        self.L = self.cum[-1]
        self.dm = prj.dims(ln)
        self.tk = self.dm.takeoff
        self.zones = []                                        # (s0, s1, kind) where no support can stand
        for i in range(1, len(self.pts) - 1):
            self.zones.append((self.cum[i] - self.tk, self.cum[i] + self.tk, "elbow", i))
        for vt, p in rt.valve_positions:
            sv = self._s_of(p)
            f = self.dm.ftf(vt)
            self.zones.append((sv - f / 2 - HUB, sv + f / 2 + HUB, "valve", vt))
        self.taps = []
        for other in prj.lines:
            if other.branch_of == ln.id:
                ro = other.id
                self.taps.append(ro)
        self.minrun = 150.0

    def _s_of(self, p):
        i = min(range(len(self.pts) - 1), key=lambda k: _dseg(p, self.pts[k], self.pts[k + 1]))
        return self.cum[i] + math.dist(self.pts[i], p)

    def leg_of(self, s):
        for i in range(len(self.pts) - 1):
            if s <= self.cum[i + 1] + 1e-6:
                return i
        return len(self.pts) - 2

    def eff(self, a, b):
        """Effective span length between two positions: horizontal pipe counts fully, vertical pipe half
        (a vertical run does not sag; the weight is not carried across it). A documented simplification."""
        if b < a:
            a, b = b, a
        tot = 0.0
        for i in range(len(self.pts) - 1):
            lo, hi = max(a, self.cum[i]), min(b, self.cum[i + 1])
            if hi > lo:
                p, q = self.pts[i], self.pts[i + 1]
                vert = abs(q[2] - p[2]) > max(abs(q[0] - p[0]), abs(q[1] - p[1]))
                tot += (hi - lo) * (0.5 if vert else 1.0)
        return tot

    def vertical(self, s):
        i = self.leg_of(s)
        a, b = self.pts[i], self.pts[i + 1]
        return abs(b[2] - a[2]) > max(abs(b[0] - a[0]), abs(b[1] - a[1]))

    def free(self, s, extra_zones=()):
        if s < self.minrun or s > self.L - self.minrun:
            return False
        return not any(z0 - self.minrun < s < z1 + self.minrun for z0, z1, *_ in list(self.zones) + list(extra_zones))

    def nearest_free(self, s, lo=None, hi=None, extra_zones=(), step=50.0):
        lo = self.minrun if lo is None else lo
        hi = self.L - self.minrun if hi is None else hi
        best = None
        for k in range(0, 400):
            for sgn in (1, -1):
                c = s + sgn * k * step
                if lo <= c <= hi and self.free(c, extra_zones):
                    return c
        return best


def _dseg(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    L2 = sum(c * c for c in ab) or 1.0
    t = max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / L2))
    return math.dist(p, [a[i] + ab[i] * t for i in range(3)])


def _beam_xs(prj: Project) -> list[float]:
    r = prj.rack
    if not r.active:
        return []
    nb = int(round((r.x1 - r.x0) / r.bay))
    return [r.x0 + k * r.bay for k in range(nb + 1)]


def _on_rack_leg(prj: Project, run: Run, s: float) -> Optional[float]:
    """If s lies on the long straight run along the rack (on a tier, inside the lane) return that tier's z."""
    if run.rt.note != "rack" or not prj.rack.active:
        return None
    i = run.leg_of(s)
    a, b = run.pts[i], run.pts[i + 1]
    if abs(a[1] - b[1]) > 1 or abs(a[2] - b[2]) > 1 or abs(a[0] - b[0]) < prj.rack.bay:
        return None
    if a[2] in rackmod.tier_z(prj) and abs(a[1] - prj.rack.y_center) <= prj.rack.width / 2:
        return a[2]
    return None


# ------------------------------------------------------------------ clash of a stanchion with things below the pipe
def _stanchion_clear(prj, ln, pos, height, sec, routes, equip_boxes) -> bool:
    x, y = pos[0], pos[1]
    lo = (x - sec / 2, y - sec / 2, 0.0)
    hi = (x + sec / 2, y + sec / 2, max(height, 1.0))
    for blo, bhi in equip_boxes:
        if all(lo[i] < bhi[i] and blo[i] < hi[i] for i in range(3)):
            return False
    for lid, rt in routes.items():
        if lid == ln.id or rt.status != "routed":
            continue
        r = prj.dims(next(l for l in prj.lines if l.id == lid)).r
        for a, b in zip(rt.points, rt.points[1:]):
            k = [0.0 if abs(b[i] - a[i]) > 1 and sum(abs(b[j] - a[j]) > 1 for j in range(3)) == 1 and i == [j for j in range(3) if abs(b[j] - a[j]) > 1][0] else r for i in range(3)]
            slo = [min(a[i], b[i]) - k[i] for i in range(3)]
            shi = [max(a[i], b[i]) + k[i] for i in range(3)]
            if all(lo[i] < shi[i] and slo[i] < hi[i] for i in range(3)):
                return False
    return True


# ------------------------------------------------------------------ generation
def generate(prj: Project, routes: dict[str, Route], std: Profile) -> list[Support]:
    """Regenerate automatic supports for every routed line; keep manual supports on lines flagged supports_manual."""
    if not std.supports_enabled:
        return [s for s in prj.supports if not s.auto]
    manual_by_line: dict[str, list[Support]] = {}
    for sp_ in prj.supports:
        if not sp_.auto:
            manual_by_line.setdefault(sp_.line, []).append(sp_)
    out: list[Support] = []
    equip = [e.box() for e in prj.equipment]
    beams = _beam_xs(prj)
    n = 0
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is None or rt.status != "routed":
            continue
        run = Run(prj, ln, rt, std)
        allow = allowable_span(prj, ln, std)["span"]
        taps = {}
        for other in prj.lines:
            if other.branch_of == ln.id and routes.get(other.id) is not None and routes[other.id].status == "routed":
                taps[other.id] = run._s_of(routes[other.id].points[-1])
        tap_zones = [(s - 300, s + 300, "tap", k) for k, s in taps.items()]

        def clear_at(s, kind_hint="rest"):
            """A candidate must not put a stanchion through equipment/other pipes."""
            pos = point_at(run.pts, run.cum, s)
            if _on_rack_leg(prj, run, s) is not None or run.vertical(s):
                return True
            h = pos[2] - run.dm.r - 100.0
            if h <= 150.0 or h > std.stanchion_max_height_mm:
                return True
            return _stanchion_clear(prj, ln, pos, h, std.stanchion_section_mm, routes, equip)

        def place(s, bias=0, vertical_ok=False, window=None):
            """Snap a wanted position to a legal one (free of fittings, on a rack beam when on the rack, clash-free).
            Horizontal pipe is preferred; bias=-1 looks toward the line start first, +1 toward the end."""
            tier = _on_rack_leg(prj, run, s)
            if tier is not None:
                i = run.leg_of(s)
                a, b = run.pts[i], run.pts[i + 1]
                cands = [bx for bx in beams if min(a[0], b[0]) + 300 < bx < max(a[0], b[0]) - 300]
                best = None
                for bx in sorted(cands, key=lambda bx: abs(run.cum[i] + abs(bx - a[0]) - s)):
                    cs = run.cum[i] + abs(bx - a[0])
                    if run.free(cs, tap_zones):
                        return cs
                return best
            order = (-1, 1) if bias <= 0 else (1, -1)
            if bias == 0:
                order = (1, -1)
            # pass 1: horizontal pipe + clash-free stanchion; pass 2: also vertical runs; pass 3: ignore the stanchion clash
            # (the support then needs a bracket/hanger from other steel and is flagged)
            passes = ((False, True), (True, True), (True, False)) if not vertical_ok else ((True, True), (True, False))
            # a candidate inside the required window beats any candidate outside it, whatever its quality
            for win in ((window, None) if window else (None,)):
                for allow_v, need_clear in passes:
                    # bias 0: nearest either side; bias +-1: exhaust the preferred side first
                    walk = ([(sg, k) for k in range(240) for sg in order] if bias == 0 else [(sg, k) for sg in order for k in range(240)])
                    for sgn, k in walk:
                        c = s + sgn * k * 50.0
                        if win is not None and not (win[0] <= c <= win[1]):
                            continue
                        if (0 < c < run.L and run.free(c, tap_zones) and (allow_v or not run.vertical(c))
                                and (not need_clear or clear_at(c)) and _on_rack_leg(prj, run, c) is None):
                            if not need_clear and not clear_at(c):
                                forced.add(round(c, 1))
                            return c
            return run.nearest_free(s, extra_zones=tap_zones)

        def best_ok(c):
            """Spot for gap-filling: horizontal, clash-free stanchion, not on a rack run."""
            return (not run.vertical(c)) and clear_at(c) and _on_rack_leg(prj, run, c) is None

        S: list[float] = []
        forced: set[float] = set()
        offbeam: set[float] = set()
        stool: set[float] = set()

        def add(s):
            if s is None:
                return
            if any(abs(s - x) < 300 for x in S):
                return
            S.append(s)

        # 1) nozzle ends (equipment nozzles must not carry the pipe)
        nd = min(std.nozzle_support_max_mm, 0.5 * allow)
        add(place(nd, bias=-1, window=(0.0, nd)))
        if not ln.is_stub and not ln.branch_of:
            add(place(run.L - nd, bias=1, window=(run.L - nd, run.L)))
        elif ln.branch_of:
            nb = min(nd, 0.5 * allow)
            add(place(run.L - nb, bias=1, window=(run.L - nb, run.L)))
        # 2) valves
        for z0, z1 in _valve_groups(run):
            if ln.nps < 3:
                continue
            for k, sd in enumerate((z0 - std.valve_support_max_mm * 0.6, z1 + std.valve_support_max_mm * 0.6)):
                if k == 1 and ln.nps < 10 and any(min(abs(x - z0), abs(x - z1)) <= std.valve_support_max_mm + 1 for x in S):
                    continue                         # one support per valve group is enough below NPS 10
                vm = std.valve_support_max_mm * 1.5
                add(place(sd, bias=-1 if k == 0 else 1, window=(z0 - vm, z0) if k == 0 else (z1, z1 + vm)))
            for vz0, vz1, *_ in [z for z in run.zones if z[2] == "valve" and z0 - 1 <= z[0] and z[1] <= z1 + 1]:
                if not any(min(abs(x - vz0), abs(x - vz1)) <= std.valve_support_check_mm for x in S):
                    mid = (vz0 + vz1) / 2                         # no free pipe near this valve: support under the valve body (stool)
                    if 1.0 < mid < run.L - 1.0:
                        S.append(mid)
                        stool.add(round(mid, 1))
        # 3) elbows
        for z0, z1, kind, info in run.zones:
            if kind != "elbow":
                continue
            lim = std.elbow_support_max_factor * allow
            if any(min(abs(x - z0), abs(x - z1)) <= lim for x in S):
                continue
            side_pref = [z0 - 250.0, z1 + 250.0]
            for sd in sorted(side_pref, key=lambda v: run.vertical(max(1.0, min(run.L - 1.0, v)))):
                p = place(sd, bias=-1 if sd < z0 else 1, window=(z0 - lim, z0) if sd < z0 else (z1, z1 + lim))
                if p is not None and min(abs(p - z0), abs(p - z1)) <= lim + 1:
                    add(p)
                    break
        # 4) tee taps: support the header near each branch
        for k, st in taps.items():
            if not any(abs(st - x) <= 0.5 * allow for x in S):
                add(place(st + 700.0)) or add(place(st - 700.0))
        # 5) vertical runs: riser guides
        for i in range(len(run.pts) - 1):
            if abs(run.pts[i + 1][2] - run.pts[i][2]) > max(abs(run.pts[i + 1][0] - run.pts[i][0]), abs(run.pts[i + 1][1] - run.pts[i][1])):
                a, b = run.cum[i], run.cum[i + 1]
                k = 1
                while a + k * std.riser_guide_spacing_mm < b - 600:
                    add(place(a + k * std.riser_guide_spacing_mm, vertical_ok=True))
                    k += 1
        # 6) fill gaps: even spacing where possible, never above the allowable span; on the rack the supports are the beams
        S.sort()
        stuck: set = set()
        for _ in range(400):
            bounds = [0.0] + S + [run.L]
            todo = [(lo, hi) for lo, hi in zip(bounds, bounds[1:]) if run.eff(lo, hi) > allow + 1 and lo > 0.0 and hi < run.L and (lo, hi) not in stuck]
            if not todo:
                break
            lo, hi = todo[0]
            parts = max(2, math.ceil(run.eff(lo, hi) / allow))
            target = lo + (hi - lo) / parts
            tier = _on_rack_leg(prj, run, (lo + hi) / 2)
            p = None
            if tier is not None:
                # farthest beam that keeps the span; no beam fits -> an off-beam support that needs an extra rack beam
                i = run.leg_of((lo + hi) / 2)
                a_, b_ = run.pts[i], run.pts[i + 1]
                hi_c = min(lo + allow, hi - 150)
                cands = sorted((run.cum[i] + abs(bx - a_[0]) for bx in beams if min(a_[0], b_[0]) < bx < max(a_[0], b_[0])), reverse=True)
                p = next((c for c in cands if lo + 300 <= c <= hi_c and run.free(c, tap_zones)), None)
                if p is None:
                    for k in range(0, 400):
                        c = hi_c - k * 50.0
                        if c < lo + 300:
                            break
                        if run.free(c, tap_zones):
                            p = c
                            offbeam.add(round(c, 1))
                            break
            else:                                                     # nearest legal spot to the even-split target, within the span
                best = None
                for k in range(0, 400):
                    for sgn in (1, -1):
                        c = target + sgn * k * 50.0
                        if lo + 300 <= c <= hi - 300 and run.eff(lo, c) <= allow and run.free(c, tap_zones) and (best_ok(c)):
                            best = c
                            break
                    if best is not None:
                        break
                if best is None:                                      # no horizontal spot: a guide on the vertical run
                    for k in range(0, 400):
                        for sgn in (1, -1):
                            c = target + sgn * k * 50.0
                            if lo + 300 <= c <= hi - 300 and run.eff(lo, c) <= allow and run.free(c, tap_zones) and run.vertical(c):
                                best = c
                                break
                        if best is not None:
                            break
                p = best if best is not None else place(target)
            if p is None or not (lo + 100 < p < hi - 100) or any(abs(p - x) < 300 for x in S):
                stuck.add((lo, hi))                                   # cannot be split (no legal spot): QC will report it
                continue
            S.append(p)
            S.sort()
        S = sorted(set(round(x, 1) for x in S))
        stool = {round(x, 1) for x in stool}
        if ln.supports_manual:                                   # hand-edited line: keep exactly the engineer's supports
            man = sorted((m for m in manual_by_line.get(ln.id, []) if 1.0 < m.s < run.L - 1.0), key=lambda m: m.s)
            S = [m.s for m in man]
            if not man:
                ln.supports_manual = False if not manual_by_line.get(ln.id) else ln.supports_manual
        if not S:
            continue
        # 7) types: rest everywhere; guides every N rest supports on horizontal runs; riser guides on verticals; one anchor on very long runs
        kinds = []
        horiz = 0
        for s in S:                                              # (auto rules; replaced below for manual lines)
            if run.vertical(s):
                kinds.append("riser_guide")
            else:
                horiz += 1
                kinds.append("guide" if std.guide_every_n_supports and horiz % std.guide_every_n_supports == 0 else "rest")
        long_runs = []
        i = 0
        while i < len(run.pts) - 1:
            j = i
            while j + 1 < len(run.pts) - 1 and _same_axis(run.pts[j], run.pts[j + 1], run.pts[j + 2]):
                j += 1
            if run.cum[j + 1] - run.cum[i] >= std.anchor_min_run_mm and not run.vertical(run.cum[i] + 1):
                long_runs.append((run.cum[i], run.cum[j + 1]))
            i = j + 1
        for a, b in long_runs:
            mid = (a + b) / 2
            cand = [k for k, s in enumerate(S) if a < s < b and kinds[k] in ("rest", "guide")]
            if cand:
                k = min(cand, key=lambda k: abs(S[k] - mid))
                kinds[k] = "anchor"
        if ln.supports_manual:
            kinds = [m.kind for m in man]
        # 8) mounting + stanchion height + loads
        w = weights(prj, ln, std)
        wo = (w["pipe"] + w["op"] + w["ins"]) * G / 1000.0           # N/mm
        wh = (w["pipe"] + w["hydro"] + w["ins"]) * G / 1000.0
        valves = [(run._s_of(p), valve_weight(prj, ln, vt) * G) for vt, p in rt.valve_positions]
        for idx, (s, kd) in enumerate(zip(S, kinds)):
            left = s - (S[idx - 1] if idx else 0.0)
            right = (S[idx + 1] if idx + 1 < len(S) else run.L) - s
            lo_trib = left if idx == 0 else left / 2
            hi_trib = right if idx + 1 == len(S) else right / 2
            trib = lo_trib + hi_trib
            vload = sum(vw for sv, vw in valves if abs(sv - s) == min(abs(sv - x) for x in S))
            pos = point_at(run.pts, run.cum, s)
            tier = _on_rack_leg(prj, run, s)
            if tier is not None:
                mounting, h = "rack_beam", max(run.dm.r * 0 + (400.0 - run.dm.r), 100.0)
            elif run.vertical(s):
                mounting, h = "needs_steel", 0.0
            else:
                h = pos[2] - run.dm.r - 100.0
                mounting = "sleeper" if h <= 150.0 else ("stanchion" if h <= std.stanchion_max_height_mm else "needs_steel")
                h = max(h, 0.0)
                if round(s, 1) in forced and mounting == "stanchion":
                    mounting = "needs_steel"
            if round(s, 1) in offbeam:
                mounting = "needs_steel"
            n += 1
            V_op, V_h = wo * trib + vload, wh * trib + vload
            lat = std.friction_coefficient * max(V_op, V_h) if kd in ("guide", "anchor", "riser_guide") else 0.0
            sup = Support(id=f"PS-{ln.id[-18:]}-{idx + 1:02d}", line=ln.id, s=s, pos=tuple(round(v, 1) for v in pos), kind=kd, mounting=mounting,
                          height=round(h, 1), load_op_n=round(V_op, 0), load_hydro_n=round(V_h, 0), lateral_n=round(lat, 0), auto=not ln.supports_manual)
            if round(s, 1) in stool:
                sup.note = "VALVE STOOL: no free pipe next to the valve(s) - support under the valve body"
            if round(s, 1) in offbeam:
                sup.note = f"ADD INTERMEDIATE RACK BEAM at x={pos[0]:.0f} (rack bay {prj.rack.bay:.0f} mm is too long for this line)"
            out.append(sup)
    _select_catalogue(prj, out)
    return out


def _valve_groups(run) -> list[tuple[float, float]]:
    """Valves closer than 1 m are supported as one group."""
    zs = sorted((z0, z1) for z0, z1, kind, _ in run.zones if kind == "valve")
    groups = []
    for z0, z1 in zs:
        if groups and z0 - groups[-1][1] < 1000.0:
            groups[-1] = (groups[-1][0], max(groups[-1][1], z1))
        else:
            groups.append((z0, z1))
    return groups


def _same_axis(a, b, c):
    def ax(p, q):
        d = [abs(q[i] - p[i]) for i in range(3)]
        return d.index(max(d))
    return ax(a, b) == ax(b, c)


def _select_catalogue(prj: Project, sups: list[Support]):
    """Pick the smallest adequate catalogue support. Placement notes (stool / extra beam) are kept; a load note is appended."""
    by = {l.id: l for l in prj.lines}
    for s in sups:
        ln = by.get(s.line)
        if ln is None:
            continue
        placement = s.note if s.note.startswith(("VALVE STOOL", "ADD INTERMEDIATE")) else ""
        load_note = ""
        load_kn = max(s.load_op_n, s.load_hydro_n) / 1000.0
        rows = prj.spec_view(ln.spec).support_rows(s.kind)
        rows = [r for r in rows if r.nps_min <= ln.nps <= r.nps_max]
        ok = sorted((r for r in rows if r.max_load_kn >= load_kn), key=lambda r: r.max_load_kn)
        if ok:
            r = ok[0]
            s.code, s.description, s.weight_kg = r.code, r.description or r.code, r.weight_kg or 0.0
        elif rows:
            r = max(rows, key=lambda r: r.max_load_kn)
            s.code, s.description = r.code, r.description or r.code
            load_note = f"LOAD {load_kn:.1f} kN EXCEEDS RATING {r.max_load_kn:g} kN"
        else:
            (code, desc), table = GENERIC[s.kind] if s.kind in GENERIC else GENERIC["rest"]
            cap = next((c for lim, c in table if ln.nps <= lim), table[-1][1])
            s.code, s.description = f"{code}-{ln.nps:g}", f"{desc} NPS {ln.nps:g} (GENERIC PLACEHOLDER, {cap:g} kN)"
            if load_kn > cap:
                load_note = f"GENERIC LOAD {load_kn:.1f} kN EXCEEDS PLACEHOLDER RATING {cap:g} kN"
        s.note = " | ".join(x for x in (placement, load_note) if x)


# ------------------------------------------------------------------ QC (independent re-check of the stored supports)
def check(prj: Project, routes: dict[str, Route], std: Profile) -> list[QCItem]:
    items: list[QCItem] = []
    if not std.supports_enabled:
        return items

    def add(check_, sev, subj, msg, at=None):
        items.append(QCItem(check=check_, severity=sev, subject=subj, message=msg,
                            at=None if at is None else tuple(round(v, 1) for v in at), key=f"{check_}|{subj}|{msg}"))
    by_line: dict[str, list[Support]] = {}
    for s in prj.supports:
        by_line.setdefault(s.line, []).append(s)
    equip = [e.box() for e in prj.equipment]
    beams = _beam_xs(prj)
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt is None or rt.status != "routed":
            continue
        run = Run(prj, ln, rt, std)
        info = allowable_span(prj, ln, std)
        allow = info["span"]
        sups = sorted(by_line.get(ln.id, []), key=lambda s: s.s)
        if not sups:
            if run.L > 0.5 * allow:
                add("support_missing", "error", ln.id, f"line is {run.L / 1000:.1f} m long and has no supports (allowable span {allow / 1000:.1f} m)")
            continue
        S = [s.s for s in sups]
        # end overhang (equipment nozzles must not carry the pipe) and spans between supports
        if S[0] > 0.5 * allow + 1:
            add("support_span", "error", ln.id, f"first support is {S[0] / 1000:.1f} m from the start nozzle (limit {0.5 * allow / 1000:.1f} m)", point_at(run.pts, run.cum, S[0] / 2))
        if not ln.is_stub and run.L - S[-1] > 0.5 * allow + 1:
            add("support_span", "error", ln.id, f"last support is {(run.L - S[-1]) / 1000:.1f} m from the end (limit {0.5 * allow / 1000:.1f} m)", point_at(run.pts, run.cum, (S[-1] + run.L) / 2))
        for a, b in zip(S, S[1:]):
            if run.eff(a, b) > allow + 1:
                add("support_span", "error", ln.id, f"span {a / 1000:.1f}-{b / 1000:.1f} m = {run.eff(a, b) / 1000:.1f} m (effective) exceeds allowable {allow / 1000:.1f} m ({info['basis']})",
                    point_at(run.pts, run.cum, (a + b) / 2))
        # supports must not stand inside fittings
        for s in sups:
            if not run.free(s.s) and s.s > 1 and s.s < run.L - 1 and not s.note.startswith("VALVE STOOL"):
                add("support_position", "error", ln.id, f"support {s.id} is at s={s.s:.0f} mm inside a fitting (elbow/valve)", s.pos)
        # valves need support nearby (NPS >= 3)
        if ln.nps >= 3:
            for z0, z1 in _valve_groups(run):
                if not any(min(abs(x - z0), abs(x - z1)) <= std.valve_support_check_mm for x in S):
                    add("support_valve", "error", ln.id, f"valve at s={(z0 + z1) / 2:.0f} mm has no support within {std.valve_support_check_mm:.0f} mm",
                        point_at(run.pts, run.cum, (z0 + z1) / 2))
        steel_needed: list[Support] = []
        for s in sups:
            load_note = next((n for n in s.note.split(" | ") if "LOAD" in n), "")
            if load_note.startswith("GENERIC LOAD"):
                add("support_load", "warning", ln.id, f"{s.id}: {load_note} - import a support catalogue to select a real support", s.pos)
            elif load_note:
                add("support_load", "error", ln.id, f"{s.id}: {load_note}", s.pos)
            if s.kind in ("rest", "guide", "anchor") and s.mounting == "rack_beam":
                if not any(abs(s.pos[0] - bx) < 1.0 for bx in beams):
                    add("support_structure", "error", ln.id, f"{s.id} is on the rack but not on a beam (x={s.pos[0]:.0f})", s.pos)
            if s.mounting == "needs_steel":
                steel_needed.append(s)
            if s.mounting == "stanchion" and not _stanchion_clear(prj, ln, s.pos, s.height, std.stanchion_section_mm, routes, equip):
                add("support_clash", "error", ln.id, f"stanchion of {s.id} runs into equipment or another line", s.pos)
        if steel_needed:
            ids = ", ".join(x.id.rsplit("-", 1)[-1] for x in steel_needed[:6]) + (" ..." if len(steel_needed) > 6 else "")
            add("support_structure", "warning", ln.id, f"{len(steel_needed)} support(s) ({ids}) at EL {min(x.pos[2] for x in steel_needed):.0f}-{max(x.pos[2] for x in steel_needed):.0f} "
                f"need supporting steel that is not in the model (above stanchion limit {std.stanchion_max_height_mm:.0f} mm or on a vertical run)", steel_needed[0].pos)
    unknown = [s for s in prj.supports if not any(l.id == s.line for l in prj.lines)]
    for s in unknown:
        add("support_position", "error", s.line, f"support {s.id} belongs to a line that no longer exists")
    return items


def line_summary(prj: Project, ln, std: Profile) -> dict:
    return allowable_span(prj, ln, std)

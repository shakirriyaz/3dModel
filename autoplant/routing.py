"""Orthogonal 3D pipe router (A* on a voxel grid) with bend penalty, minimum leg length
between bends, equipment/pipe clearances, rack-level preference and headroom avoidance."""
from __future__ import annotations
import math
import numpy as np
from .model import Project, Route, DIRS, split_ref
from .standards import Profile
from . import rack as rackmod
from ._astar import astar, DX, DY, DZ, OPP as OPPA

DV = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
DNAME = {"+x": 0, "-x": 1, "+y": 2, "-y": 3, "+z": 4, "-z": 5}
OPP = {0: 1, 1: 0, 2: 3, 3: 2, 4: 5, 5: 4}


def _add(a, b, k=1.0):
    return (a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k)


class Grid:
    def __init__(self, prj: Project):
        p = prj.grid_pitch
        lo = [1e18] * 3
        hi = [-1e18] * 3
        for e in prj.equipment:
            a, b = e.box()
            for i in range(3):
                lo[i] = min(lo[i], a[i])
                hi[i] = max(hi[i], b[i])
        m = prj.margin
        self.p = p
        self.o = (math.floor((lo[0] - m) / p) * p, math.floor((lo[1] - m) / p) * p, 0.0)
        top = max(prj.max_height, hi[2] + 2000, (prj.rack_elevation or 0) + 1500)
        self.n = (int((hi[0] + m - self.o[0]) / p) + 2, int((hi[1] + m - self.o[1]) / p) + 2,
                  int(top / p) + 2)

    def cell(self, w):
        return tuple(int(round((w[i] - self.o[i]) / self.p)) for i in range(3))

    def world(self, c):
        return tuple(self.o[i] + c[i] * self.p for i in range(3))

    def box_slices(self, lo, hi, infl):
        """Slices of cells whose centres lie within [lo-infl, hi+infl]."""
        sl = []
        for i in range(3):
            a = math.ceil((lo[i] - infl - self.o[i]) / self.p - 1e-9)
            b = math.floor((hi[i] + infl - self.o[i]) / self.p + 1e-9)
            sl.append(slice(max(a, 0), min(b, self.n[i] - 1) + 1))
        return tuple(sl)


class Router:
    def __init__(self, prj: Project, std: Profile):
        self.prj, self.std = prj, std
        self.g = Grid(prj)
        self.routes: dict[str, Route] = {}
        self.steel = rackmod.boxes(prj)
        self.stubs: dict[str, tuple] = {}   # line -> (start_pt, start_stub, end_pt, end_stub, d_s, d_e, r)
        self._prep()

    # ------------------------------------------------------------------
    def _prep(self):
        p, s = self.g.p, self.std
        for ln in self.prj.lines:
            dm = self.prj.dims(ln)
            r = dm.r
            ends = []
            for ref in (ln.from_, ln.to):
                if ref.startswith("@"):
                    ends.append(None)       # stub end / branch tap: decided during routing
                    continue
                eid, nid = split_ref(ref)
                e = self.prj.eq(eid)
                nz = next(n for n in e.nozzles if n.id == nid)
                pos = e.nozzle_pos(nz)
                d = DIRS[nz.direction]
                need = max(dm.takeoff + s.weld_gap, r + s.equip_clearance + 2 * p)
                if ln.is_stub:
                    need = max(need, self.STUB_LENGTH)      # reserve the whole boundary stub
                stub_len = math.ceil(need / p) * p
                ends.append((pos, _add(pos, d, stub_len), nz.direction, stub_len))
            self.stubs[ln.id] = (ends[0], ends[1], r)

    def _paint_segment(self, arr, a, b, infl):
        lo = [min(a[i], b[i]) for i in range(3)]
        hi = [max(a[i], b[i]) for i in range(3)]
        arr[self.g.box_slices(lo, hi, infl)] = True

    def _blocked(self, ln, routed_pts, w0, w1, soft=False):
        """Blocked-cell array for the window [w0, w1) (cell indices, x/y windowed, full z)."""
        g, s = self.g, self.std
        r = self.prj.dims(ln).r
        half = g.p * 0.5
        shape = (w1[0] - w0[0], w1[1] - w0[1], g.n[2])
        arr = np.zeros(shape, dtype=bool)
        sarr = np.zeros(shape, dtype=bool)
        wlo = (g.o[0] + w0[0] * g.p - g.p, g.o[1] + w0[1] * g.p - g.p)
        whi = (g.o[0] + w1[0] * g.p + g.p, g.o[1] + w1[1] * g.p + g.p)

        def paint(lo, hi, infl, target=None):
            target = arr if target is None else target
            if hi[0] + infl < wlo[0] or lo[0] - infl > whi[0] or hi[1] + infl < wlo[1] or lo[1] - infl > whi[1]:
                return
            sl = g.box_slices(lo, hi, infl)
            xs = slice(max(sl[0].start - w0[0], 0), min(sl[0].stop - w0[0], shape[0]))
            ys = slice(max(sl[1].start - w0[1], 0), min(sl[1].stop - w0[1], shape[1]))
            if xs.start < xs.stop and ys.start < ys.stop:
                target[xs, ys, sl[2]] = True

        kz = math.ceil((r + s.grade_clearance - g.o[2]) / g.p)
        arr[:, :, :max(kz, 0)] = True       # pipe soffit must clear grade
        for e in self.prj.equipment:
            lo, hi = e.box()
            paint(lo, hi, r + s.equip_clearance + half)
        for _, lo, hi in self.steel:
            paint(lo, hi, r + 25.0 + half)
        for other in self.prj.lines:
            if other.id == ln.id:
                continue
            ro = self.prj.dims(other).r
            infl = r + ro + s.pipe_gap + half
            pts = routed_pts.get(other.id)
            if pts is None:       # reserve nozzle stubs of lines not yet routed
                a_end, b_end, _ = self.stubs[other.id]
                segs = [(e[0], e[1]) for e in (a_end, b_end) if e is not None]
            else:
                segs = list(zip(pts, pts[1:]))
            for a, b in segs:
                paint(tuple(min(a[i], b[i]) for i in range(3)), tuple(max(a[i], b[i]) for i in range(3)), infl,
                      sarr if (soft and pts is not None) else arr)
        return arr, sarr

    # ------------------------------------------------------------------
    MAX_STATES = 90_000_000
    SOFT_PENALTY_CELLS = 6.0      # cost of one cell inside another pipe's envelope, in cells
    SOFT_MAX_EXP = 12_000_000

    def _zf(self):
        g, s = self.g, self.std
        zf = np.ones(g.n[2])
        for iz in range(g.n[2]):
            z = g.o[2] + iz * g.p
            if s.grade_band < z < s.headroom:
                zf[iz] = 4.0
            elif self.prj.rack_elevation is not None and abs(z - self.prj.rack_elevation) < g.p * 0.5:
                zf[iz] = 0.7
        return zf

    def _search(self, ln, routed_pts, start, d0, run0, goal, want, goal_run, R, soft=False):
        """A* between two grid states. Returns list of ((x,y,z), heading, 0) or None."""
        g, s = self.g, self.std
        p = g.p
        for c in (start, goal):
            if not all(0 <= c[i] < g.n[i] for i in range(3)):
                return None
        zf = self._zf()
        last = None
        for margin_mm in ((25000, None) if soft else (8000,)):
            m = max(g.n[0], g.n[1]) if margin_mm is None else int(margin_mm / p)
            while True:
                w0 = (max(min(start[0], goal[0]) - m, 0), max(min(start[1], goal[1]) - m, 0))
                w1 = (min(max(start[0], goal[0]) + m + 1, g.n[0]), min(max(start[1], goal[1]) + m + 1, g.n[1]))
                nx, ny, nz = w1[0] - w0[0], w1[1] - w0[1], g.n[2]
                if nx * ny * nz * 6 * (R + 1) <= self.MAX_STATES or m <= 4:
                    break
                m = int(m * 0.85)          # shrink to the memory budget
            if last == (w0, w1):
                continue
            last = (w0, w1)
            arr, sarr = self._blocked(ln, routed_pts, w0, w1, soft)
            ls = (start[0] - w0[0], start[1] - w0[1], start[2])
            lg = (goal[0] - w0[0], goal[1] - w0[1], goal[2])
            arr[ls] = False
            arr[lg] = False
            blocked = np.ascontiguousarray(arr.reshape(-1).astype(np.uint8))
            softa = np.ascontiguousarray(sarr.reshape(-1).astype(np.uint8))
            path, _ = astar(blocked, nx, ny, nz, ls[0], ls[1], ls[2], d0, run0, lg[0], lg[1], lg[2], want,
                            R, goal_run, float(p), float(s.bend_penalty), zf, 1.05,
                            self.SOFT_MAX_EXP if soft else 4_000_000,
                            DX, DY, DZ, OPPA, softa, float(p) * self.SOFT_PENALTY_CELLS)
            if len(path):
                return [((int(a) + w0[0], int(b) + w0[1], int(c)), int(d), 0) for a, b, c, d in path]
        return None

    def _params(self, ln):
        g, s = self.g, self.std
        (p0, s0, dn0, L0), (p1, s1, dn1, L1), r = self.stubs[ln.id]
        min_leg = 2 * self.prj.dims(ln).takeoff + s.weld_gap
        R = max(1, math.ceil(min_leg / g.p))
        return (p0, s0, dn0, L0), (p1, s1, dn1, L1), r, min_leg, R

    def route_line(self, ln, routed_pts, soft=False) -> Route:
        """soft=False: other pipes are hard obstacles (one 8 m-margin window, cheap to fail).
        soft=True: routed pipes are passable at a high cost (congestion-tolerant, wide window);
        the caller rips up whichever lines the result runs through."""
        g = self.g
        (p0, s0, dn0, L0), (p1, s1, dn1, L1), r, min_leg, R = self._params(ln)
        d0, want = DNAME[dn0], OPP[DNAME[dn1]]
        cells = self._search(ln, routed_pts, g.cell(s0), d0, min(R, math.ceil(L0 / g.p)), g.cell(s1), want,
                             max(0, math.ceil((min_leg - L1) / g.p)), R, soft)
        if cells is None:
            return Route(line_id=ln.id, status="failed", note="no feasible path on grid")
        return self._finish(ln, cells, p0, p1, s0, s1, r, g)

    # ---------------- pipe-rack-first routing ----------------
    def _self_clear(self, pts, r) -> bool:
        infl = 2 * r + 50.0
        segs = [(tuple(min(a[i], b[i]) - infl for i in range(3)), tuple(max(a[i], b[i]) + infl for i in range(3)))
                for a, b in zip(pts, pts[1:])]
        for i in range(len(segs)):
            for j in range(i + 2, len(segs)):
                if all(segs[i][0][k] < segs[j][1][k] and segs[j][0][k] < segs[i][1][k] for k in range(3)):
                    # perpendicular neighbours-of-neighbours may legitimately be close; require real overlap
                    a, b = pts[i], pts[i + 1]
                    c, d = pts[j], pts[j + 1]
                    hl = [min(a[k], b[k]) - r for k in range(3)]
                    hh = [max(a[k], b[k]) + r for k in range(3)]
                    if all(min(c[k], d[k]) < hh[k] and max(c[k], d[k]) > hl[k] for k in range(3)):
                        return False
        return True

    def route_via_rack(self, ln, pts) -> Route | None:
        """Long line: stub -> riser to a free rack track -> straight run along the rack -> drop to stub."""
        rk, g = self.prj.rack, self.g
        (p0, s0, dn0, L0), (p1, s1, dn1, L1), r, min_leg, R = self._params(ln)
        if not rk.active or r > rackmod.MAX_RACK_RADIUS or abs(p1[0] - p0[0]) < rk.long_threshold:
            return None
        p = g.p
        sign = 1 if p1[0] > p0[0] else -1
        dtrav = 0 if sign > 0 else 1
        snap = lambda v: round(v / p) * p
        lo_x, hi_x = rk.x0 + rk.bay, rk.x1 - rk.bay
        xe = min(max(snap(s0[0]), lo_x), hi_x)
        xx = min(max(snap(s1[0]), lo_x), hi_x)
        if sign * (xx - xe) < 2 * min_leg:
            return None
        ymid = 0.5 * (p0[1] + p1[1])
        reach = rk.width / 2 - 500.0
        ys = [snap(rk.y_center + k * p) for k in range(-int(reach / p), int(reach / p) + 1)]
        yp = min(max(ymid, rk.y_center - reach), rk.y_center + reach)
        cands = sorted(((abs(y - yp), z, y) for y in ys for z in rackmod.tier_z(self.prj)))
        d0, want = DNAME[dn0], OPP[DNAME[dn1]]
        goal_run = max(0, math.ceil((min_leg - L1) / p))
        tried = 0
        for _, z, y in cands:
            if tried >= self.RACK_TRIES:
                break
            if self._conflicts(ln, [(xe, y, z), (xx, y, z)], pts):
                continue
            tried += 1
            e_cell, x_cell = g.cell((xe, y, z)), g.cell((xx, y, z))
            legA = self._search(ln, pts, g.cell(s0), d0, min(R, math.ceil(L0 / p)), e_cell, dtrav, 0, R)
            if legA is None:
                continue
            legC = self._search(ln, pts, x_cell, dtrav, R, g.cell(s1), want, goal_run, R)
            if legC is None:
                continue
            step = sign
            mid = [((x, e_cell[1], e_cell[2]), dtrav, 0) for x in range(e_cell[0] + step, x_cell[0], step)]
            rt = self._finish(ln, legA + mid + legC, p0, p1, s0, s1, r, g)
            if self._self_clear(rt.points, r) and not self._conflicts(ln, rt.points, pts):
                rt.note = "rack"
                return rt
        return None

    RACK_TRIES = 6

    STUB_LENGTH = 3000.0     # free-end stub to the plant boundary

    def route_stub(self, ln) -> Route:
        """Single-nozzle line leaving the unit (off-page connector / battery limit): straight out."""
        (p0, s0, dn0, L0), _, r = self.stubs[ln.id]
        d = DIRS[dn0]
        end = _add(p0, d, max(self.STUB_LENGTH, L0))
        return Route(line_id=ln.id, status="routed", points=[tuple(p0), tuple(end)], note="battery-limit stub")

    TAP_TRIES = 6

    def _tap_candidates(self, ln, parent_pts):
        """Tap points (position, outward direction) on the parent header, nearest the branch start first."""
        g, s = self.g, self.std
        par = next(l for l in self.prj.lines if l.id == ln.branch_of)
        dp, db = self.prj.dims(par), self.prj.dims(ln)
        rp, rb = dp.r, db.r
        p = g.p
        margin = max(dp.takeoff, db.takeoff, dp.view.tee_half_length(par.nps, ln.nps)) + s.weld_gap + 300.0
        start = self.stubs[ln.id][0][1]
        out = []
        for a, b in zip(parent_pts, parent_pts[1:]):
            ax = [i for i in range(3) if abs(b[i] - a[i]) > 1e-6]
            if len(ax) != 1:
                continue
            ax = ax[0]
            L = abs(b[ax] - a[ax])
            sgn = 1 if b[ax] > a[ax] else -1
            k = math.ceil(margin / p)
            while k * p <= L - margin:
                pos = list(a)
                pos[ax] = a[ax] + sgn * k * p
                for dn, dv in DIRS.items():
                    if dv[ax] != 0:
                        continue
                    out.append((sum(abs(pos[i] - start[i]) for i in range(3)) + (0 if dv[2] == 0 else 400), tuple(pos), dn, dv))
                k += 2
        out.sort(key=lambda t: t[0])
        res, seen = [], set()
        for _, pos, dn, dv in out:
            key = (round(pos[0] / 500), round(pos[1] / 500), round(pos[2] / 500), dn)
            if key in seen:
                continue
            seen.add(key)
            res.append((pos, dn, dv))
        return res, rp, rb

    def route_branch(self, ln, pts, soft=False) -> Route:
        parent = pts.get(ln.branch_of)
        if parent is None:
            return Route(line_id=ln.id, status="failed", note="header not routed yet")
        g, s = self.g, self.std
        cands, rp, rb = self._tap_candidates(ln, parent)
        p = g.p
        infl = rb + rp + s.pipe_gap + p * 0.5
        L1 = math.ceil((infl + p) / p) * p
        start_end = self.stubs[ln.id][0]
        tries = 0
        for pos, dn, dv in cands:
            if tries >= (1 if soft else self.TAP_TRIES):
                break
            tries += 1
            s1 = _add(pos, dv, L1)
            self.stubs[ln.id] = (start_end, (pos, s1, dn, L1), rb)
            rt = self.route_line(ln, pts, soft)
            if rt.status == "routed":
                rt.note = "branch"
                return rt
        return Route(line_id=ln.id, status="failed", note="no feasible branch route")

    def route_one(self, ln, pts, soft=False) -> Route:
        if ln.is_stub:
            return self.route_stub(ln)
        if ln.branch_of:
            return self.route_branch(ln, pts, soft)
        if soft:
            return self.route_line(ln, pts, soft=True)
        return self.route_line_auto(ln, pts)

    def route_line_auto(self, ln, pts) -> Route:
        rt = self.route_via_rack(ln, pts)
        return rt if rt is not None else self.route_line(ln, pts)

    def _finish(self, ln, cells, p0, p1, s0, s1, r, g):
        # corners of the grid path
        corners = [cells[0][0]]
        for a, b in zip(cells, cells[1:]):
            if b[1] != a[1]:
                corners.append(a[0])
        corners.append(cells[-1][0])
        dedup = [corners[0]]
        for c in corners[1:]:
            if c != dedup[-1]:
                dedup.append(c)
        corners = dedup
        pts = self._snap_to_nozzles(corners, p0, p1, s0, s1)
        return Route(line_id=ln.id, status="routed", points=pts)

    def _snap_to_nozzles(self, corners, p0, p1, s0, s1):
        """Convert grid corners to real coordinates so the first/last legs hit the exact nozzles.
        Per axis the coordinate is piecewise-constant; first piece takes the start stub value,
        last piece the end stub value, middle pieces keep the grid value."""
        g = self.g
        C = [g.world(c) for c in corners]
        n = len(C)
        if n == 1:
            C = [C[0], C[0]]
            n = 2
        real = [list(c) for c in C]
        for a in range(3):
            # pieces: index increments when moving along axis a
            piece = [0]
            for i in range(1, n):
                piece.append(piece[-1] + (1 if abs(C[i][a] - C[i - 1][a]) > 1e-6 else 0))
            last = piece[-1]
            for i in range(n):
                if piece[i] == 0:
                    real[i][a] = s0[a]
                elif piece[i] == last:
                    real[i][a] = s1[a]
            if last == 0 and abs(s0[a] - s1[a]) > 1e-6:
                real[-1][a] = s1[a]   # unavoidable jog; QC will flag short/diagonal legs
        pts = [tuple(p0)] + [tuple(r) for r in real] + [tuple(p1)]
        # remove duplicate / collinear points
        out = [pts[0]]
        for q in pts[1:]:
            if math.dist(q, out[-1]) < 1e-6:
                continue
            out.append(q)
        res = [out[0]]
        for i in range(1, len(out) - 1):
            a, b, c = res[-1], out[i], out[i + 1]
            v1 = tuple(b[j] - a[j] for j in range(3))
            v2 = tuple(c[j] - b[j] for j in range(3))
            cross = (v1[1] * v2[2] - v1[2] * v2[1], v1[2] * v2[0] - v1[0] * v2[2],
                     v1[0] * v2[1] - v1[1] * v2[0])
            if max(abs(x) for x in cross) < 1e-6 and sum(x * y for x, y in zip(v1, v2)) > 0:
                continue
            res.append(b)
        res.append(out[-1])
        return res

    # ------------------------------------------------------------------
    def _related(self, lid) -> set:
        """Lines in the same tee network (ancestors + descendants): they legitimately touch at the tee."""
        by_id = {l.id: l for l in self.prj.lines}
        out, cur = set(), lid
        while by_id[cur].branch_of:
            cur = by_id[cur].branch_of
            out.add(cur)
        out |= self._descendants(lid)
        return out

    def _descendants(self, lid) -> set:
        out, todo = set(), [lid]
        while todo:
            x = todo.pop()
            for l in self.prj.lines:
                if l.branch_of == x and l.id not in out:
                    out.add(l.id)
                    todo.append(l.id)
        return out

    def _conflicts(self, ln, pts, routed_pts) -> list[str]:
        """Routed lines whose clearance envelope the path `pts` runs through."""
        s = self.std
        by_id = {l.id: l for l in self.prj.lines}
        r = self.prj.dims(ln).r
        out = []
        related = self._related(ln.id)
        for lid, other in routed_pts.items():
            if lid == ln.id or lid in related:
                continue
            infl = r + self.prj.dims(by_id[lid]).r + s.pipe_gap
            hit = False
            for a, b in zip(pts, pts[1:]):
                alo = [min(a[i], b[i]) - infl for i in range(3)]
                ahi = [max(a[i], b[i]) + infl for i in range(3)]
                for c, d in zip(other, other[1:]):
                    if all(min(c[i], d[i]) < ahi[i] and max(c[i], d[i]) > alo[i] for i in range(3)):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                out.append(lid)
        return out

    def route_all(self, reroute_factor: int = 4, keep: dict | None = None) -> dict[str, Route]:
        """1) hard pass (headers big-first, stubs, then branches once their header exists).
        2) Every failed line is routed congestion-tolerantly (routed pipes passable at high cost);
        the lines it runs through (and their branches) are ripped up, the line is committed and the
        ripped lines go back in the queue. Bounded by a reroute budget.
        `keep` = routes to retain unchanged (incremental re-route after an edit)."""
        by_id = {l.id: l for l in self.prj.lines}
        base = [l for l in self.prj.lines if not l.is_stub and not l.branch_of]
        base.sort(key=lambda l: (-self.prj.dims(l).od, l.id))
        stubs = [l for l in self.prj.lines if l.is_stub]
        branches = [l for l in self.prj.lines if l.branch_of]

        def depth(l):
            d = 0
            while l.branch_of and d < 20:
                l, d = by_id[l.branch_of], d + 1
            return d
        branches.sort(key=lambda l: (depth(l), -self.prj.dims(l).od, l.id))
        routes: dict[str, Route] = {}
        pts: dict[str, list] = {}
        for lid, rt in (keep or {}).items():
            if lid in by_id and rt.status == "routed":
                routes[lid], pts[lid] = rt, rt.points
        queue: list[str] = []
        for ln in base + stubs + branches:
            if ln.id in pts:
                continue
            if ln.branch_of and ln.branch_of not in pts:
                routes[ln.id] = Route(line_id=ln.id, status="failed", note="header not routed yet")
                queue.append(ln.id)
                continue
            rt = self.route_one(ln, pts)
            routes[ln.id] = rt
            if rt.status == "routed":
                pts[ln.id] = rt.points
            else:
                queue.append(ln.id)
        budget = reroute_factor * len(self.prj.lines)
        rips: dict[str, int] = {}
        stall = 0
        while queue and budget > 0 and stall <= len(queue):
            lid = queue.pop(0)
            ln = by_id[lid]
            if ln.branch_of and ln.branch_of not in pts:      # wait for the header
                queue.append(lid)
                stall += 1
                continue
            stall = 0
            rt = self.route_one(ln, pts)                       # cheap hard retry (state changed)
            if rt.status != "routed":
                rt = self.route_one(ln, pts, soft=True)
            if rt.status != "routed":
                routes[lid] = rt                                # infeasible even ignoring pipes
                continue
            conf = self._conflicts(ln, rt.points, pts)
            if len(conf) > 12 or any(rips.get(c, 0) >= 4 for c in conf):
                routes[lid] = Route(line_id=lid, status="failed", note="congestion: too many conflicts")
                continue
            ripped = set()
            for c in conf:
                ripped.add(c)
                ripped |= self._descendants(c)
            for c in sorted(ripped):
                if c in pts:
                    pts.pop(c)
                    routes[c] = Route(line_id=c, status="failed", note="ripped up")
                    rips[c] = rips.get(c, 0) + 1
                    queue.append(c)
            budget -= len(ripped)
            routes[lid], pts[lid] = rt, rt.points
        for lid in queue:       # budget exhausted
            if routes[lid].status != "routed":
                routes[lid] = Route(line_id=lid, status="failed", note="reroute budget exhausted")
        self.routes = routes
        self._place_valves()
        return routes

    def _place_valves(self):
        by_id = {l.id: l for l in self.prj.lines}
        taps: dict[str, list] = {}
        for lid, rt in self.routes.items():
            ln = by_id[lid]
            if rt.status == "routed" and ln.branch_of:
                taps.setdefault(ln.branch_of, []).append(rt.points[-1])
        for lid, rt in self.routes.items():
            ln = by_id[lid]
            rt.valve_positions = []
            if rt.status != "routed" or not ln.valves:
                continue
            n = len(ln.valves)
            dm = self.prj.dims(ln)
            keep_off = max((dm.ftf(v) for v in ln.valves), default=0.0) / 2 + 300.0
            legs = [(math.dist(a, b), a, b) for a, b in zip(rt.points, rt.points[1:])]
            legs.sort(key=lambda t: -t[0])
            chosen = None
            for L, a, b in legs:
                ux = [(b[i] - a[i]) / L for i in range(3)]
                spots = [tuple(a[i] + ux[i] * L * (k + 1) / (n + 1) for i in range(3)) for k in range(n)]
                if all(math.dist(sp, t) >= keep_off for sp in spots for t in taps.get(lid, [])):
                    chosen = spots
                    break
            if chosen is None:
                L, a, b = legs[0]
                ux = [(b[i] - a[i]) / L for i in range(3)]
                chosen = [tuple(a[i] + ux[i] * L * (k + 1) / (n + 1) for i in range(3)) for k in range(n)]
            rt.valve_positions = list(zip(ln.valves, chosen))

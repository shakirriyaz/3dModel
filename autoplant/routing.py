"""Orthogonal 3D pipe router (A* on a voxel grid) with bend penalty, minimum leg length
between bends, equipment/pipe clearances, rack-level preference and headroom avoidance."""
from __future__ import annotations
import math
import numpy as np
from .model import Project, Route, DIRS, split_ref
from .catalog import od, elbow_takeoff, valve_ftf
from .standards import Profile
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
        self.stubs: dict[str, tuple] = {}   # line -> (start_pt, start_stub, end_pt, end_stub, d_s, d_e, r)
        self._prep()

    # ------------------------------------------------------------------
    def _prep(self):
        p, s = self.g.p, self.std
        for ln in self.prj.lines:
            r = od(ln.nps) / 2
            ends = []
            for ref in (ln.from_, ln.to):
                eid, nid = split_ref(ref)
                e = self.prj.eq(eid)
                nz = next(n for n in e.nozzles if n.id == nid)
                pos = e.nozzle_pos(nz)
                d = DIRS[nz.direction]
                need = max(elbow_takeoff(ln.nps) + s.weld_gap, r + s.equip_clearance + 2 * p)
                stub_len = math.ceil(need / p) * p
                ends.append((pos, _add(pos, d, stub_len), nz.direction, stub_len))
            self.stubs[ln.id] = (ends[0], ends[1], r)

    def _paint_segment(self, arr, a, b, infl):
        lo = [min(a[i], b[i]) for i in range(3)]
        hi = [max(a[i], b[i]) for i in range(3)]
        arr[self.g.box_slices(lo, hi, infl)] = True

    def _blocked(self, ln, routed_pts, w0, w1):
        """Blocked-cell array for the window [w0, w1) (cell indices, x/y windowed, full z)."""
        g, s = self.g, self.std
        r = od(ln.nps) / 2
        half = g.p * 0.5
        shape = (w1[0] - w0[0], w1[1] - w0[1], g.n[2])
        arr = np.zeros(shape, dtype=bool)
        wlo = (g.o[0] + w0[0] * g.p - g.p, g.o[1] + w0[1] * g.p - g.p)
        whi = (g.o[0] + w1[0] * g.p + g.p, g.o[1] + w1[1] * g.p + g.p)

        def paint(lo, hi, infl):
            if hi[0] + infl < wlo[0] or lo[0] - infl > whi[0] or hi[1] + infl < wlo[1] or lo[1] - infl > whi[1]:
                return
            sl = g.box_slices(lo, hi, infl)
            xs = slice(max(sl[0].start - w0[0], 0), min(sl[0].stop - w0[0], shape[0]))
            ys = slice(max(sl[1].start - w0[1], 0), min(sl[1].stop - w0[1], shape[1]))
            if xs.start < xs.stop and ys.start < ys.stop:
                arr[xs, ys, sl[2]] = True

        kz = math.ceil((r + s.grade_clearance - g.o[2]) / g.p)
        arr[:, :, :max(kz, 0)] = True       # pipe soffit must clear grade
        for e in self.prj.equipment:
            lo, hi = e.box()
            paint(lo, hi, r + s.equip_clearance + half)
        for other in self.prj.lines:
            if other.id == ln.id:
                continue
            ro = od(other.nps) / 2
            infl = r + ro + s.pipe_gap + half
            pts = routed_pts.get(other.id)
            if pts is None:       # reserve nozzle stubs of lines not yet routed
                (p0, p1, _, _), (q0, q1, _, _), _ = self.stubs[other.id]
                segs = [(p0, p1), (q0, q1)]
            else:
                segs = list(zip(pts, pts[1:]))
            for a, b in segs:
                paint(tuple(min(a[i], b[i]) for i in range(3)), tuple(max(a[i], b[i]) for i in range(3)), infl)
        return arr

    # ------------------------------------------------------------------
    MAX_STATES = 90_000_000

    def route_line(self, ln, routed_pts) -> Route:
        g, s = self.g, self.std
        (p0, s0, dn0, L0), (p1, s1, dn1, L1), r = self.stubs[ln.id]
        start, goal = g.cell(s0), g.cell(s1)
        for c in (start, goal):
            if not all(0 <= c[i] < g.n[i] for i in range(3)):
                return Route(line_id=ln.id, status="failed", note="nozzle stub outside grid")
        p = g.p
        min_leg = 2 * elbow_takeoff(ln.nps) + s.weld_gap
        R = max(1, math.ceil(min_leg / p))
        d0, want = DNAME[dn0], OPP[DNAME[dn1]]
        run0 = min(R, math.ceil(L0 / p))
        goal_run = max(0, math.ceil((min_leg - L1) / p))
        zf = np.ones(g.n[2])
        for iz in range(g.n[2]):
            z = g.o[2] + iz * p
            if s.grade_band < z < s.headroom:
                zf[iz] = 4.0
            elif self.prj.rack_elevation is not None and abs(z - self.prj.rack_elevation) < p * 0.5:
                zf[iz] = 0.7
        last = None
        for margin_mm in (8000, 20000, 60000, None):
            if margin_mm is None:
                m = max(g.n[0], g.n[1])
            else:
                m = int(margin_mm / p)
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
            arr = self._blocked(ln, routed_pts, w0, w1)
            ls = (start[0] - w0[0], start[1] - w0[1], start[2])
            lg = (goal[0] - w0[0], goal[1] - w0[1], goal[2])
            arr[ls] = False
            arr[lg] = False
            blocked = np.ascontiguousarray(arr.reshape(-1).astype(np.uint8))
            path, _ = astar(blocked, nx, ny, nz, ls[0], ls[1], ls[2], d0, run0, lg[0], lg[1], lg[2], want,
                            R, goal_run, float(p), float(s.bend_penalty), zf, 1.05,
                            4_000_000,
                            DX, DY, DZ, OPPA)
            if len(path):
                cells = [((int(a) + w0[0], int(b) + w0[1], int(c)), int(d), 0) for a, b, c, d in path]
                return self._finish(ln, cells, p0, p1, s0, s1, r, g)
        return Route(line_id=ln.id, status="failed", note="no feasible path on grid")

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
    def _dist_to_route(self, pts, q):
        best = 1e18
        for a, b in zip(pts, pts[1:]):
            lo = [min(a[i], b[i]) for i in range(3)]
            hi = [max(a[i], b[i]) for i in range(3)]
            d2 = sum(max(lo[i] - q[i], 0, q[i] - hi[i]) ** 2 for i in range(3))
            best = min(best, d2)
        return math.sqrt(best)

    def route_all(self, rounds: int = 3) -> dict[str, Route]:
        """Initial pass (big pipes first), then rip-up & reroute for failed lines:
        remove the k nearest routed lines, route the failed one first, reroute the removed."""
        by_id = {l.id: l for l in self.prj.lines}
        order = sorted(self.prj.lines, key=lambda l: (-od(l.nps), l.id))
        routes: dict[str, Route] = {}
        pts: dict[str, list] = {}
        for ln in order:
            routes[ln.id] = self.route_line(ln, pts)
            if routes[ln.id].status == "routed":
                pts[ln.id] = routes[ln.id].points
        for _ in range(rounds):
            failed = [lid for lid, r in routes.items() if r.status != "routed"]
            if not failed:
                break
            progress = False
            for fid in failed:
                (a, _, _, _), (b, _, _, _), _ = self.stubs[fid]
                near = sorted(pts, key=lambda l: min(self._dist_to_route(pts[l], a), self._dist_to_route(pts[l], b)))
                for k in (2, 5, 10, 20):
                    rip = near[:k]
                    if not rip:
                        break
                    snap_pts, snap_routes = dict(pts), dict(routes)
                    for l in rip:
                        pts.pop(l)
                    rt = self.route_line(by_id[fid], pts)
                    ok = rt.status == "routed"
                    if ok:
                        routes[fid], pts[fid] = rt, rt.points
                        for l in sorted(rip, key=lambda l: (-od(by_id[l].nps), l)):
                            r2 = self.route_line(by_id[l], pts)
                            routes[l] = r2
                            if r2.status == "routed":
                                pts[l] = r2.points
                            else:
                                ok = False
                                break
                    if ok:
                        progress = True
                        break
                    pts.clear(); pts.update(snap_pts)
                    routes.clear(); routes.update(snap_routes)
            if not progress:
                break
        self.routes = routes
        self._place_valves()
        return routes

    def _place_valves(self):
        by_id = {l.id: l for l in self.prj.lines}
        for lid, rt in self.routes.items():
            ln = by_id[lid]
            if rt.status != "routed" or not ln.valves:
                continue
            legs = [(math.dist(a, b), a, b) for a, b in zip(rt.points, rt.points[1:])]
            legs.sort(key=lambda t: -t[0])
            L, a, b = legs[0]
            n = len(ln.valves)
            ux = [(b[i] - a[i]) / L for i in range(3)]
            for k, vt in enumerate(ln.valves):
                t = L * (k + 1) / (n + 1)
                rt.valve_positions.append((vt, tuple(a[i] + ux[i] * t for i in range(3))))

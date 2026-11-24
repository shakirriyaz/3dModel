"""Orthogonal 3D pipe router (A* on a voxel grid) with bend penalty, minimum leg length
between bends, equipment/pipe clearances, rack-level preference and headroom avoidance."""
from __future__ import annotations
import heapq
import math
import numpy as np
from .model import Project, Route, DIRS, split_ref
from .catalog import od, elbow_takeoff, valve_ftf
from .standards import Profile

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

    def _blocked(self, ln, routed_pts: dict[str, list]):
        g, s = self.g, self.std
        r = od(ln.nps) / 2
        half = g.p * 0.5
        arr = np.zeros(g.n, dtype=bool)
        kz = math.ceil((r + s.grade_clearance - g.o[2]) / g.p)
        arr[:, :, :max(kz, 0)] = True       # pipe soffit must clear grade
        for e in self.prj.equipment:
            lo, hi = e.box()
            arr[g.box_slices(lo, hi, r + s.equip_clearance + half)] = True
        for other in self.prj.lines:
            if other.id == ln.id:
                continue
            ro = od(other.nps) / 2
            infl = r + ro + s.pipe_gap + half
            pts = routed_pts.get(other.id)
            if pts is None:       # reserve nozzle stubs of lines not yet routed
                (p0, p1, _, _), (q0, q1, _, _), _ = self.stubs[other.id]
                self._paint_segment(arr, p0, p1, infl)
                self._paint_segment(arr, q0, q1, infl)
            else:
                for a, b in zip(pts, pts[1:]):
                    self._paint_segment(arr, a, b, infl)
        return arr

    # ------------------------------------------------------------------
    def route_line(self, ln, routed_pts) -> Route:
        g, s = self.g, self.std
        (p0, s0, dn0, L0), (p1, s1, dn1, L1), r = self.stubs[ln.id]
        arr = self._blocked(ln, routed_pts)
        start, goal = g.cell(s0), g.cell(s1)
        for c in (start, goal):
            arr[c] = False
        nx, ny, nz = g.n
        for c in (start, goal):
            if not all(0 <= c[i] < g.n[i] for i in range(3)):
                return Route(line_id=ln.id, status="failed", note="nozzle stub outside grid")
        blocked = bytearray(arr.tobytes())
        p = g.p
        min_leg = 2 * elbow_takeoff(ln.nps) + s.weld_gap
        R = max(1, math.ceil(min_leg / p))
        d0 = DNAME[dn0]
        want = OPP[DNAME[dn1]]
        run0 = min(R, math.ceil(L0 / p))
        goal_run = max(0, math.ceil((min_leg - L1) / p))
        # per-level horizontal cost factors
        zf = []
        for iz in range(nz):
            z = g.o[2] + iz * p
            f = 1.0
            if s.grade_band < z < s.headroom:
                f = 4.0
            elif self.prj.rack_elevation is not None and abs(z - self.prj.rack_elevation) < p * 0.5:
                f = 0.7
            zf.append(f)
        fmin = min(zf)
        bend = s.bend_penalty
        sy, sz_ = nz * ny, nz
        gx, gy, gz = goal

        def h(x, y, z):
            return (abs(x - gx) + abs(y - gy) + abs(z - gz)) * p * fmin

        heap = [(h(*start), 0, 0.0, start[0], start[1], start[2], d0, run0)]
        best = {(start, d0, run0): 0.0}
        parent = {}
        cnt = 0
        expansions = 0
        while heap:
            f, _, gc, x, y, z, d, run = heapq.heappop(heap)
            key = ((x, y, z), d, run)
            if best.get(key, 1e18) < gc - 1e-9:
                continue
            if (x, y, z) == goal and d == want and run >= goal_run:
                return self._finish(ln, key, parent, p0, p1, s0, s1, r, g)
            expansions += 1
            if expansions > 1_500_000:
                break
            for nd in range(6):
                if nd == OPP[d]:
                    continue
                if nd == d:
                    nrun = min(run + 1, R)
                    extra = 0.0
                else:
                    if run < R:
                        continue
                    nrun = 1
                    extra = bend
                dx, dy, dz = DV[nd]
                X, Y, Z = x + dx, y + dy, z + dz
                if X < 0 or Y < 0 or Z < 0 or X >= nx or Y >= ny or Z >= nz:
                    continue
                if blocked[(X * ny + Y) * nz + Z]:
                    continue
                step = p * (zf[Z] if nd < 4 else 1.0)
                ng = gc + step + extra
                nk = ((X, Y, Z), nd, nrun)
                if ng < best.get(nk, 1e18) - 1e-9:
                    best[nk] = ng
                    parent[nk] = key
                    cnt += 1
                    heapq.heappush(heap, (ng + h(X, Y, Z), cnt, ng, X, Y, Z, nd, nrun))
        return Route(line_id=ln.id, status="failed", note="no feasible path on grid")

    def _finish(self, ln, key, parent, p0, p1, s0, s1, r, g):
        cells = []
        k = key
        while True:
            cells.append(k)
            if k not in parent:
                break
            k = parent[k]
        cells.reverse()
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
    def route_all(self, max_rounds: int = 4) -> dict[str, Route]:
        order = sorted(self.prj.lines, key=lambda l: (-od(l.nps), l.id))
        failed_first: list[str] = []
        best: dict[str, Route] = {}
        best_fail = None
        for rnd in range(max_rounds):
            ids = failed_first + [l.id for l in order if l.id not in failed_first]
            by_id = {l.id: l for l in self.prj.lines}
            routed_pts: dict[str, list] = {}
            routes: dict[str, Route] = {}
            fails = []
            for lid in ids:
                rt = self.route_line(by_id[lid], routed_pts)
                routes[lid] = rt
                if rt.status == "routed":
                    routed_pts[lid] = rt.points
                else:
                    fails.append(lid)
            if best_fail is None or len(fails) < best_fail:
                best, best_fail = routes, len(fails)
            if not fails:
                break
            failed_first = fails + [i for i in failed_first if i not in fails]
        self.routes = best
        self._place_valves()
        return best

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

"""Compiled A* kernel (numba). State = (cell, heading, run-since-last-bend)."""
from __future__ import annotations
import heapq
import numpy as np
from numba import njit

DX = np.array([1, -1, 0, 0, 0, 0], dtype=np.int64)
DY = np.array([0, 0, 1, -1, 0, 0], dtype=np.int64)
DZ = np.array([0, 0, 0, 0, 1, -1], dtype=np.int64)
OPP = np.array([1, 0, 3, 2, 5, 4], dtype=np.int64)


@njit(cache=True)
def astar(blocked, nx, ny, nz, sx, sy, sz, d0, run0, gx, gy, gz, want, R, goal_run,
          pitch, bend, zf, eps, max_exp, dx, dy, dz, opp, soft, soft_pen):
    R1 = R + 1
    nstates = nx * ny * nz * 6 * R1
    g = np.full(nstates, np.inf, dtype=np.float32)
    parent = np.full(nstates, -1, dtype=np.int32)
    fmin = zf.min()
    s0 = ((((sx * ny + sy) * nz + sz) * 6 + d0) * R1) + run0
    g[s0] = 0.0
    h0 = (abs(sx - gx) + abs(sy - gy) + abs(sz - gz)) * pitch * fmin * eps
    heap = [(h0, np.int64(s0))]
    exp = 0
    found = np.int64(-1)
    while len(heap) > 0:
        f, s = heapq.heappop(heap)
        run = s % R1
        t = s // R1
        d = t % 6
        t = t // 6
        z = t % nz
        t = t // nz
        y = t % ny
        x = t // ny
        gc = g[s]
        # stale entry check
        hh = (abs(x - gx) + abs(y - gy) + abs(z - gz)) * pitch * fmin * eps
        if f > gc + hh + 1e-3:
            continue
        if x == gx and y == gy and z == gz and d == want and run >= goal_run:
            found = s
            break
        exp += 1
        if exp > max_exp:
            break
        for nd in range(6):
            if nd == opp[d]:
                continue
            if nd == d:
                nrun = min(run + 1, R)
                extra = 0.0
            else:
                if run < R:
                    continue
                nrun = 1
                extra = bend
            X = x + dx[nd]
            Y = y + dy[nd]
            Z = z + dz[nd]
            if X < 0 or Y < 0 or Z < 0 or X >= nx or Y >= ny or Z >= nz:
                continue
            if blocked[(X * ny + Y) * nz + Z] != 0:
                continue
            step = pitch * (zf[Z] if nd < 4 else 1.0)
            ng = gc + step + extra
            if soft[(X * ny + Y) * nz + Z] != 0:
                ng += soft_pen
            ns = ((((X * ny + Y) * nz + Z) * 6 + nd) * R1) + nrun
            if ng < g[ns] - 1e-4:
                g[ns] = ng
                parent[ns] = s
                hn = (abs(X - gx) + abs(Y - gy) + abs(Z - gz)) * pitch * fmin * eps
                heapq.heappush(heap, (ng + hn, np.int64(ns)))
    if found < 0:
        return np.empty((0, 4), dtype=np.int64), exp
    # reconstruct
    n = 0
    s = found
    while s >= 0:
        n += 1
        s = parent[s]
    out = np.empty((n, 4), dtype=np.int64)
    s = found
    i = n - 1
    while s >= 0:
        run = s % R1
        t = s // R1
        d = t % 6
        t = t // 6
        z = t % nz
        t = t // nz
        y = t % ny
        x = t // ny
        out[i, 0] = x
        out[i, 1] = y
        out[i, 2] = z
        out[i, 3] = d
        i -= 1
        s = parent[s]
    return out, exp

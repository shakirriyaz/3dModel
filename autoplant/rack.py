"""Pipe rack: lane reservation, tier elevations and steel (columns + tier beams) as solids."""
from __future__ import annotations
import math
from .model import Project

COL = 300.0          # column section (mm)
BEAM_DEPTH = 300.0
BEAM_TOP_BELOW = 400.0   # beam top this far under the pipe centreline of its tier
MAX_RACK_RADIUS = 200.0  # OD <= 400 mm (about NPS 14); larger pipes are routed in open space


def wanted(prj: Project) -> bool:
    r = prj.rack
    return bool(r.enabled) if r.enabled is not None else len(prj.equipment) >= 10


def base_elevation(prj: Project) -> float:
    return prj.rack_elevation if prj.rack_elevation is not None else 4500.0


def tier_z(prj: Project) -> list[float]:
    return [base_elevation(prj) + k * prj.rack.tier_spacing for k in range(prj.rack.tiers)]


def lane_half(prj: Project, equip_clearance: float) -> float:
    return prj.rack.width / 2 + equip_clearance


def finalize(prj: Project, equip_clearance: float):
    """After layout: fix rack x-extent; disable (with a note) if equipment sits in the lane."""
    r = prj.rack
    r.active = False
    if not wanted(prj):
        return
    lo = min(e.box()[0][0] for e in prj.equipment)
    hi = max(e.box()[1][0] for e in prj.equipment)
    r.x0 = math.floor((lo - r.bay) / r.bay) * r.bay
    r.x1 = math.ceil((hi + r.bay) / r.bay) * r.bay
    half = lane_half(prj, equip_clearance)
    for e in prj.equipment:
        (_, ylo, _), (_, yhi, _) = e.box()
        if ylo < r.y_center + half and yhi > r.y_center - half:
            prj.notes.append(f"pipe rack disabled: {e.id} occupies the rack lane (move it or set rack.enabled=false)")
            return
    r.active = True


def boxes(prj: Project) -> list[tuple[str, tuple, tuple]]:
    """Rack steel as (name, lo, hi) boxes."""
    r = prj.rack
    if not r.active:
        return []
    out = []
    tz = tier_z(prj)
    top = tz[-1] - BEAM_TOP_BELOW
    nb = int(round((r.x1 - r.x0) / r.bay))
    ya, yb = r.y_center - r.width / 2, r.y_center + r.width / 2
    for k in range(nb + 1):
        x = r.x0 + k * r.bay
        for j, y in enumerate((ya, yb)):
            out.append((f"RK-C{k:02d}{'SN'[j]}", (x - COL / 2, y - COL / 2, 0.0), (x + COL / 2, y + COL / 2, top)))
        for t, z in enumerate(tz):
            zt = z - BEAM_TOP_BELOW
            out.append((f"RK-B{k:02d}T{t + 1}", (x - 100.0, ya, zt - BEAM_DEPTH), (x + 100.0, yb, zt)))
    return out

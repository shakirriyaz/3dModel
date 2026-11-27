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


def design_bay(prj: Project, std) -> float:
    """Bay from the shortest allowable span among the lines that will use the rack (long-haul, small enough for a tier)."""
    from . import supports
    r = prj.rack
    spans = []
    for ln in prj.lines:
        if ln.is_stub or ln.branch_of:
            continue
        dm = prj.dims(ln)
        if dm.r > MAX_RACK_RADIUS:
            continue
        try:
            ends = []
            for ref in (ln.from_, ln.to):
                e = prj.eq(ref.split(".")[0])
                nz = next((n for n in e.nozzles if n.id == ref.split(".", 1)[1]), None)
                ends.append(e.nozzle_pos(nz)[0] if nz is not None and nz.offset is not None else e.position[0])
        except (KeyError, StopIteration, IndexError):
            continue
        if abs(ends[0] - ends[1]) >= r.long_threshold:
            spans.append(supports.allowable_span(prj, ln, std)["span"])
    if not spans:
        return r.bay_max
    return max(r.bay_min, min(r.bay_max, math.floor(min(spans) / 500.0) * 500.0))


def finalize(prj: Project, std):
    """After layout: fix the rack bay and x-extent; disable (with a note) if equipment sits in the lane."""
    equip_clearance = std if isinstance(std, (int, float)) else std.equip_clearance
    r = prj.rack
    r.active = False
    if not wanted(prj):
        return
    if not r.bay_max:
        r.bay_max = r.bay
    if r.bay_auto and not isinstance(std, (int, float)):
        r.bay = design_bay(prj, std)
        prj.notes = [n for n in prj.notes if not n.startswith("rack bay")]
        if r.bay < r.bay_max:
            prj.notes.append(f"rack bay set to {r.bay:.0f} mm (from {r.bay_max:.0f} mm) so the pipes on the rack stay within their allowable span")
    else:
        r.bay = r.bay_max
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

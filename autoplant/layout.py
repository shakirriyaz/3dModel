"""Equipment placement + automatic nozzle generation."""
from __future__ import annotations
import math
from .model import Project, Nozzle, split_ref, DIRS
from .catalog import EQUIP_TEMPLATES
from .standards import Profile
from . import rack as rackmod


def _snap(v, p):
    return round(v / p) * p


def apply_templates(prj: Project):
    for e in prj.equipment:
        shape, size, elev = EQUIP_TEMPLATES.get(e.kind, EQUIP_TEMPLATES["generic"])
        e.shape = e.shape or shape
        e.size = e.size or size
        if e.elevation is None:
            e.elevation = elev
        e.size = tuple(_snap(s, 2 * prj.grid_pitch) or 2 * prj.grid_pitch for s in e.size)
        e.elevation = _snap(e.elevation, prj.grid_pitch)


def place_equipment(prj: Project, std: Profile):
    p = prj.grid_pitch
    fixed = [e for e in prj.equipment if e.position is not None]
    free = [e for e in prj.equipment if e.position is None]
    if not free:
        return
    use_lane = rackmod.wanted(prj)
    x = max((e.box()[1][0] for e in fixed), default=0.0) + (std.equip_spacing if fixed else 0.0)
    # areas in order of first appearance (no area tags -> one block, as before)
    areas: dict = {}
    for e in free:
        areas.setdefault(e.area, []).append(e)
    for grp in areas.values():
        x = _place_block(prj, std, grp, x, use_lane)
        if len(areas) > 1:
            x += std.equip_spacing        # extra gap between areas


def _place_block(prj, std, grp, x, use_lane):
    p = prj.grid_pitch
    ids = {e.id for e in grp}
    succ = {e.id: set() for e in grp}
    for ln in prj.lines:
        a_, b_ = split_ref(ln.from_)[0], split_ref(ln.to)[0]
        if a_ != b_ and a_ in ids and b_ in ids:
            succ[a_].add(b_)
    # BFS levels from sources (no incoming line within the block; else first item); back-edges
    # in loops are ignored so recycles do not distort the flow order.
    indeg = {i: 0 for i in ids}
    for bs in succ.values():
        for b_ in bs:
            indeg[b_] += 1
    order = [e.id for e in grp]
    roots = [i for i in order if indeg[i] == 0] or [order[0]]
    level, queue = {}, []
    for r_ in roots:
        level[r_] = 0
        queue.append(r_)
    while queue:
        a_ = queue.pop(0)
        for b_ in sorted(succ[a_]):
            if b_ not in level:
                level[b_] = level[a_] + 1
                queue.append(b_)
    for i in order:
        level.setdefault(i, 0)
    layers: dict[int, list] = {}
    for e in grp:
        layers.setdefault(level[e.id], []).append(e)
    for lv in sorted(layers):
        items = layers[lv]
        width = max(e.size[0] for e in items)
        if not use_lane:
            total = sum(e.size[1] for e in items) + std.equip_spacing * (len(items) - 1)
            y = -total / 2
            for e in items:
                e.position = (_snap(x + width / 2, p), _snap(y + e.size[1] / 2, p))
                y += e.size[1] + std.equip_spacing
        else:   # two rows either side of the rack lane
            half = rackmod.lane_half(prj, std.equip_clearance) + std.equip_spacing / 2
            yc = prj.rack.y_center
            for side, g2 in ((1, items[0::2]), (-1, items[1::2])):
                y = half
                for e in g2:
                    e.position = (_snap(x + width / 2, p), _snap(yc + side * (y + e.size[1] / 2), p))
                    y += e.size[1] + std.equip_spacing
        x += width + std.equip_spacing
    return x


def _slots(shape, sy, sz):
    """Candidate (lateral, vertical) nozzle offsets on a face, preferred first."""
    mid = sz / 2
    out = [(0.0, mid)]
    for k in range(1, 8):
        for s in (1, -1):
            v = mid + s * k * 1000.0
            if 250 <= v <= sz - 250 and shape != "box":
                out.append((0.0, v))
    if shape == "box" or len(out) < 4:
        lat = sy / 2 - 250
        for k in range(1, 8):
            for s in (1, -1):
                l = s * k * 750.0
                if abs(l) <= max(lat, 0):
                    out.append((l, mid))
    return out


def _top_slots(sx, sy):
    """(x, y) offsets on the top face, centre first, then a 1 m grid inside the footprint."""
    out = [(0.0, 0.0)]
    for k in range(1, 6):
        for dx, dy in ((k, 0), (-k, 0), (0, k), (0, -k)):
            x, y = dx * 1000.0, dy * 1000.0
            if abs(x) <= sx / 2 - 250 and abs(y) <= sy / 2 - 250:
                out.append((x, y))
    return out


def auto_nozzles(prj: Project):
    """Create/complete nozzles for every line end, facing the partner equipment."""
    p = prj.grid_pitch
    used: dict[tuple[str, str], int] = {}
    for ln in prj.lines:
        for end, other in ((ln.from_, ln.to), (ln.to, ln.from_)):
            eid, nid = split_ref(end)
            oid, _ = split_ref(other)
            e, o = prj.eq(eid), prj.eq(oid)
            if nid is None:
                nid = f"{ln.id}-{'S' if end == ln.from_ else 'E'}"
                if end == ln.from_:
                    ln.from_ = f"{eid}.{nid}"
                else:
                    ln.to = f"{eid}.{nid}"
            nz = next((n for n in e.nozzles if n.id == nid), None)
            if nz is None:
                nz = Nozzle(id=nid)
                e.nozzles.append(nz)
            nz.nps = nz.nps or ln.nps
            if nz.direction is None:
                dx, dy = o.position[0] - e.position[0], o.position[1] - e.position[1]
                pref = ["+x" if dx >= 0 else "-x", "+y" if dy >= 0 else "-y"]
                if abs(dy) > abs(dx):
                    pref.reverse()
                pref += [("-x" if pref[0] == "+x" else "+x") if pref[0][1] == "x" else ("-y" if pref[0] == "+y" else "+y")]
                pref += [d for d in ("+x", "-x", "+y", "-y") if d not in pref] + ["+z"]
                chosen = None
                for cand in pref:
                    d = DIRS[cand]
                    sx, sy, sz = e.size
                    if cand == "+z":
                        slots = _top_slots(sx, sy)
                    else:
                        w = sy if d[0] else sx
                        slots = _slots(e.shape, w, sz)
                    k = used.get((eid, cand), 0)
                    if k < len(slots):
                        chosen = (cand, d, slots[k])
                        used[(eid, cand)] = k + 1
                        break
                if chosen is None:
                    raise ValueError(f"no free nozzle slot on {eid} for line {ln.id}")
                nz.direction = chosen[0]
                d, (lat, vert) = chosen[1], chosen[2]
                sx, sy, sz = e.size
                if chosen[0] == "+z":
                    nz.offset = (_snap(lat, p), _snap(vert, p), sz)
                elif d[0]:
                    nz.offset = (d[0] * sx / 2, _snap(lat, p), _snap(vert, p))
                else:
                    nz.offset = (_snap(lat, p), d[1] * sy / 2, _snap(vert, p))

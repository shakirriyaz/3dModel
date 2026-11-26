"""Automatic quality-control gates. Independent of the router: checks the final geometry."""
from __future__ import annotations
import math
from .model import Project, Route, QCItem, QCReport, split_ref
from .standards import Profile
from . import rack as rackmod, catalog

TOL = 1.0


def _seg_box(a, b, infl):
    """Pipe leg as a box: inflated by `infl` perpendicular to the axis only (flat end caps)."""
    ax = [i for i in range(3) if abs(b[i] - a[i]) > TOL]
    k = [0.0 if (len(ax) == 1 and i == ax[0]) else infl for i in range(3)]
    return (tuple(min(a[i], b[i]) - k[i] for i in range(3)), tuple(max(a[i], b[i]) + k[i] for i in range(3)))


def _overlap(b1, b2, tol=TOL):
    return all(b1[0][i] < b2[1][i] - tol and b2[0][i] < b1[1][i] - tol for i in range(3))


def _centre(b1, b2):
    """Centre of the overlap volume of two boxes."""
    return tuple((max(b1[0][i], b2[0][i]) + min(b1[1][i], b2[1][i])) / 2 for i in range(3))


def _axis(a, b):
    d = [abs(b[i] - a[i]) for i in range(3)]
    return [i for i in range(3) if d[i] > TOL]


def _tee_pair(prj, routes, l1, i1, l2, i2) -> bool:
    by = {l.id: l for l in prj.lines}
    for br, bi, hd in ((l1, i1, l2), (l2, i2, l1)):
        if by[br].branch_of == hd and bi == len(routes[br].points) - 2:
            return True
    return False


def run_qc(prj: Project, routes: dict[str, Route], std: Profile) -> QCReport:
    items: list[QCItem] = []
    checks = ["completeness", "nozzle_connectivity", "orthogonality", "pipe_size_known", "min_leg",
              "bend_count", "equipment_clash", "pipe_clash", "equipment_overlap", "headroom",
              "valve_fit", "duplicate_nozzle_use", "rack_clash", "self_clash", "tee_geometry", "input_note", "spec_unknown", "size_not_in_spec",
              "valve_not_in_spec", "branch_not_in_spec", "builtin_catalogue"]

    def add(check, sev, subj, msg, at=None):
        items.append(QCItem(check=check, severity=sev, subject=subj, message=msg,
                            at=None if at is None else tuple(round(v, 1) for v in at),
                            key=f"{check}|{subj}|{msg}"))

    for note in prj.notes:
        add("input_note", "warning", "input", note)

    if not prj.specs and prj.lines:
        add("builtin_catalogue", "warning", "specs",
            f"no piping spec imported - the built-in generic catalogue is assumed for {len(prj.lines)} line(s) "
            "(generic pipe/fitting/valve dimensions, not a piping class)")

    # equipment overlap (layout)
    boxes = {e.id: e.box() for e in prj.equipment}
    ids = list(boxes)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ba = (tuple(boxes[a][0][k] - std.equip_spacing / 2 for k in range(3)),
                  tuple(boxes[a][1][k] + std.equip_spacing / 2 for k in range(3)))
            bb = (tuple(boxes[b][0][k] - std.equip_spacing / 2 for k in range(3)),
                  tuple(boxes[b][1][k] + std.equip_spacing / 2 for k in range(3)))
            if _overlap(ba, bb):
                hard = _overlap(boxes[a], boxes[b])
                add("equipment_overlap", "error" if hard else "warning", f"{a}/{b}",
                    "equipment overlap" if hard else f"spacing below {std.equip_spacing:.0f} mm",
                    _centre(boxes[a], boxes[b]) if hard else _centre(ba, bb))

    # nozzle use
    seen: dict[str, str] = {}
    for ln in prj.lines:
        for ref in (ln.from_, ln.to):
            if ref.startswith("@"):
                continue
            if ref in seen:
                add("duplicate_nozzle_use", "error", ln.id, f"nozzle {ref} also used by {seen[ref]} (branches/tees not supported yet)")
            seen[ref] = ln.id

    seg_index = []   # (line_id, idx, a, b, r)
    for ln in prj.lines:
        rt = routes.get(ln.id)
        dm = prj.dims(ln)
        r = dm.r
        if prj.specs:
            view = prj.spec_view(ln.spec)
            if ln.spec not in prj.specs:
                add("spec_unknown", "error", ln.id, f"piping class {ln.spec!r} is not defined in the imported spec")
            else:
                if not view.has_size(ln.nps):
                    add("size_not_in_spec", "error", ln.id, f"NPS {ln.nps:g} is not available in class {ln.spec}")
                for vt in sorted(set(ln.valves)):
                    if not view.valve_allowed(vt, ln.nps):
                        add("valve_not_in_spec", "error", ln.id, f"class {ln.spec} has no {vt} valve in NPS {ln.nps:g}")
                if ln.branch_of:
                    hdr = next((l for l in prj.lines if l.id == ln.branch_of), None)
                    hv = prj.spec_view(hdr.spec) if hdr else None
                    if hv is not None and hv.spec is not None and hv.spec.branches:
                        br = hv.branch_row(hdr.nps, ln.nps)
                        if br is None:
                            add("branch_not_in_spec", "error", ln.id, f"branch table of class {hdr.spec} has no {hdr.nps:g}x{ln.nps:g} entry")
                        elif br.type == "none":
                            add("branch_not_in_spec", "error", ln.id, f"class {hdr.spec} does not allow a {hdr.nps:g}x{ln.nps:g} branch")
        if not prj.specs and ln.nps not in catalog.PIPE_OD:
            add("pipe_size_known", "warning", ln.id, f"NPS {ln.nps} not in the built-in catalogue; nearest size used")
        if rt is None or rt.status != "routed":
            add("completeness", "error", ln.id, f"line not routed ({rt.note if rt else 'no route'})")
            continue
        pts = rt.points
        # endpoints at nozzles
        for ref, pt in ((ln.from_, pts[0]), (ln.to, pts[-1])):
            if ref.startswith("@"):
                continue
            eid, nid = split_ref(ref)
            e = prj.eq(eid)
            nz = next(n for n in e.nozzles if n.id == nid)
            if math.dist(e.nozzle_pos(nz), pt) > TOL:
                add("nozzle_connectivity", "error", ln.id, f"route does not terminate at {ref}")
        if ln.branch_of:
            hdr = routes.get(ln.branch_of)
            if hdr is None or hdr.status != "routed":
                add("tee_geometry", "error", ln.id, f"header {ln.branch_of} not routed")
            else:
                tap, ok = pts[-1], False
                for a, b in zip(hdr.points, hdr.points[1:]):
                    ax = _axis(a, b)
                    if len(ax) != 1:
                        continue
                    k = ax[0]
                    others = [i for i in range(3) if i != k]
                    if all(abs(tap[i] - a[i]) < TOL for i in others) and min(a[k], b[k]) + TOL < tap[k] < max(a[k], b[k]) - TOL:
                        last = _axis(pts[-2], pts[-1])
                        ok = last != [k] and len(last) == 1
                        break
                if not ok:
                    add("tee_geometry", "error", ln.id, f"branch does not meet header {ln.branch_of} perpendicularly inside a straight run", tap)
        # orthogonality / zero legs
        bends = 0
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            ax = _axis(a, b)
            if len(ax) != 1:
                add("orthogonality", "error", ln.id, f"leg {i} is not axis-aligned or has zero length")
            seg_index.append((ln.id, i, a, b, r))
        for i in range(len(pts) - 2):
            if _axis(pts[i], pts[i + 1]) != _axis(pts[i + 1], pts[i + 2]):
                bends += 1
        if bends > std.max_bends_per_line:
            add("bend_count", "warning", ln.id, f"{bends} bends (limit {std.max_bends_per_line})")
        # min leg between fittings
        tk = dm.takeoff
        legs = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        for i, L in enumerate(legs):
            ends_bends = (1 if i > 0 else 0) + (1 if i < len(legs) - 1 else 0)
            need = tk * ends_bends + std.weld_gap + (0 if ends_bends == 2 else 0)
            if ends_bends == 2:
                need = 2 * tk + std.weld_gap
            if L < need - TOL:
                add("min_leg", "error", ln.id, f"leg {i} length {L:.0f} < required {need:.0f} mm")
        # valve fit
        if rt.valve_positions:
            longest = max(legs)
            need = sum(dm.ftf(v) for v, _ in rt.valve_positions) + 2 * std.weld_gap
            if longest < need:
                add("valve_fit", "error", ln.id, f"longest leg {longest:.0f} mm too short for {len(rt.valve_positions)} valve(s)")
        # headroom (warning): horizontal legs in the walking band
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            if 0 < i < len(pts) - 2 and _axis(a, b) in ([0], [1]):
                z = a[2]
                if std.grade_band < z - r and z + r < std.headroom:
                    add("headroom", "warning", ln.id, f"horizontal leg {i} at z={z:.0f} inside headroom band")
    # equipment clash
    for lid, i, a, b, r in seg_index:
        sb = _seg_box(a, b, r)
        for e in prj.equipment:
            if _overlap(sb, e.box()):
                add("equipment_clash", "error", lid, f"leg {i} intersects equipment {e.id}", _centre(sb, e.box()))
    # pipe vs rack steel (exact, no extra clearance)
    steel = rackmod.boxes(prj)
    for lid, i, a, b, r in seg_index:
        sb = _seg_box(a, b, r)
        for name, lo, hi in steel:
            if _overlap(sb, (lo, hi)):
                add("rack_clash", "error", lid, f"leg {i} intersects rack steel {name}", _centre(sb, (lo, hi)))
    # a line passing through itself (non-adjacent legs)
    by_line: dict[str, list] = {}
    for lid, i, a, b, r in seg_index:
        by_line.setdefault(lid, []).append((i, a, b, r))
    for lid, segs in by_line.items():
        for x in range(len(segs)):
            for y in range(x + 2, len(segs)):
                if _overlap(_seg_box(segs[x][1], segs[x][2], segs[x][3]), _seg_box(segs[y][1], segs[y][2], segs[y][3])):
                    add("self_clash", "error", lid, f"legs {segs[x][0]} and {segs[y][0]} intersect",
                        _centre(_seg_box(segs[x][1], segs[x][2], segs[x][3]), _seg_box(segs[y][1], segs[y][2], segs[y][3])))
    # pipe-pipe clash (sweep along x over inflated boxes keeps this near-linear)
    half = std.pipe_gap / 2
    cand = []
    for (l, i, a, b, r) in seg_index:
        bb = _seg_box(a, b, r + half)
        cand.append((bb[0][0], bb[1][0], l, i, a, b, r, bb))
    cand.sort(key=lambda t: t[0])
    for ii, (x0, x1, l1, i1, a1, b1, r1, bb1) in enumerate(cand):
        for x0b, x1b, l2, i2, a2, b2, r2, bb2 in cand[ii + 1:]:
            if x0b >= x1 - TOL:
                break
            if l1 == l2 or not _overlap(bb1, bb2):
                continue
            if _tee_pair(prj, routes, l1, i1, l2, i2):
                continue          # the branch's last leg legitimately meets its header at the tee
            hard = _overlap(_seg_box(a1, b1, r1), _seg_box(a2, b2, r2))
            add("pipe_clash", "error" if hard else "warning", f"{l1}/{l2}",
                "pipes intersect" if hard else f"pipe gap < {std.pipe_gap:.0f} mm (legs {i1}/{i2})",
                _centre(bb1, bb2))
    errors = sum(1 for i in items if i.severity == "error")
    return QCReport(passed=errors == 0, errors=errors, warnings=len(items) - errors,
                    checks_run=checks, items=items, standard=std.name, standard_validated=std.validated)


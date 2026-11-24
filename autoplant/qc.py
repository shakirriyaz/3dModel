"""Automatic quality-control gates. Independent of the router: checks the final geometry."""
from __future__ import annotations
import math
from .model import Project, Route, QCItem, QCReport, split_ref
from .catalog import od, elbow_takeoff, valve_ftf, PIPE_OD
from .standards import Profile

TOL = 1.0


def _seg_box(a, b, infl):
    """Pipe leg as a box: inflated by `infl` perpendicular to the axis only (flat end caps)."""
    ax = [i for i in range(3) if abs(b[i] - a[i]) > TOL]
    k = [0.0 if (len(ax) == 1 and i == ax[0]) else infl for i in range(3)]
    return (tuple(min(a[i], b[i]) - k[i] for i in range(3)), tuple(max(a[i], b[i]) + k[i] for i in range(3)))


def _overlap(b1, b2, tol=TOL):
    return all(b1[0][i] < b2[1][i] - tol and b2[0][i] < b1[1][i] - tol for i in range(3))


def _axis(a, b):
    d = [abs(b[i] - a[i]) for i in range(3)]
    return [i for i in range(3) if d[i] > TOL]


def run_qc(prj: Project, routes: dict[str, Route], std: Profile) -> QCReport:
    items: list[QCItem] = []
    checks = ["completeness", "nozzle_connectivity", "orthogonality", "pipe_size_known", "min_leg",
              "bend_count", "equipment_clash", "pipe_clash", "equipment_overlap", "headroom",
              "valve_fit", "duplicate_nozzle_use"]

    def add(check, sev, subj, msg):
        items.append(QCItem(check=check, severity=sev, subject=subj, message=msg))

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
                    "equipment overlap" if hard else f"spacing below {std.equip_spacing:.0f} mm")

    # nozzle use
    seen: dict[str, str] = {}
    for ln in prj.lines:
        for ref in (ln.from_, ln.to):
            if ref in seen:
                add("duplicate_nozzle_use", "error", ln.id, f"nozzle {ref} also used by {seen[ref]} (branches/tees not supported yet)")
            seen[ref] = ln.id

    seg_index = []   # (line_id, idx, a, b, r)
    for ln in prj.lines:
        rt = routes.get(ln.id)
        r = od(ln.nps) / 2
        if ln.nps not in PIPE_OD:
            add("pipe_size_known", "warning", ln.id, f"NPS {ln.nps} not in catalog; nearest size used")
        if rt is None or rt.status != "routed":
            add("completeness", "error", ln.id, f"line not routed ({rt.note if rt else 'no route'})")
            continue
        pts = rt.points
        # endpoints at nozzles
        for ref, pt in ((ln.from_, pts[0]), (ln.to, pts[-1])):
            eid, nid = split_ref(ref)
            e = prj.eq(eid)
            nz = next(n for n in e.nozzles if n.id == nid)
            if math.dist(e.nozzle_pos(nz), pt) > TOL:
                add("nozzle_connectivity", "error", ln.id, f"route does not terminate at {ref}")
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
        tk = elbow_takeoff(ln.nps)
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
            need = valve_ftf(ln.nps) * len(rt.valve_positions) + 2 * std.weld_gap
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
                add("equipment_clash", "error", lid, f"leg {i} intersects equipment {e.id}")
    # pipe-pipe clash
    for ii, (l1, i1, a1, b1, r1) in enumerate(seg_index):
        for l2, i2, a2, b2, r2 in seg_index[ii + 1:]:
            if l1 == l2:
                continue
            if _overlap(_seg_box(a1, b1, r1 + std.pipe_gap / 2), _seg_box(a2, b2, r2 + std.pipe_gap / 2)):
                hard = _overlap(_seg_box(a1, b1, r1), _seg_box(a2, b2, r2))
                add("pipe_clash", "error" if hard else "warning", f"{l1}/{l2}",
                    "pipes intersect" if hard else f"pipe gap < {std.pipe_gap:.0f} mm (legs {i1}/{i2})")
    errors = sum(1 for i in items if i.severity == "error")
    return QCReport(passed=errors == 0, errors=errors, warnings=len(items) - errors,
                    checks_run=checks, items=items, standard=std.name, standard_validated=std.validated)


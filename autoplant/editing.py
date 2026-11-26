"""Manual editing on top of the automatic result.

Edits are applied to the saved model (positions, nozzles, lines). Only what an edit touches is
re-routed - everything else is kept as is - then QC is re-run on the whole model, so a manual
change can never bypass the checks. Each edit snapshots the previous model for undo."""
from __future__ import annotations
import json, os, shutil, time
from .model import Project, Route, Equipment, Line, Nozzle, split_ref
from .catalog import od
from . import pipeline, rack as rackmod
from .standards import get_profile

OUTPUTS_DEFAULT = ["ifc", "dxf", "iso_pdf", "iso_svg", "bom_csv", "qc_report", "viewer"]


class EditError(ValueError):
    pass


def load_state(out_dir: str) -> tuple[Project, dict[str, Route]]:
    with open(os.path.join(out_dir, "model.json")) as f:
        m = json.load(f)
    prj = Project(**m["project"])
    routes = {k: Route(**v) for k, v in m["routes"].items()}
    return prj, routes


def _lines_of(prj, eid):
    return {l.id for l in prj.lines for ref in (l.from_, l.to) if not ref.startswith("@") and split_ref(ref)[0] == eid}


def _descendants(prj, ids):
    out, todo = set(ids), list(ids)
    while todo:
        x = todo.pop()
        for l in prj.lines:
            if l.branch_of == x and l.id not in out:
                out.add(l.id)
                todo.append(l.id)
    return out


def apply_ops(prj: Project, ops: list[dict]) -> set[str]:
    """Mutate the project; return ids of lines whose routes are no longer valid."""
    dirty: set[str] = set()
    for op in ops:
        kind = op.get("op")
        if kind == "move_equipment":
            e = prj.eq(op["id"])
            x = op["x"] if "x" in op else e.position[0] + op.get("dx", 0.0)
            y = op["y"] if "y" in op else e.position[1] + op.get("dy", 0.0)
            p = prj.grid_pitch
            e.position = (round(x / p) * p, round(y / p) * p)
            dirty |= _lines_of(prj, e.id)
        elif kind == "set_elevation":
            e = prj.eq(op["id"])
            e.elevation = round(float(op["z"]) / prj.grid_pitch) * prj.grid_pitch
            dirty |= _lines_of(prj, e.id)
        elif kind == "set_line":
            ln = next((l for l in prj.lines if l.id == op["id"]), None)
            if ln is None:
                raise EditError(f"unknown line {op['id']}")
            if "nps" in op and float(op["nps"]) != ln.nps:
                ln.nps = float(op["nps"])
                dirty.add(ln.id)
            for k in ("spec", "service"):
                if k in op:
                    setattr(ln, k, str(op[k]))
            if "valves" in op:
                ln.valves = [str(v) for v in op["valves"]]
                dirty.add(ln.id)          # valve positions depend on the route
        elif kind == "reroute":
            dirty.add(op["id"])
        elif kind == "set_nozzle":
            e = prj.eq(op["equipment"])
            nz = next((n for n in e.nozzles if n.id == op["nozzle"]), None)
            if nz is None:
                raise EditError(f"unknown nozzle {op['equipment']}.{op['nozzle']}")
            if "direction" in op:
                nz.direction = op["direction"]
                nz.offset = None          # re-derived on the new face
            if "offset" in op:
                nz.offset = tuple(op["offset"])
            dirty |= _lines_of(prj, e.id)
        elif kind == "add_line":
            if any(l.id == op["id"] for l in prj.lines):
                raise EditError(f"line {op['id']} exists")
            for ref in (op["from"], op["to"]):
                if not ref.startswith("@") and split_ref(ref)[0] not in {e.id for e in prj.equipment}:
                    raise EditError(f"unknown equipment in {ref}")
            prj.lines.append(Line(id=op["id"], **{"from": op["from"]}, to=op["to"], nps=float(op["nps"]),
                                  spec=op.get("spec", "A1"), service=op.get("service", ""), valves=op.get("valves", [])))
            dirty.add(op["id"])
        elif kind == "delete_line":
            if not any(l.id == op["id"] for l in prj.lines):
                raise EditError(f"unknown line {op['id']}")
            gone = _descendants(prj, [op["id"]])
            prj.lines = [l for l in prj.lines if l.id not in gone]
        elif kind == "add_equipment":
            if any(e.id == op["id"] for e in prj.equipment):
                raise EditError(f"equipment {op['id']} exists")
            prj.equipment.append(Equipment(id=op["id"], kind=op.get("kind", "generic"), size=op.get("size"),
                                           elevation=op.get("elevation"), position=tuple(op["position"]),
                                           area=op.get("area")))
        elif kind == "delete_equipment":
            if _lines_of(prj, op["id"]):
                raise EditError(f"{op['id']} still has lines; delete them first")
            prj.equipment = [e for e in prj.equipment if e.id != op["id"]]
        else:
            raise EditError(f"unknown op {kind!r}")
    return _descendants(prj, dirty)


def _invalid_by_geometry(prj, routes, std, skip: set[str]) -> set[str]:
    """Kept routes that now hit moved equipment / steel (cheap box test)."""
    bad = set()
    solids = [(e.box()) for e in prj.equipment] + [(lo, hi) for _, lo, hi in rackmod.boxes(prj)]
    for lid, rt in routes.items():
        if lid in skip or rt.status != "routed":
            continue
        r = next((od(l.nps) / 2 for l in prj.lines if l.id == lid), 0.0)
        for a, b in zip(rt.points, rt.points[1:]):
            ax = [i for i in range(3) if abs(b[i] - a[i]) > 1.0]
            k = [0.0 if (len(ax) == 1 and i == ax[0]) else r for i in range(3)]
            lo = [min(a[i], b[i]) - k[i] for i in range(3)]
            hi = [max(a[i], b[i]) + k[i] for i in range(3)]
            if any(all(lo[i] < s_hi[i] - 1 and s_lo[i] < hi[i] - 1 for i in range(3)) for s_lo, s_hi in solids):
                bad.add(lid)
                break
    return bad


def edit_dir(out_dir: str, ops: list[dict], outputs=None, author: str = "") -> dict:
    """Apply `ops` to the model saved in out_dir, re-route what changed, re-run QC and rewrite outputs."""
    t0 = time.time()
    prj, routes = load_state(out_dir)
    before = json.load(open(os.path.join(out_dir, "qc_report.json"))) if os.path.exists(os.path.join(out_dir, "qc_report.json")) else None
    hist = os.path.join(out_dir, "..", "history")
    os.makedirs(hist, exist_ok=True)
    n = len(os.listdir(hist)) + 1
    snap = os.path.join(hist, f"{n:04d}")
    os.makedirs(snap)
    shutil.copy(os.path.join(out_dir, "model.json"), snap)
    json.dump({"ops": ops, "author": author, "time": time.time()}, open(os.path.join(snap, "edit.json"), "w"))
    try:
        dirty = apply_ops(prj, ops)
    except Exception:
        shutil.rmtree(snap)
        raise
    prj.notes = [x for x in prj.notes if not x.startswith("pipe rack")]
    std = get_profile(prj.standard)
    was_rack = prj.rack.active
    rackmod.finalize(prj, std.equip_clearance)
    # nozzles that lost their offset (face change) or new lines need layout completion
    from . import layout
    layout.apply_templates(prj)
    layout.auto_nozzles(prj)
    keep = {k: v for k, v in routes.items() if k not in dirty and any(l.id == k for l in prj.lines)}
    if prj.rack.active != was_rack or (prj.rack.active and (prj.rack.x0, prj.rack.x1) != (prj.rack.x0, prj.rack.x1)):
        keep = {}
    keep = {k: v for k, v in keep.items() if k not in _invalid_by_geometry(prj, keep, std, set())}
    res = pipeline.run(prj, out_dir, outputs or OUTPUTS_DEFAULT, prj.standard, keep=keep)
    rerouted = [k for k, r in res.routes.items() if k not in keep]
    return {"version": n, "rerouted": sorted(rerouted), "kept": len(keep), "seconds": round(time.time() - t0, 2),
            "status": res.status, "errors": res.qc.errors, "warnings": res.qc.warnings,
            "errors_before": None if before is None else before["errors"]}


def history(out_dir: str) -> list[dict]:
    hist = os.path.join(out_dir, "..", "history")
    out = []
    if os.path.isdir(hist):
        for d in sorted(os.listdir(hist)):
            p = os.path.join(hist, d, "edit.json")
            if os.path.exists(p):
                e = json.load(open(p))
                out.append({"version": int(d), "ops": e["ops"], "author": e.get("author", ""), "time": e.get("time")})
    return out


def undo_dir(out_dir: str, outputs=None) -> dict:
    """Restore the model as it was before the latest edit (routes retained, QC + outputs regenerated)."""
    hist = os.path.join(out_dir, "..", "history")
    snaps = sorted(os.listdir(hist)) if os.path.isdir(hist) else []
    if not snaps:
        raise EditError("nothing to undo")
    last = os.path.join(hist, snaps[-1])
    shutil.copy(os.path.join(last, "model.json"), os.path.join(out_dir, "model.json"))
    shutil.rmtree(last)
    prj, routes = load_state(out_dir)
    res = pipeline.run(prj, out_dir, outputs or OUTPUTS_DEFAULT, prj.standard, keep=routes)
    return {"status": res.status, "errors": res.qc.errors, "warnings": res.qc.warnings, "remaining_history": len(snaps) - 1}

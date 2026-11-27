"""Manual editing on top of the automatic result.

Edits are applied to the saved model (positions, nozzles, lines). Only what an edit touches is
re-routed - everything else is kept as is - then QC is re-run on the whole model, so a manual
change can never bypass the checks. Each edit snapshots the previous model for undo."""
from __future__ import annotations
import json, os, shutil, time
from .model import Project, Route, Equipment, Line, Nozzle, split_ref
from . import pipeline, rack as rackmod
from .standards import get_profile

OUTPUTS_DEFAULT = ["ifc", "dxf", "iso_pdf", "iso_svg", "iso_dxf", "ga_pdf", "ga_dxf", "bom_csv", "qc_report", "clash_report", "viewer"]


class EditError(ValueError):
    pass


def load_state(out_dir: str) -> tuple[Project, dict[str, Route]]:
    with open(os.path.join(out_dir, "model.json")) as f:
        m = json.load(f)
    prj = Project(**m["project"])
    routes = {k: Route(**v) for k, v in m["routes"].items()}
    return prj, routes


def _sig(prj, ln):
    """Geometry-relevant dimensions of a line (what a spec change can alter)."""
    dm = prj.dims(ln)
    return (round(dm.od, 2), round(dm.takeoff, 2), tuple(round(dm.ftf(v), 1) for v in ln.valves))


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
            if "spec" in op and str(op["spec"]) != ln.spec:
                before = _sig(prj, ln)
                ln.spec = str(op["spec"])
                if _sig(prj, ln) != before:
                    dirty.add(ln.id)
            if "service" in op:
                ln.service = str(op["service"])
            if "valves" in op:
                ln.valves = [str(v) for v in op["valves"]]
                dirty.add(ln.id)          # valve positions depend on the route
        elif kind == "remap_spec":
            to = str(op["to"])
            if prj.specs and to not in prj.specs:
                raise EditError(f"class {to!r} is not in the imported spec (available: {', '.join(sorted(prj.specs))})")
            hit = [l for l in prj.lines if l.spec == op["from"]]
            if not hit:
                raise EditError(f"no line uses class {op['from']!r}")
            for l in hit:
                before = _sig(prj, l)
                l.spec = to
                if _sig(prj, l) != before:
                    dirty.add(l.id)
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
        r = next((prj.dims(l).r for l in prj.lines if l.id == lid), 0.0)
        for a, b in zip(rt.points, rt.points[1:]):
            ax = [i for i in range(3) if abs(b[i] - a[i]) > 1.0]
            k = [0.0 if (len(ax) == 1 and i == ax[0]) else r for i in range(3)]
            lo = [min(a[i], b[i]) - k[i] for i in range(3)]
            hi = [max(a[i], b[i]) + k[i] for i in range(3)]
            if any(all(lo[i] < s_hi[i] - 1 and s_lo[i] < hi[i] - 1 for i in range(3)) for s_lo, s_hi in solids):
                bad.add(lid)
                break
    return bad


def _run_edit(out_dir: str, mutate, record: dict, outputs=None, author: str = "") -> dict:
    """Snapshot -> mutate(prj) -> set of dirty line ids -> re-route only those -> QC -> outputs."""
    t0 = time.time()
    prj, routes = load_state(out_dir)
    qcp = os.path.join(out_dir, "qc_report.json")
    before = json.load(open(qcp)) if os.path.exists(qcp) else None
    hist = os.path.join(out_dir, "..", "history")
    os.makedirs(hist, exist_ok=True)
    n = len(os.listdir(hist)) + 1
    snap = os.path.join(hist, f"{n:04d}")
    os.makedirs(snap)
    shutil.copy(os.path.join(out_dir, "model.json"), snap)
    json.dump({**record, "author": author, "time": time.time()}, open(os.path.join(snap, "edit.json"), "w"))
    try:
        dirty = mutate(prj)
    except Exception:
        shutil.rmtree(snap)
        raise
    prj.notes = [x for x in prj.notes if not x.startswith("pipe rack")]
    std = get_profile(prj.standard)
    was_rack = prj.rack.active
    rackmod.finalize(prj, std.equip_clearance)
    from . import layout
    layout.apply_templates(prj)
    layout.auto_nozzles(prj)
    keep = {k: v for k, v in routes.items() if k not in dirty and any(l.id == k for l in prj.lines)}
    if prj.rack.active != was_rack:
        keep = {}
    keep = {k: v for k, v in keep.items() if k not in _invalid_by_geometry(prj, keep, std, set())}
    res = pipeline.run(prj, out_dir, outputs or OUTPUTS_DEFAULT, prj.standard, keep=keep)
    rerouted = [k for k, r in res.routes.items() if k not in keep]
    return {"version": n, "rerouted": sorted(rerouted), "kept": len(keep), "seconds": round(time.time() - t0, 2),
            "status": res.status, "errors": res.qc.errors, "warnings": res.qc.warnings,
            "errors_before": None if before is None else before["errors"]}


def edit_dir(out_dir: str, ops: list[dict], outputs=None, author: str = "") -> dict:
    """Apply `ops` to the model saved in out_dir, re-route what changed, re-run QC and rewrite outputs."""
    return _run_edit(out_dir, lambda prj: apply_ops(prj, ops), {"ops": ops}, outputs, author)


class SpecImportError(ValueError):
    def __init__(self, report):
        super().__init__("; ".join(f"{i.table}{'' if i.row is None else ' row ' + str(i.row)}: {i.message}" for i in report.errors[:5]))
        self.report = report


def import_spec_dir(out_dir: str, name: str, data: bytes | None, outputs=None, author: str = "") -> dict:
    """Replace the project's piping spec (data=None -> back to the built-in generic catalogue).
    Only lines whose pipe size / elbow / valve dimensions change are re-routed; QC re-runs on everything."""
    import hashlib
    from . import spec as specmod
    report = None
    if data is not None:
        new_specs, report = specmod.load_spec(name, data)
        if not report.ok:
            raise SpecImportError(report)
    else:
        new_specs = {}

    def mutate(prj):
        old = {l.id: _sig(prj, l) for l in prj.lines}
        prj.specs = new_specs
        prj.spec_source = ({"name": name, "sha1": hashlib.sha1(data).hexdigest()[:12], "imported_at": time.time(),
                            "classes": report.classes, "warnings": [i.model_dump() for i in report.warnings]}
                           if data is not None else {})
        return _descendants(prj, {l.id for l in prj.lines if _sig(prj, l) != old[l.id]})

    out = _run_edit(out_dir, mutate, {"ops": [{"op": "import_spec", "file": name if data is not None else None}]}, outputs, author)
    out["spec_warnings"] = 0 if report is None else len(report.warnings)
    return out


def set_drawing_standard_dir(out_dir: str, data: dict, outputs=None, author: str = "") -> dict:
    """Replace the drawing standard (title block, sheet, conventions). Geometry is untouched, drawings regenerate."""
    from .drawing.standard import DrawingStandard
    try:
        std = DrawingStandard(**data)
    except Exception as e:                      # pydantic validation error -> readable message
        raise EditError(f"invalid drawing standard: {str(e)[:300]}")

    def mutate(prj):
        prj.drawing = std
        return set()
    return _run_edit(out_dir, mutate, {"ops": [{"op": "drawing_standard", "name": std.name}]}, outputs, author)


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

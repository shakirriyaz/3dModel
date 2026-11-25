"""End-to-end pipeline: project -> layout -> routing -> QC -> exports."""
from __future__ import annotations
import json, os, time
from dataclasses import dataclass, field
from .model import Project, Route, QCReport
from .standards import get_profile
from . import layout, routing, qc, bom, rack

ALL_OUTPUTS = ["ifc", "dxf", "iso_pdf", "iso_svg", "bom_csv", "qc_report", "viewer"]


@dataclass
class RunResult:
    project: Project
    routes: dict
    qc: QCReport
    files: list = field(default_factory=list)
    seconds: float = 0.0

    @property
    def status(self):
        return "PASSED" if self.qc.passed else "FAILED"


def build_model(prj: Project, standard: str | None = None):
    std = get_profile(standard or prj.standard)
    layout.apply_templates(prj)
    layout.place_equipment(prj, std)
    layout.auto_nozzles(prj)
    rack.finalize(prj, std.equip_clearance)
    router = routing.Router(prj, std)
    routes = router.route_all()
    report = qc.run_qc(prj, routes, std)
    return std, routes, report


def run(prj: Project, outdir: str, outputs=None, standard: str | None = None) -> RunResult:
    from .export import ifc as x_ifc, dxf as x_dxf, iso as x_iso, scene as x_scene
    t0 = time.time()
    outputs = list(outputs or ALL_OUTPUTS)
    os.makedirs(outdir, exist_ok=True)
    std, routes, report = build_model(prj, standard)
    rows = bom.build_bom(prj, routes)
    stamp = "QC PASSED" if report.passed else "DRAFT - QC FAILED"
    if not std.validated:
        stamp += " (UNVALIDATED DESIGN RULES)"
    res = RunResult(prj, routes, report)

    def out(name):
        p = os.path.join(outdir, name)
        res.files.append(name)
        return p

    x_scene.write_scene_json(prj, routes, out("scene.json"), stamp, report, rows)
    with open(out("model.json"), "w") as f:
        json.dump({"project": prj.model_dump(by_alias=True),
                   "routes": {k: v.model_dump() for k, v in routes.items()}}, f, indent=1)
    if "ifc" in outputs:
        x_ifc.write_ifc(prj, routes, out("plant.ifc"))
    if "dxf" in outputs:
        x_dxf.write_dxf(prj, routes, out("plant.dxf"), stamp)
    if "iso_svg" in outputs:
        os.makedirs(os.path.join(outdir, "iso"), exist_ok=True)
        for name in x_iso.write_svgs(prj, routes, os.path.join(outdir, "iso"), stamp):
            res.files.append("iso/" + name)
    if "iso_pdf" in outputs:
        x_iso.write_pdf(prj, routes, rows, out("isometrics.pdf"), stamp)
    if "bom_csv" in outputs:
        with open(out("bom.csv"), "w") as f:
            f.write(bom.bom_csv(rows))
    if "qc_report" in outputs:
        with open(out("qc_report.json"), "w") as f:
            f.write(report.model_dump_json(indent=1))
        with open(out("qc_report.md"), "w") as f:
            f.write(_qc_md(prj, report, stamp))
    if "viewer" in outputs:
        x_scene.write_viewer(prj, routes, out("viewer.html"), stamp)
    res.seconds = time.time() - t0
    return res


def _qc_md(prj, r: QCReport, stamp):
    lines = [f"# QC report - {prj.name}", "", f"**Status: {stamp}**", "",
             f"Standard profile: `{r.standard}` (validated: {r.standard_validated})", "",
             f"Errors: {r.errors}  Warnings: {r.warnings}", "", "Checks run: " + ", ".join(r.checks_run), ""]
    if r.items:
        lines += ["| Severity | Check | Subject | Message |", "|---|---|---|---|"]
        lines += [f"| {i.severity} | {i.check} | {i.subject} | {i.message} |" for i in r.items]
    else:
        lines.append("No findings.")
    return "\n".join(lines) + "\n"

from __future__ import annotations
import argparse, sys
from .io_neutral import load_project
from .pipeline import run, ALL_OUTPUTS
from .standards import PROFILES


def main(argv=None):
    ap = argparse.ArgumentParser("autoplant", description="Automated plant 3D design pipeline")
    ap.add_argument("input", help="project .json (neutral) or DEXPI Proteus .xml")
    ap.add_argument("-o", "--out", default="out", help="output directory")
    ap.add_argument("--standard", default=None, help=f"profile name {sorted(PROFILES)} or JSON path")
    ap.add_argument("--spec", default=None, help="piping spec: folder/.zip of CSVs, .xlsx or .json (see examples/spec_sample)")
    ap.add_argument("--outputs", default=",".join(ALL_OUTPUTS), help="comma list: " + ",".join(ALL_OUTPUTS))
    a = ap.parse_args(argv)
    if a.input.lower().endswith(".xml"):
        from .io_dexpi import load_dexpi
        prj = load_dexpi(a.input)
    else:
        prj = load_project(a.input)
    if a.spec:
        from . import spec as specmod
        specs, rep = specmod.load_spec_path(a.spec)
        for i in rep.issues:
            print(f"  spec {i.severity}: {i.table}{'' if i.row is None else ' row ' + str(i.row)}: {i.message}")
        if not rep.ok:
            print("spec import failed - nothing run")
            return 2
        prj.specs = specs
        prj.spec_source = {"name": a.spec, "classes": rep.classes}
    res = run(prj, a.out, a.outputs.split(","), a.standard)
    print(f"QC {res.status}: {res.qc.errors} errors, {res.qc.warnings} warnings  ({res.seconds:.1f}s)")
    nr = sum(1 for r in res.routes.values() if r.note == "rack")
    print(f"pipe rack: {'active' if res.project.rack.active else 'not used'}, {nr} lines routed via rack")
    for n in res.project.notes:
        print("  note:", n)
    for i in res.qc.items[:30]:
        print(f"  [{i.severity}] {i.check} {i.subject}: {i.message}")
    for k, why in res.skipped.items():
        print(f"  skipped {k}: {why}")
    print("files:", ", ".join(res.files))
    return 0 if res.qc.passed else 2


if __name__ == "__main__":
    sys.exit(main())

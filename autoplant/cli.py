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
    ap.add_argument("--outputs", default=",".join(ALL_OUTPUTS), help="comma list: " + ",".join(ALL_OUTPUTS))
    a = ap.parse_args(argv)
    if a.input.lower().endswith(".xml"):
        from .io_dexpi import load_dexpi
        prj = load_dexpi(a.input)
    else:
        prj = load_project(a.input)
    res = run(prj, a.out, a.outputs.split(","), a.standard)
    print(f"QC {res.status}: {res.qc.errors} errors, {res.qc.warnings} warnings  ({res.seconds:.1f}s)")
    for i in res.qc.items[:30]:
        print(f"  [{i.severity}] {i.check} {i.subject}: {i.message}")
    print("files:", ", ".join(res.files))
    return 0 if res.qc.passed else 2


if __name__ == "__main__":
    sys.exit(main())

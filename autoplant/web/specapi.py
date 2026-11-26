"""Spec endpoints shared by one-off jobs and team projects."""
from __future__ import annotations
import io, json, os, zipfile
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from .. import editing, spec as specmod
from ..model import Project

SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "spec_sample"


def import_spec(out_dir: str, name: str, data: bytes | None, author: str, outputs=None) -> dict:
    try:
        return editing.import_spec_dir(out_dir, name, data, outputs=outputs, author=author)
    except editing.SpecImportError as e:
        raise HTTPException(422, {"message": "the spec file has errors - nothing was imported",
                                  "issues": [i.model_dump() for i in e.report.issues]})


def summary(out_dir: str) -> dict:
    prj = Project(**json.load(open(os.path.join(out_dir, "model.json")))["project"])
    used = {}
    for l in prj.lines:
        used[l.spec] = used.get(l.spec, 0) + 1
    return {"imported": bool(prj.specs), "source": prj.spec_source, "lines_per_class": used,
            "classes": prj.spec_source.get("classes", {}),
            "unknown_classes": sorted(c for c in used if prj.specs and c not in prj.specs)}


def export(out_dir: str) -> JSONResponse:
    prj = Project(**json.load(open(os.path.join(out_dir, "model.json")))["project"])
    if not prj.specs:
        raise HTTPException(404, "no spec imported - the built-in generic catalogue is in use")
    return JSONResponse(specmod.export_json(prj.specs), headers={"Content-Disposition": "attachment; filename=piping_spec.json"})


def template_zip() -> StreamingResponse:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(SAMPLE.glob("*.csv")):
            z.write(f, f.name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=spec_template.zip"})

"""Web app: upload project -> automatic run -> QC verdict, downloads, 3D viewer."""
from __future__ import annotations
import io, json, os, shutil, tempfile, uuid, zipfile
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from .. import editing
from ..io_neutral import load_project
from ..pipeline import run, ALL_OUTPUTS
from ..standards import PROFILES

ROOT = Path(os.environ.get("AUTOPLANT_RUNS", tempfile.gettempdir())) / "autoplant_runs"
ROOT.mkdir(parents=True, exist_ok=True)
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="AutoPlant")
from .projects import router as projects_router
app.include_router(projects_router)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/workspace/p/{pid}", response_class=HTMLResponse)
def workspace_project(pid: int):
    return (STATIC / "workspace.html").read_text().replace("__MODE__", "project").replace("__ID__", str(pid))


@app.get("/workspace/{job}", response_class=HTMLResponse)
def workspace(job: str):
    _out(job)
    return (STATIC / "workspace.html").read_text().replace("__MODE__", "job").replace("__ID__", job)


@app.get("/api/options")
def options():
    return {"standards": {k: {"description": v.description, "validated": v.validated} for k, v in PROFILES.items()},
            "outputs": ALL_OUTPUTS,
            "examples": sorted(p.name for p in EXAMPLES.glob("*") if p.suffix in (".json", ".xml"))}


@app.post("/api/run")
def run_job(file: UploadFile | None = File(None), example: str = Form(""), standard: str = Form("generic"),
            outputs: str = Form(",".join(ALL_OUTPUTS))):
    try:
        if file is not None and file.filename:
            data = file.file.read()
            name = file.filename
        elif example:
            p = EXAMPLES / Path(example).name
            if not p.is_file():
                raise HTTPException(404, "unknown example")
            data, name = p.read_bytes(), p.name
        else:
            raise HTTPException(400, "provide a file or an example")
        jid = uuid.uuid4().hex[:12]
        jdir = ROOT / jid
        jdir.mkdir()
        if name.lower().endswith(".xml"):
            from ..io_dexpi import load_dexpi
            src = jdir / "input.xml"
            src.write_bytes(data)
            prj = load_dexpi(str(src))
        else:
            prj = load_project(json.loads(data))
        outs = [o for o in outputs.split(",") if o in ALL_OUTPUTS]
        res = run(prj, str(jdir / "out"), outs, standard)
    except HTTPException:
        raise
    except Exception as e:   # surface input problems to the user
        raise HTTPException(422, f"{type(e).__name__}: {e}")
    return {"job": jid, "status": res.status, "seconds": round(res.seconds, 2), "files": res.files,
            "qc": json.loads(res.qc.model_dump_json())}


def _out(job: str) -> Path:
    if not job.isalnum():
        raise HTTPException(400, "bad job id")
    d = ROOT / job / "out"
    if not d.is_dir():
        raise HTTPException(404, "unknown job")
    return d


@app.get("/api/jobs/{job}/file/{path:path}")
def get_file(job: str, path: str):
    d = _out(job).resolve()
    f = (d / path).resolve()
    if d not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


@app.get("/api/jobs/{job}/zip")
def get_zip(job: str):
    d = _out(job)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d))
    buf.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename=autoplant_{job}.zip"})


class EditBody(BaseModel):
    ops: list[dict]


@app.post("/api/jobs/{job}/edit")
def edit_job(job: str, body: EditBody):
    d = _out(job)
    try:
        return editing.edit_dir(str(d), body.ops)
    except (editing.EditError, KeyError, ValueError) as e:
        raise HTTPException(422, f"{type(e).__name__}: {e}")


@app.post("/api/jobs/{job}/undo")
def undo_job(job: str):
    d = _out(job)
    try:
        return editing.undo_dir(str(d))
    except editing.EditError as e:
        raise HTTPException(409, str(e))


@app.get("/api/jobs/{job}/history")
def job_history(job: str):
    return editing.history(str(_out(job)))


def main():
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()

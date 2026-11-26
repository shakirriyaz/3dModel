"""Multi-user project API: auth, projects, members, revisions (optimistic locking), markups, clash review."""
from __future__ import annotations
import io, json, os, shutil, tempfile, zipfile
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from .. import editing, clash
from . import specapi
from ..db import Store
from ..io_neutral import load_project
from ..model import QCReport
from ..pipeline import run, ALL_OUTPUTS

router = APIRouter()
RANK = {"viewer": 0, "editor": 1, "admin": 2}
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
DEFAULT_OUTPUTS = [o for o in ALL_OUTPUTS if o != "step"]


def store(request: Request) -> Store:
    st = getattr(request.app.state, "store", None)
    if st is None:
        st = request.app.state.store = Store(os.environ.get("AUTOPLANT_DATA", os.path.join(tempfile.gettempdir(), "autoplant_data")))
    return st


def user(request: Request, st: Store = Depends(store)) -> dict:
    auth = request.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else request.cookies.get("ap_token")
    u = st.user_for_token(tok)
    if not u:
        raise HTTPException(401, "login required")
    return u


def need(st: Store, pid: int, u: dict, level: str) -> str:
    role = st.role_in(pid, u)
    if role is None:
        raise HTTPException(404, "unknown project")        # do not reveal other people's projects
    if RANK[role] < RANK[level]:
        raise HTTPException(403, f"{level} access required")
    return role


# ---------------------------------------------------------------- auth
class Creds(BaseModel):
    username: str
    password: str
    role: str = "editor"


@router.post("/api/auth/register")
def register(c: Creds, request: Request, st: Store = Depends(store)):
    """First user becomes admin. Afterwards only an admin can create users."""
    if st.user_count() == 0:
        role = "admin"
    else:
        auth = request.headers.get("authorization", "")
        me = st.user_for_token(auth[7:] if auth.lower().startswith("bearer ") else request.cookies.get("ap_token"))
        if not me or me["role"] != "admin":
            raise HTTPException(403, "only an admin can create users")
        role = c.role
    try:
        return st.create_user(c.username, c.password, role)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/api/auth/login")
def login(c: Creds, response: Response, st: Store = Depends(store)):
    r = st.login(c.username, c.password)
    if not r:
        raise HTTPException(401, "wrong username or password")
    tok, u = r
    response.set_cookie("ap_token", tok, httponly=True, samesite="lax", max_age=12 * 3600)
    return {"token": tok, "user": u}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, st: Store = Depends(store)):
    auth = request.headers.get("authorization", "")
    st.logout(auth[7:] if auth.lower().startswith("bearer ") else request.cookies.get("ap_token") or "")
    response.delete_cookie("ap_token")
    return {"ok": True}


@router.get("/api/auth/me")
def me(u: dict = Depends(user), st: Store = Depends(store)):
    return {**u, "first_run": False}


@router.get("/api/auth/status")
def auth_status(st: Store = Depends(store)):
    return {"needs_first_user": st.user_count() == 0}


@router.get("/api/users")
def users(u: dict = Depends(user), st: Store = Depends(store)):
    if u["role"] != "admin":
        raise HTTPException(403, "admin only")
    return st.list_users()


# ---------------------------------------------------------------- projects
@router.get("/api/projects")
def list_projects(u: dict = Depends(user), st: Store = Depends(store)):
    return st.projects_for(u)


@router.post("/api/projects")
def create_project(name: str = Form(""), file: UploadFile | None = File(None), example: str = Form(""),
                   standard: str = Form("generic"), u: dict = Depends(user), st: Store = Depends(store)):
    if u["role"] == "viewer":
        raise HTTPException(403, "viewers cannot create projects")
    if file is not None and file.filename:
        data, fname = file.file.read(), file.filename
    elif example:
        p = EXAMPLES / Path(example).name
        if not p.is_file():
            raise HTTPException(404, "unknown example")
        data, fname = p.read_bytes(), p.name
    else:
        raise HTTPException(400, "provide a file or an example")
    pid = st.create_project(name.strip() or fname, u["id"])
    pdir = st.project_dir(pid)
    try:
        if fname.lower().endswith(".xml"):
            from ..io_dexpi import load_dexpi
            src = os.path.join(pdir, "input.xml")
            open(src, "wb").write(data)
            prj = load_dexpi(src)
        else:
            prj = load_project(json.loads(data))
        res = run(prj, os.path.join(pdir, "out"), DEFAULT_OUTPUTS, standard)
    except Exception as e:
        shutil.rmtree(pdir, ignore_errors=True)
        with st.conn() as c:
            c.execute("delete from projects where id=?", (pid,))
        raise HTTPException(422, f"{type(e).__name__}: {e}")
    summary = {"status": res.status, "errors": res.qc.errors, "warnings": res.qc.warnings, "seconds": round(res.seconds, 2)}
    rev = st.add_revision(pid, u["id"], "create", f"created from {fname}", summary, os.path.join(pdir, "out", "model.json"))
    st.audit(pid, u["id"], "create", fname)
    return {"id": pid, "rev": rev, **summary}


@router.get("/api/projects/{pid}")
def get_project(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    role = need(st, pid, u, "viewer")
    return {**st.project(pid), "my_role": role, "members": st.members(pid)}


class Member(BaseModel):
    username: str
    role: str = "viewer"


@router.post("/api/projects/{pid}/members")
def add_member(pid: int, m: Member, u: dict = Depends(user), st: Store = Depends(store)):
    p = st.project(pid)
    if p is None or st.role_in(pid, u) is None:
        raise HTTPException(404, "unknown project")           # never reveal other people's projects
    if u["role"] != "admin" and p["owner_id"] != u["id"]:
        raise HTTPException(403, "only the owner or an admin can manage members")
    target = st.user_by_name(m.username)
    if not target:
        raise HTTPException(404, "unknown user")
    try:
        st.set_member(pid, target["id"], m.role)
    except ValueError as e:
        raise HTTPException(422, str(e))
    st.audit(pid, u["id"], "member", f"{m.username}={m.role}")
    return st.members(pid)


@router.get("/api/projects/{pid}/head")
def head(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    return {"rev": st.project(pid)["current_rev"]}


@router.get("/api/projects/{pid}/revisions")
def revisions(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    return st.revisions(pid)


class EditBody(BaseModel):
    base_rev: int
    ops: list[dict]
    comment: str = ""


@router.post("/api/projects/{pid}/edit")
def edit(pid: int, body: EditBody, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "editor")
    with st.plock(pid):
        cur = st.project(pid)["current_rev"]
        if body.base_rev != cur:
            raise HTTPException(409, {"message": "someone else changed this project - reload and retry", "current_rev": cur})
        out = os.path.join(st.project_dir(pid), "out")
        try:
            result = editing.edit_dir(out, body.ops, outputs=DEFAULT_OUTPUTS, author=u["username"])
        except (editing.EditError, KeyError, ValueError) as e:
            raise HTTPException(422, f"{type(e).__name__}: {e}")
        rev = st.add_revision(pid, u["id"], "edit", body.comment, result, os.path.join(out, "model.json"))
        st.audit(pid, u["id"], "edit", json.dumps(body.ops)[:1500])
    return {"rev": rev, **result}


class Restore(BaseModel):
    rev: int
    base_rev: int


@router.post("/api/projects/{pid}/restore")
def restore(pid: int, body: Restore, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "editor")
    with st.plock(pid):
        cur = st.project(pid)["current_rev"]
        if body.base_rev != cur:
            raise HTTPException(409, {"message": "project changed - reload and retry", "current_rev": cur})
        snap = os.path.join(st.project_dir(pid), "revs", str(body.rev), "model.json")
        if not os.path.exists(snap):
            raise HTTPException(404, "unknown revision")
        out = os.path.join(st.project_dir(pid), "out")
        shutil.copy(snap, os.path.join(out, "model.json"))
        prj, routes = editing.load_state(out)
        res = run(prj, out, DEFAULT_OUTPUTS, prj.standard, keep=routes)
        summary = {"status": res.status, "errors": res.qc.errors, "warnings": res.qc.warnings, "restored_from": body.rev}
        rev = st.add_revision(pid, u["id"], "restore", f"restored revision {body.rev}", summary, os.path.join(out, "model.json"))
        st.audit(pid, u["id"], "restore", str(body.rev))
    return {"rev": rev, **summary}


@router.post("/api/projects/{pid}/spec")
def project_spec_import(pid: int, base_rev: int = Form(...), comment: str = Form(""), file: UploadFile | None = File(None),
                        clear: bool = Form(False), u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "editor")
    if file is None and not clear:
        raise HTTPException(400, "provide a spec file or clear=true")
    with st.plock(pid):
        cur = st.project(pid)["current_rev"]
        if base_rev != cur:
            raise HTTPException(409, {"message": "someone else changed this project - reload and retry", "current_rev": cur})
        out = os.path.join(st.project_dir(pid), "out")
        name = file.filename if file is not None else ""
        result = specapi.import_spec(out, name or "spec", None if clear else file.file.read(), author=u["username"], outputs=DEFAULT_OUTPUTS)
        rev = st.add_revision(pid, u["id"], "spec", comment or ("spec cleared" if clear else f"imported spec {name}"), result,
                              os.path.join(out, "model.json"))
        st.audit(pid, u["id"], "spec", name or "cleared")
    return {"rev": rev, **result}


@router.get("/api/projects/{pid}/spec")
def project_spec(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    return specapi.summary(os.path.join(st.project_dir(pid), "out"))


@router.get("/api/projects/{pid}/spec/export")
def project_spec_export(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    return specapi.export(os.path.join(st.project_dir(pid), "out"))


def _file(st, pid, u, path):
    need(st, pid, u, "viewer")
    d = Path(st.project_dir(pid), "out").resolve()
    f = (d / path).resolve()
    if d not in f.parents or not f.is_file():
        raise HTTPException(404)
    return f


@router.get("/api/projects/{pid}/file/{path:path}")
def project_file(pid: int, path: str, u: dict = Depends(user), st: Store = Depends(store)):
    return FileResponse(_file(st, pid, u, path))


@router.get("/api/projects/{pid}/zip")
def project_zip(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    d = Path(st.project_dir(pid), "out")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename=project_{pid}.zip"})


@router.get("/api/projects/{pid}/audit")
def audit(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "editor")
    return st.audit_log(pid)


# ---------------------------------------------------------------- markups
class MarkupIn(BaseModel):
    element: str = ""
    xyz: list[float] | None = None
    text: str


class CommentIn(BaseModel):
    text: str


class StatusIn(BaseModel):
    status: str


@router.get("/api/projects/{pid}/markups")
def markups(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    return st.markups(pid)


@router.post("/api/projects/{pid}/markups")
def add_markup(pid: int, m: MarkupIn, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")        # viewers may raise review comments; they cannot edit the model
    if not m.text.strip():
        raise HTTPException(422, "text required")
    mid = st.add_markup(pid, st.project(pid)["current_rev"], m.element, m.xyz, m.text.strip(), u["id"])
    st.audit(pid, u["id"], "markup", f"{m.element}: {m.text[:200]}")
    return {"id": mid}


@router.post("/api/projects/{pid}/markups/{mid}/comment")
def comment(pid: int, mid: int, c: CommentIn, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    try:
        st.comment_markup(pid, mid, u["id"], c.text.strip())
    except KeyError:
        raise HTTPException(404, "unknown markup")
    return {"ok": True}


@router.post("/api/projects/{pid}/markups/{mid}/status")
def markup_status(pid: int, mid: int, s: StatusIn, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "editor")
    try:
        st.set_markup_status(pid, mid, s.status, u["id"])
    except KeyError:
        raise HTTPException(404, "unknown markup")
    except ValueError as e:
        raise HTTPException(422, str(e))
    st.audit(pid, u["id"], "markup_status", f"{mid}={s.status}")
    return {"ok": True}


# ---------------------------------------------------------------- clash review
def _report(st, pid) -> QCReport:
    p = Path(st.project_dir(pid), "out", "qc_report.json")
    return QCReport(**json.loads(p.read_text()))


@router.get("/api/projects/{pid}/clashes")
def clashes(pid: int, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    return clash.rows(_report(st, pid), st.clash_status(pid))


class Waive(BaseModel):
    key: str
    status: str = "waived"
    comment: str = ""


@router.post("/api/projects/{pid}/clashes/status")
def clash_status(pid: int, w: Waive, u: dict = Depends(user), st: Store = Depends(store)):
    """Waive a soft clash (warning) with a mandatory comment, or re-open it. Hard clashes (errors) cannot be waived."""
    need(st, pid, u, "editor")
    item = next((i for i in _report(st, pid).items if i.key == w.key), None)
    if item is None:
        raise HTTPException(404, "no such finding in the current revision")
    if w.status == "waived":
        if item.severity == "error":
            raise HTTPException(422, "hard clashes (errors) cannot be waived - fix the model")
        if not w.comment.strip():
            raise HTTPException(422, "a comment is required to waive a finding")
    elif w.status != "open":
        raise HTTPException(422, "status must be waived or open")
    st.set_clash_status(pid, w.key, w.status, w.comment.strip(), u["id"])
    st.audit(pid, u["id"], "clash_" + w.status, f"{w.key} {w.comment[:200]}")
    return {"ok": True}


@router.get("/api/projects/{pid}/clash_report.{ext}")
def clash_report(pid: int, ext: str, u: dict = Depends(user), st: Store = Depends(store)):
    need(st, pid, u, "viewer")
    rs = clash.rows(_report(st, pid), st.clash_status(pid))
    name = st.project(pid)["name"]
    if ext == "csv":
        return PlainTextResponse(clash.to_csv(rs), media_type="text/csv")
    if ext == "html":
        stamp = "QC PASSED" if _report(st, pid).passed else "QC FAILED"
        return HTMLResponse(clash.to_html(rs, name, stamp))
    raise HTTPException(404)

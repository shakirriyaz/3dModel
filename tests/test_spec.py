import io, json, os, zipfile
import openpyxl, pytest
from fastapi.testclient import TestClient
from autoplant import spec as sp
from autoplant.db import Store
from autoplant.io_neutral import load_project
from autoplant.pipeline import run, build_model
from autoplant.web.app import app

EX = os.path.join(os.path.dirname(__file__), "..", "examples")
SAMPLE = os.path.join(EX, "spec_sample")


def make_zip(**tables) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in tables.items():
            z.writestr(name + ".csv", text)
    return buf.getvalue()


MIN = dict(classes="class,description,rating,material,elbow_type\nA1,test,150#,CS,LR\n",
           pipe="class,nps,od_mm,wall_mm,schedule\nA1,4,114.3,6.02,STD\nA1,6,168.3,7.11,STD\n")


def sample_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in os.listdir(SAMPLE):
            z.write(os.path.join(SAMPLE, f), f)
    return buf.getvalue()


# ------------------------------------------------------------------ import formats
def test_sample_imports_in_every_format(tmp_path):
    specs, rep = sp.load_spec_path(SAMPLE)
    assert rep.ok and set(specs) == {"A1", "B2", "S1"} and rep.classes["A1"]["pipe"] == 11
    z_specs, z_rep = sp.load_spec("spec.zip", sample_bytes())
    assert z_rep.ok and z_specs["B2"].pipe[0].wall == specs["B2"].pipe[0].wall
    j_specs, j_rep = sp.load_spec("spec.json", json.dumps(sp.export_json(specs)).encode())
    assert j_rep.ok and {k: v.model_dump() for k, v in j_specs.items()} == {k: v.model_dump() for k, v in specs.items()}   # lossless round trip
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for t, rows in sp.export_json(specs).items():
        ws = wb.create_sheet(t)
        cols = list(rows[0])
        ws.append(cols)
        for r in rows:
            ws.append([r[c] for c in cols])
    buf = io.BytesIO(); wb.save(buf)
    x_specs, x_rep = sp.load_spec("spec.xlsx", buf.getvalue())
    assert x_rep.ok and x_specs["A1"].valves[0].ftf == specs["A1"].valves[0].ftf and len(x_specs["B2"].branches) == len(specs["B2"].branches)


def test_dn_columns_and_aliases():
    z = make_zip(classes="Piping Class,Rating\nX1,300#\n", pipe="class,DN,OD,WT\nX1,100,114.3,6.02\n")
    specs, rep = sp.load_spec("s.zip", z)
    assert rep.ok and specs["X1"].pipe[0].nps == 4 and specs["X1"].rating == "300#"


@pytest.mark.parametrize("tables,needle", [
    (dict(classes="class\nA1\n"), "table 'pipe' is missing"),
    (dict(MIN, pipe=MIN["pipe"] + "A1,4,114.3,6.02,STD\n"), "duplicate pipe row"),
    (dict(MIN, pipe="class,nps,od_mm,wall_mm\nA1,4,114.3,60\n"), "not smaller than half the OD"),
    (dict(MIN, pipe="class,nps,od_mm,wall_mm\nZZ,4,114.3,6\n"), "unknown class 'ZZ'"),
    (dict(MIN, valves="class,type,nps,ftf\nA1,gate,4,abc\n"), "not a number"),
    (dict(MIN, valves="class,type,nps,ftf\nA1,,4,229\n"), "valve 'type' is required"),
    (dict(MIN, branches="class,header_nps,branch_nps,type\nA1,6,4,flange\n"), "unknown branch type"),
    (dict(MIN, classes="class,elbow_type\nA1,XX\n"), "elbow_type must be LR or SR"),
    (dict(MIN, classes="class\nA1\nA1\n"), "duplicate class"),
])
def test_validation_rejects_bad_specs(tables, needle):
    specs, rep = sp.load_spec("bad.zip", make_zip(**tables))
    assert not rep.ok and specs == {}
    assert any(needle in i.message for i in rep.errors), [i.message for i in rep.issues]


def test_bad_files():
    assert not sp.load_spec("x.txt", b"hello")[1].ok
    assert not sp.load_spec("x.zip", b"not a zip")[1].ok
    assert not sp.load_spec("x.json", b"{bad")[1].ok


# ------------------------------------------------------------------ the spec changes the design
def project_with(specs_zip, spec_code="A1", nps=4.0, valves=None, branch=None):
    prj = load_project(f"{EX}/separator_skid.json")
    for l in prj.lines:
        l.spec = spec_code
    if specs_zip is not None:
        prj.specs = sp.load_spec("s.zip", specs_zip)[0]
    return prj


def test_spec_dimensions_drive_the_geometry():
    prj = project_with(sample_bytes())
    ln = prj.lines[0]
    assert prj.dims(ln).takeoff == pytest.approx(1.5 * ln.nps * 25.4)      # explicit LR row from the spec
    assert prj.spec_view("S1").takeoff(6) == pytest.approx(1.0 * 6 * 25.4) # derived SR for S1
    # a spec whose 4" pipe has a different OD changes the model OD and the BOM
    z = make_zip(**dict(MIN, pipe="class,nps,od_mm,wall_mm,schedule\nA1,4,100,5,X\nA1,6,168.3,7.11,STD\nA1,3,88.9,5.49,STD\n"))
    prj = project_with(z)
    assert prj.dims(next(l for l in prj.lines if l.nps == 4)).od == 100


def test_unknown_spec_size_and_valve_are_qc_errors(tmp_path):
    prj = project_with(sample_bytes())
    prj.lines[0].spec = "NOPE"
    prj.lines[1].nps = 14.0                                  # not in class A1 (max NPS 12)
    prj.lines[2].spec, prj.lines[2].valves = "B2", ["ball"]  # B2 has only gate + check valves
    _, _, rep = build_model(prj)
    checks = {(i.check, i.subject) for i in rep.items if i.severity == "error"}
    assert ("spec_unknown", prj.lines[0].id) in checks
    assert ("size_not_in_spec", prj.lines[1].id) in checks
    assert ("valve_not_in_spec", prj.lines[2].id) in checks and not rep.passed


def _tee_project():
    prj = load_project(f"{EX}/three_units_rack.json")
    hdr = prj.lines[0]
    prj.lines.append(type(hdr)(id="BR1", **{"from": "U0-3"}, to="@" + hdr.id, nps=2.0, spec="A1"))
    for l in prj.lines:
        l.spec, l.valves = "A1", []
        l.nps = 2.0 if l.id == "BR1" else (6.0 if l.id == hdr.id else 3.0)
    return prj, hdr


def _spec_with(branches_csv):
    t = dict(MIN, pipe="class,nps,od_mm,wall_mm\nA1,6,168.3,7.11\nA1,2,60.3,3.91\nA1,3,88.9,5.49\n",
             valves="class,type,nps,ftf\nA1,gate,6,267\n")
    if branches_csv:
        t["branches"] = branches_csv
    specs, rep = sp.load_spec("a.zip", make_zip(**t))
    assert rep.ok, rep.errors
    return specs


def test_branch_table_is_enforced():
    # (a) table has the 6x2 entry -> allowed
    prj, hdr = _tee_project()
    prj.specs = _spec_with("class,header_nps,branch_nps,type\nA1,6,2,weldolet\n")
    assert not [i for i in build_model(prj)[2].items if i.check == "branch_not_in_spec"]
    # (b) table exists but has no 6x2 entry -> error
    prj, hdr = _tee_project()
    prj.specs = _spec_with("class,header_nps,branch_nps,type\nA1,6,3,tee\n")
    errs = [i for i in build_model(prj)[2].items if i.check == "branch_not_in_spec"]
    assert errs and errs[0].subject == "BR1" and "no 6x2 entry" in errs[0].message
    # (c) table explicitly forbids it -> error
    prj, hdr = _tee_project()
    prj.specs = _spec_with("class,header_nps,branch_nps,type\nA1,6,2,none\n")
    errs = [i for i in build_model(prj)[2].items if i.check == "branch_not_in_spec"]
    assert errs and "does not allow" in errs[0].message
    # (d) the branch table drives the BOM item
    prj, hdr = _tee_project()
    prj.specs = _spec_with("class,header_nps,branch_nps,type,code,description\nA1,6,2,weldolet,OLET-6x2,Weldolet 6x2\n")
    from autoplant import bom
    std, routes, rep = build_model(prj)
    assert any(r["code"] == "OLET-6x2" and r["kind"] == "branch" for r in bom.build_mto(prj, routes))


def test_mto_matches_bom_and_uses_spec_codes(tmp_path):
    prj = project_with(sample_bytes())
    res = run(prj, str(tmp_path), ["bom_csv", "qc_report"])
    import csv
    mto = list(csv.DictReader(open(tmp_path / "mto.csv")))
    bom = list(csv.DictReader(open(tmp_path / "bom.csv")))
    assert any(r["code"].startswith("PIPE-A1-") for r in mto) and any(r["code"].startswith("ELB90-A1-") for r in mto)
    assert sum(float(r["qty"]) for r in mto if r["kind"] == "pipe") == pytest.approx(sum(float(r["pipe_cut_length_m"]) for r in bom), abs=0.05)
    assert sum(int(r["qty"]) for r in mto if r["kind"] == "elbow") == sum(int(r["elbows_90_lr"]) for r in bom)
    assert bom[0]["material"] == "ASTM A106 Gr.B" and bom[0]["rating"] == "150#"
    assert any(r["kind"] == "valve" and r["code"].startswith("VLV-") for r in mto)


def test_without_spec_a_warning_is_raised():
    _, _, rep = build_model(load_project(f"{EX}/separator_skid.json"))
    assert any(i.check == "builtin_catalogue" for i in rep.items)


# ------------------------------------------------------------------ API
@pytest.fixture()
def c(tmp_path):
    app.state.store = Store(str(tmp_path / "data"))
    return TestClient(app)


def test_job_api_import_clear_export(c):
    job = c.post("/api/run", data={"example": "separator_skid.json", "standard": "generic", "outputs": "qc_report"}).json()["job"]
    assert c.get(f"/api/jobs/{job}/spec").json()["imported"] is False
    bad = c.post(f"/api/jobs/{job}/spec", files={"file": ("bad.zip", make_zip(classes="class\nA1\n"))})
    assert bad.status_code == 422 and bad.json()["detail"]["issues"] and "nothing was imported" in bad.json()["detail"]["message"]
    assert c.get(f"/api/jobs/{job}/spec").json()["imported"] is False             # failed import changes nothing
    r = c.post(f"/api/jobs/{job}/spec", files={"file": ("sample.zip", sample_bytes())})
    assert r.status_code == 200
    s = c.get(f"/api/jobs/{job}/spec").json()
    assert s["imported"] and set(s["classes"]) == {"A1", "B2", "S1"} and s["source"]["name"] == "sample.zip" and s["unknown_classes"] == []
    exp = c.get(f"/api/jobs/{job}/spec/export")
    assert exp.status_code == 200 and sp.load_spec("e.json", exp.content)[1].ok
    assert c.post(f"/api/jobs/{job}/undo").status_code == 200                      # spec import is undoable
    assert c.get(f"/api/jobs/{job}/spec").json()["imported"] is False
    assert c.post(f"/api/jobs/{job}/spec", files={"file": ("sample.zip", sample_bytes())}).status_code == 200
    assert c.post(f"/api/jobs/{job}/spec/clear").status_code == 200
    assert c.get(f"/api/jobs/{job}/spec").json()["imported"] is False
    z = zipfile.ZipFile(io.BytesIO(c.get("/api/spec/template").content))
    assert {"classes.csv", "pipe.csv", "valves.csv", "branches.csv"} <= set(z.namelist())


def test_project_spec_import_is_a_revision_with_permissions(c):
    def reg(n, role):
        h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": "password123"}).json()["token"]}
        return c.post("/api/auth/register", json={"username": n, "password": "password123", "role": role}, headers=h)
    c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
    reg("ed", "editor"); reg("vw", "viewer")
    tok = lambda n: {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": n, "password": "password123"}).json()["token"]}
    he, hv = tok("ed"), tok("vw")
    pid = c.post("/api/projects", data={"example": "separator_skid.json"}, headers=he).json()["id"]
    c.post(f"/api/projects/{pid}/members", json={"username": "vw", "role": "viewer"}, headers=he)
    files = {"file": ("sample.zip", sample_bytes())}
    assert c.post(f"/api/projects/{pid}/spec", data={"base_rev": 1}, files=files, headers=hv).status_code == 403
    assert c.post(f"/api/projects/{pid}/spec", data={"base_rev": 5}, files={"file": ("sample.zip", sample_bytes())}, headers=he).status_code == 409
    r = c.post(f"/api/projects/{pid}/spec", data={"base_rev": 1, "comment": "company spec"}, files={"file": ("sample.zip", sample_bytes())}, headers=he)
    assert r.status_code == 200 and r.json()["rev"] == 2
    assert c.get(f"/api/projects/{pid}/revisions", headers=hv).json()[0]["kind"] == "spec"
    assert c.get(f"/api/projects/{pid}/spec", headers=hv).json()["imported"] is True
    mto = c.get(f"/api/projects/{pid}/file/mto.csv", headers=hv).text
    assert "PIPE-A1-" in mto


def test_remap_class_codes_edit(tmp_path):
    from autoplant import editing
    prj = load_project(f"{EX}/separator_skid.json")
    for l in prj.lines:
        l.spec = "AS200"                                  # the plant's own code, not in the spec
    run(prj, str(tmp_path / "out"))
    editing.import_spec_dir(str(tmp_path / "out"), "s.zip", sample_bytes())
    q = json.load(open(tmp_path / "out" / "qc_report.json"))
    assert any(i["check"] == "spec_unknown" for i in q["items"])             # all 6 lines flagged
    with pytest.raises(editing.EditError):
        editing.edit_dir(str(tmp_path / "out"), [{"op": "remap_spec", "from": "AS200", "to": "ZZ"}])   # target must exist
    out = editing.edit_dir(str(tmp_path / "out"), [{"op": "remap_spec", "from": "AS200", "to": "A1"}])
    assert out["errors"] == 0 and out["status"] == "PASSED"
    q = json.load(open(tmp_path / "out" / "qc_report.json"))
    assert not any(i["check"] == "spec_unknown" for i in q["items"])

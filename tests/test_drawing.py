import io, json, math, os, xml.etree.ElementTree as ET, zipfile
import ezdxf, fitz, pytest
from fastapi.testclient import TestClient
from autoplant import spec as sp
from autoplant.db import Store
from autoplant.drawing import ga, iso, spools
from autoplant.drawing.canvas import Sheet
from autoplant.drawing.standard import DrawingStandard, get_standard
from autoplant.export import iso as xiso
from autoplant.io_dexpi import load_dexpi
from autoplant.io_neutral import load_project
from autoplant.pipeline import build_model, run
from autoplant.web.app import app

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


def texts(sh: Sheet):
    return [op[3] for op in sh.ops if op[0] == "text"]


@pytest.fixture(scope="module")
def skid():
    prj = load_project(f"{EX}/separator_skid.json")
    prj.specs = sp.load_spec_path(f"{EX}/spec_sample")[0]
    for l in prj.lines:
        l.spec = "A1"
    std, routes, rep = build_model(prj)
    assert rep.passed
    return prj, routes


# ------------------------------------------------------------------ standard
def test_standard_loading(tmp_path):
    assert get_standard(None).iso_sheet == "A3"
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"name": "acme", "company": "ACME", "iso_sheet": "A2", "spool_max_length": 6000}))
    s = get_standard(str(p))
    assert s.company == "ACME" and s.paper("iso") == (594, 420) and s.revision == "A"      # unspecified keys keep defaults
    with pytest.raises(Exception):
        DrawingStandard(iso_sheet="B7")
    with pytest.raises(ValueError):
        get_standard("nope")


# ------------------------------------------------------------------ isometric content
def test_iso_sheet_contents(skid):
    prj, routes = skid
    prj.drawing = DrawingStandard(company="ACME ENG", project_no="P-1", checked_by="JD")
    ln = next(l for l in prj.lines if l.id == "L-1003")
    rt = routes[ln.id]
    sh = iso.build_iso(prj, ln, rt, prj.drawing, True, "QC PASSED", 3, 6)
    t = texts(sh)
    for needle in ("PIPING ISOMETRIC", "ACME ENG", "L-1003", "P-1-ISO-L-1003-03", "3 OF 6", "JD", "MATERIAL LIST", "CUT LIST", "WELD LIST", "NOTES", "N", "QC PASSED"):
        assert any(needle == s or needle in s for s in t), needle
    # every leg length is dimensioned with its true value
    legs = {f"{math.dist(a, b):.0f}" for a, b in zip(rt.points, rt.points[1:])}
    assert legs <= set(t), legs - set(t)
    # spec data reaches the sheet: commodity codes and material list
    assert any(s.startswith("PIPE-A1-4") for s in t) and any(s.startswith("VLV-GATE-A1-4") for s in t)
    # balloons numbered 1..n for the 7 items of this line
    assert {str(i) for i in range(1, 8)} <= set(t)
    # welds numbered sequentially and listed
    fab = spools.breakdown(prj, ln, rt, prj.drawing)
    assert [w["tag"] for w in fab["welds"]] == [f"W{i}" for i in range(1, len(fab["welds"]) + 1)]
    assert all(w["tag"] in t for w in fab["welds"])
    # a failed QC stamps the sheet red
    bad = iso.build_iso(prj, ln, rt, prj.drawing, False, "DRAFT - QC FAILED", 1, 1)
    assert any(op[0] == "text" and op[3] == "DRAFT - QC FAILED" and op[8] == "red" for op in bad.ops)


def test_fabrication_breakdown_is_consistent(skid):
    prj, routes = skid
    for limit in (12000.0, 3500.0, 2000.0):
        std = DrawingStandard(spool_max_length=limit)
        for ln in prj.lines:
            rt = routes[ln.id]
            dm = prj.dims(ln)
            f = spools.breakdown(prj, ln, rt, std)
            L = f["length"]
            bends = len(rt.points) - 2
            valve_span = sum(dm.ftf(v) + 2 * spools.FLANGE_HUB for v, _ in rt.valve_positions)
            assert sum(p["length"] for p in f["pieces"]) + 2 * dm.takeoff * bends + valve_span == pytest.approx(L, abs=len(f["pieces"]))
            assert sum(s["length"] for s in f["spools"]) == pytest.approx(L, abs=len(f["spools"]))
            assert len(f["spools"]) == len(f["breaks"]) + 1 and sum(1 for w in f["welds"] if w["field"]) == len(f["breaks"])
            tags = [w["tag"] for w in f["welds"]]
            assert len(set(tags)) == len(tags)
            for s in f["spools"]:                         # within the limit, or flagged because no feasible break exists
                assert s["length"] <= limit + 1 or s["over_limit"]
            assert not f["spools"][0]["over_limit"] or L > limit
    # a tight limit really introduces field welds
    f = spools.breakdown(prj, next(l for l in prj.lines if l.id == "L-1003"), routes["L-1003"], DrawingStandard(spool_max_length=3000))
    assert len(f["spools"]) >= 2 and any(w["field"] and w["tag"].startswith("FW") for w in f["welds"])


def test_iso_outputs_render_in_all_formats(skid, tmp_path):
    prj, routes = skid
    prj.drawing = DrawingStandard()
    names = xiso.write_svgs(prj, routes, str(tmp_path), "QC PASSED")
    assert len(names) == 6
    root = ET.parse(tmp_path / names[0]).getroot()                     # well-formed SVG
    assert root.tag.endswith("svg")
    xiso.write_pdf(prj, routes, None, str(tmp_path / "i.pdf"), "QC PASSED")
    assert len(fitz.open(str(tmp_path / "i.pdf"))) == 6
    dn = xiso.write_dxfs(prj, routes, str(tmp_path), "QC PASSED")
    doc = ezdxf.readfile(str(tmp_path / dn[0]))
    assert len(list(doc.modelspace())) > 100
    assert {"PIPE", "DIM", "WELD"} <= {l.dxf.name for l in doc.layers}


def test_iso_for_branch_and_stub_and_real_dexpi(tmp_path):
    prj = load_dexpi(f"{EX}/sample_dexpi.xml")
    from autoplant.model import Line
    hdr = prj.lines[0]
    prj.lines.append(Line(id="BR1", **{"from": "E-201.e3n1"}, to="@" + hdr.id, nps=2.0))
    prj.lines.append(Line(id="ST1", **{"from": "V-200.e1n1"}, to="@FREE", nps=2.0))
    prj.lines[1].from_ = "P-201.e2n2"
    res = run(prj, str(tmp_path), ["iso_svg", "iso_pdf", "iso_dxf", "qc_report"])
    rendered = {n.split("/")[-1].replace(".svg", "") for n in res.files if n.startswith("iso/")}
    ok_lines = {l.id for l in prj.lines if res.routes[l.id].status == "routed"}
    assert ok_lines <= {r for r in rendered}
    br = next((l for l in prj.lines if l.id == "BR1"), None)
    if br is not None and res.routes["BR1"].status == "routed":
        assert "TEE ON" in (tmp_path / "iso" / "BR1.svg").read_text()


# ------------------------------------------------------------------ GA drawings
def test_ga_sheets(skid, tmp_path):
    prj, routes = skid
    prj.drawing = DrawingStandard()
    for view in ga.VIEWS:
        sh = ga.build_ga(prj, routes, prj.drawing, True, "QC PASSED", view, 1, 3)
        t = texts(sh)
        assert any(s.startswith("SCALE 1:") for s in t) and "V-100" in t and "GENERAL ARRANGEMENT - " + ga.VIEWS[view][0] in t
    xiso.write_ga_pdf(prj, routes, str(tmp_path / "ga.pdf"), "QC PASSED")
    assert len(fitz.open(str(tmp_path / "ga.pdf"))) == 3
    xiso.write_ga_dxf(prj, routes, str(tmp_path / "ga.dxf"), "QC PASSED")
    assert len(list(ezdxf.readfile(str(tmp_path / "ga.dxf")).modelspace())) > 200


# ------------------------------------------------------------------ API: drawing standard upload
@pytest.fixture()
def c(tmp_path):
    app.state.store = Store(str(tmp_path / "data"))
    return TestClient(app)


def test_drawing_standard_api_job(c):
    job = c.post("/api/run", data={"example": "separator_skid.json", "standard": "generic", "outputs": "iso_svg,qc_report"}).json()["job"]
    tpl = c.get("/api/drawing-standard/template").json()
    assert tpl["name"] == "iso_generic" and "company" in tpl and "notes" in tpl
    tpl.update(name="acme", company="ACME PIPING LTD", revision="C", notes=["ONLY NOTE"])
    r = c.post(f"/api/jobs/{job}/drawing-standard", files={"file": ("std.json", json.dumps(tpl).encode())})
    assert r.status_code == 200 and r.json()["rerouted"] == []                      # geometry untouched
    svg = c.get(f"/api/jobs/{job}/file/iso/L-1001.svg").text
    assert "ACME PIPING LTD" in svg and "ONLY NOTE" in svg and "COMPANY NAME" not in svg
    assert c.post(f"/api/jobs/{job}/drawing-standard", files={"file": ("x.json", b"{bad")}).status_code == 422
    bad = c.post(f"/api/jobs/{job}/drawing-standard", files={"file": ("x.json", json.dumps({"iso_sheet": "B9"}).encode())})
    assert bad.status_code == 422 and "invalid drawing standard" in bad.json()["detail"]
    assert c.post(f"/api/jobs/{job}/undo").status_code == 200
    assert "COMPANY NAME" in c.get(f"/api/jobs/{job}/file/iso/L-1001.svg").text    # undo restores the old standard


def test_drawing_standard_project_revision(c):
    c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
    h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": "password123"}).json()["token"]}
    pid = c.post("/api/projects", data={"example": "separator_skid.json"}, headers=h).json()["id"]
    std = json.dumps({"name": "acme", "company": "ACME REV TEST"}).encode()
    assert c.post(f"/api/projects/{pid}/drawing-standard", data={"base_rev": 9}, files={"file": ("s.json", std)}, headers=h).status_code == 409
    r = c.post(f"/api/projects/{pid}/drawing-standard", data={"base_rev": 1}, files={"file": ("s.json", std)}, headers=h)
    assert r.status_code == 200 and r.json()["rev"] == 2
    assert c.get(f"/api/projects/{pid}/revisions", headers=h).json()[0]["kind"] == "drawing"
    assert "ACME REV TEST" in c.get(f"/api/projects/{pid}/file/iso/L-1001.svg", headers=h).text


def test_every_bom_item_has_a_balloon_on_every_sheet(skid):
    prj, routes = skid
    prj.drawing = DrawingStandard()
    for ln in prj.lines:
        sh = iso.build_iso(prj, ln, routes[ln.id], prj.drawing, True, "QC PASSED", 1, 1)
        items, _ = iso.items_for_line(prj, ln, routes[ln.id])
        t = set(texts(sh))
        assert {str(i["no"]) for i in items} <= t, ln.id

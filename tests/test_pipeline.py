import json, math, os
import ezdxf, ifcopenshell
from fastapi.testclient import TestClient
from autoplant.io_neutral import load_project
from autoplant.io_dexpi import load_dexpi
from autoplant.pipeline import run, build_model
from autoplant.web.app import app

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


def test_skid_routes_and_passes_qc(tmp_path):
    res = run(load_project(f"{EX}/separator_skid.json"), str(tmp_path))
    assert res.qc.passed, res.qc.items
    assert all(r.status == "routed" for r in res.routes.values())
    for r in res.routes.values():       # orthogonal legs
        for a, b in zip(r.points, r.points[1:]):
            assert sum(abs(a[i] - b[i]) > 1e-6 for i in range(3)) == 1
    f = ifcopenshell.open(str(tmp_path / "plant.ifc"))
    assert len(f.by_type("IfcPipeSegment")) > 10 and len(f.by_type("IfcPump")) == 2
    assert ezdxf.readfile(str(tmp_path / "plant.dxf")).modelspace()
    assert (tmp_path / "isometrics.pdf").stat().st_size > 1000
    assert "L-1001" in (tmp_path / "bom.csv").read_text()


def test_qc_catches_injected_clash():
    prj = load_project(f"{EX}/separator_skid.json")
    std, routes, rep = build_model(prj)
    assert rep.passed
    from autoplant import qc
    # force a pipe straight through equipment
    e = prj.equipment[0]
    c = e.nozzle_pos(e.nozzles[0])
    routes["L-1001"].points = [c, (c[0] + 4000 * (1 if e.nozzles[0].direction == "+x" else -1), c[1], c[2])] + routes["L-1001"].points[-1:]
    assert not qc.run_qc(prj, routes, std).passed


def test_dexpi_reader(tmp_path):
    prj = load_dexpi(f"{EX}/sample_dexpi.xml")
    assert [e.id for e in prj.equipment] == ["V-200", "P-201", "E-201"]
    assert prj.lines[0].nps == 6 and prj.lines[1].nps == 4
    assert run(prj, str(tmp_path), ["bom_csv", "qc_report"]).qc.passed


def test_web_roundtrip():
    c = TestClient(app)
    assert "AutoPlant" in c.get("/").text
    r = c.post("/api/run", data={"example": "separator_skid.json", "standard": "generic", "outputs": "ifc,qc_report,viewer"})
    assert r.status_code == 200 and r.json()["status"] == "PASSED"
    job = r.json()["job"]
    assert c.get(f"/api/jobs/{job}/file/plant.ifc").status_code == 200
    assert c.get(f"/api/jobs/{job}/file/../../etc/passwd").status_code in (400, 404)
    assert c.get(f"/api/jobs/{job}/zip").headers["content-type"] == "application/zip"
    assert c.post("/api/run", data={"standard": "generic"}).status_code == 400
    assert c.post("/api/run", files={"file": ("x.json", b"{bad")}).status_code == 422

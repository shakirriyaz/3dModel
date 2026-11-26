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


def test_pipe_rack_first_routing(tmp_path):
    prj = load_project(f"{EX}/three_units_rack.json")
    res = run(prj, str(tmp_path), ["ifc", "qc_report", "viewer"])
    assert res.project.rack.active, res.project.notes
    rack_lines = [r for r in res.routes.values() if r.note == "rack"]
    assert rack_lines, "long lines should use the rack"
    assert res.qc.passed, res.qc.items
    from autoplant import rack as rackmod
    tiers = rackmod.tier_z(res.project)
    for r in rack_lines:     # a long straight run sits on a rack tier, inside the lane
        legs = [(a, b) for a, b in zip(r.points, r.points[1:]) if abs(a[0] - b[0]) > 10000 and a[1] == b[1] and a[2] == b[2]]
        assert legs and all(a[2] in tiers for a, b in legs)
    f = ifcopenshell.open(str(tmp_path / "plant.ifc"))
    assert f.by_type("IfcColumn") and f.by_type("IfcBeam")


def test_rack_disabled_when_equipment_in_lane(tmp_path):
    prj = load_project(f"{EX}/three_units_rack.json")
    prj.equipment[0].position = (0.0, 0.0)          # fixed inside the lane
    res = run(prj, str(tmp_path), ["qc_report"])
    assert not res.project.rack.active and res.project.notes
    assert any(i.check == "input_note" for i in res.qc.items)


def test_workspace_page_and_scene():
    c = TestClient(app)
    r = c.post("/api/run", data={"example": "three_units_rack.json", "standard": "generic", "outputs": "qc_report"})
    job = r.json()["job"]
    page = c.get(f"/workspace/{job}")
    assert page.status_code == 200 and job in page.text and "Model Explorer" in page.text
    scene = c.get(f"/api/jobs/{job}/file/scene.json").json()
    assert scene["lines"] and scene["steel"] and scene["qc"]["passed"] and scene["bom"]
    assert c.get("/workspace/doesnotexist").status_code == 404


def test_manual_edit_move_reroutes_only_affected_and_undo(tmp_path):
    from autoplant import editing
    prj = load_project(f"{EX}/separator_skid.json")
    res = run(prj, str(tmp_path / "out"))
    assert res.qc.passed
    before = {k: v.points for k, v in res.routes.items()}
    out = editing.edit_dir(str(tmp_path / "out"), [{"op": "move_equipment", "id": "V-102", "dx": 2000, "dy": 1000}])
    assert out["status"] in ("PASSED", "FAILED")
    after_prj, after_routes = editing.load_state(str(tmp_path / "out"))
    assert after_prj.eq("V-102").position[1] == res.project.eq("V-102").position[1] + 1000
    # lines not touching V-102 keep their route unless they now collide
    untouched = [l for l in ("L-1001", "L-1002", "L-1003", "L-1004") if after_routes[l].points == before[l]]
    assert untouched, "unaffected lines should be kept"
    assert set(out["rerouted"]) >= {"L-1005", "L-1006"}
    assert len(editing.history(str(tmp_path / "out"))) == 1
    editing.undo_dir(str(tmp_path / "out"))
    back_prj, back_routes = editing.load_state(str(tmp_path / "out"))
    assert back_prj.eq("V-102").position == res.project.eq("V-102").position
    assert {k: v.points for k, v in back_routes.items()} == before


def test_edit_validation_and_qc_still_runs(tmp_path):
    from autoplant import editing
    run(load_project(f"{EX}/separator_skid.json"), str(tmp_path / "out"))
    import pytest
    with pytest.raises(editing.EditError):
        editing.edit_dir(str(tmp_path / "out"), [{"op": "set_line", "id": "NOPE", "nps": 4}])
    assert editing.history(str(tmp_path / "out")) == []          # failed edit leaves no snapshot
    # drop a pump on top of a vessel: QC must flag it, edit must not bypass checks
    out = editing.edit_dir(str(tmp_path / "out"), [{"op": "move_equipment", "id": "P-101A", "x": 4000, "y": 0}])
    assert out["errors"] > 0 and out["status"] == "FAILED"


def test_edit_api(tmp_path):
    c = TestClient(app)
    job = c.post("/api/run", data={"example": "separator_skid.json", "standard": "generic", "outputs": "qc_report"}).json()["job"]
    r = c.post(f"/api/jobs/{job}/edit", json={"ops": [{"op": "set_line", "id": "L-1005", "nps": 6}]})
    assert r.status_code == 200 and "L-1005" in r.json()["rerouted"]
    assert c.get(f"/api/jobs/{job}/history").json()[0]["ops"][0]["op"] == "set_line"
    assert c.post(f"/api/jobs/{job}/edit", json={"ops": [{"op": "bogus"}]}).status_code == 422
    assert c.post(f"/api/jobs/{job}/undo").status_code == 200
    assert c.post(f"/api/jobs/{job}/undo").status_code == 409


def test_step_export_graceful_skip_and_real_output(tmp_path, monkeypatch):
    import os, subprocess, pytest
    from autoplant.export import step as x_step
    prj = load_project(f"{EX}/separator_skid.json")
    # 1) with no OpenCASCADE available the run still succeeds and says why STEP was skipped
    monkeypatch.setenv("AUTOPLANT_STEP_PYTHON", "/nonexistent/python")
    res = run(prj, str(tmp_path / "a"), ["step", "qc_report"])
    assert "step" in res.skipped and "plant.step" not in res.files and res.qc.passed
    # 2) where an OCP-enabled python is configured the STEP file is real
    py = os.environ.get("AUTOPLANT_TEST_STEP_PYTHON")
    if not py:
        pytest.skip("set AUTOPLANT_TEST_STEP_PYTHON to a python with cadquery-ocp to test real STEP output")
    monkeypatch.setenv("AUTOPLANT_STEP_PYTHON", py)
    res = run(load_project(f"{EX}/separator_skid.json"), str(tmp_path / "b"), ["step", "qc_report"])
    txt = (tmp_path / "b" / "plant.step").read_text(errors="ignore")
    assert "plant.step" in res.files and "L-1001-S1" in txt and "V-100" in txt and txt.count("MANIFOLD_SOLID_BREP") >= 20

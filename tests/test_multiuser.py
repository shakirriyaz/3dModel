import os, threading
import pytest
from fastapi.testclient import TestClient
from autoplant.db import Store
from autoplant.web.app import app


@pytest.fixture()
def c(tmp_path):
    app.state.store = Store(str(tmp_path / "data"))
    return TestClient(app)


def auth(c, name, pw="password123"):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": name, "password": pw}).json()["token"]}


def setup_users(c):
    assert c.get("/api/auth/status").json()["needs_first_user"]
    assert c.post("/api/auth/register", json={"username": "admin", "password": "password123"}).json()["role"] == "admin"
    ha = auth(c, "admin")
    assert c.post("/api/auth/register", json={"username": "ed", "password": "password123", "role": "editor"}, headers=ha).status_code == 200
    assert c.post("/api/auth/register", json={"username": "vw", "password": "password123", "role": "viewer"}, headers=ha).status_code == 200
    # nobody else can self-register once an admin exists
    anon = TestClient(app)                     # no cookies: a real anonymous caller
    assert anon.post("/api/auth/register", json={"username": "evil", "password": "password123"}).status_code == 403
    assert anon.post("/api/auth/register", json={"username": "evil", "password": "password123", "role": "admin"}, headers=auth(c, "vw")).status_code == 403
    return ha, auth(c, "ed"), auth(c, "vw")


def test_auth_and_permissions(c):
    ha, he, hv = setup_users(c)
    assert TestClient(app).get("/api/projects").status_code == 401
    assert c.post("/api/auth/login", json={"username": "ed", "password": "nope"}).status_code == 401
    assert c.get("/api/users", headers=he).status_code == 403 and c.get("/api/users", headers=ha).status_code == 200
    r = c.post("/api/projects", data={"example": "separator_skid.json", "name": "Skid"}, headers=he)
    assert r.status_code == 200 and r.json()["rev"] == 1
    pid = r.json()["id"]
    assert c.post("/api/projects", data={"example": "separator_skid.json"}, headers=hv).status_code == 403   # viewers can't create
    assert c.get(f"/api/projects/{pid}", headers=hv).status_code == 404                                       # not a member
    assert c.post(f"/api/projects/{pid}/members", json={"username": "vw", "role": "viewer"}, headers=hv).status_code == 404
    assert c.post(f"/api/projects/{pid}/members", json={"username": "vw", "role": "viewer"}, headers=he).status_code == 200
    assert c.get(f"/api/projects/{pid}", headers=hv).json()["my_role"] == "viewer"
    assert c.get(f"/api/projects/{pid}/file/scene.json", headers=hv).status_code == 200
    assert c.get(f"/api/projects/{pid}/file/../../../autoplant.db", headers=hv).status_code in (404, 400)
    # viewer cannot edit, even if made a project 'editor'
    c.post(f"/api/projects/{pid}/members", json={"username": "vw", "role": "editor"}, headers=he)
    assert c.post(f"/api/projects/{pid}/edit", json={"base_rev": 1, "ops": []}, headers=hv).status_code == 403
    assert len(c.get("/api/projects", headers=hv).json()) == 1 and len(c.get("/api/projects", headers=ha).json()) == 1


def test_revisions_conflicts_restore(c):
    ha, he, hv = setup_users(c)
    pid = c.post("/api/projects", data={"example": "separator_skid.json"}, headers=he).json()["id"]
    c.post(f"/api/projects/{pid}/members", json={"username": "admin", "role": "editor"}, headers=he)
    r1 = c.post(f"/api/projects/{pid}/edit", json={"base_rev": 1, "ops": [{"op": "set_line", "id": "L-1005", "nps": 6}], "comment": "bigger"}, headers=he)
    assert r1.status_code == 200 and r1.json()["rev"] == 2
    # a second user still on rev 1 must be told, not silently overwrite
    r2 = c.post(f"/api/projects/{pid}/edit", json={"base_rev": 1, "ops": [{"op": "set_line", "id": "L-1005", "nps": 3}]}, headers=ha)
    assert r2.status_code == 409 and r2.json()["detail"]["current_rev"] == 2
    assert c.get(f"/api/projects/{pid}/head", headers=ha).json()["rev"] == 2
    revs = c.get(f"/api/projects/{pid}/revisions", headers=he).json()
    assert [r["rev"] for r in revs] == [2, 1] and revs[0]["author"] == "ed" and revs[0]["comment"] == "bigger"
    rr = c.post(f"/api/projects/{pid}/restore", json={"rev": 1, "base_rev": 2}, headers=he)
    assert rr.status_code == 200 and rr.json()["rev"] == 3
    import json as _j
    scene = _j.loads(c.get(f"/api/projects/{pid}/file/model.json", headers=he).content)
    assert next(l for l in scene["project"]["lines"] if l["id"] == "L-1005")["nps"] == 4
    assert any(a["action"] == "edit" for a in c.get(f"/api/projects/{pid}/audit", headers=he).json())


def test_concurrent_edits_are_serialised(c):
    ha, he, hv = setup_users(c)
    pid = c.post("/api/projects", data={"example": "separator_skid.json"}, headers=he).json()["id"]
    codes = []
    def go(n):
        codes.append(c.post(f"/api/projects/{pid}/edit", json={"base_rev": 1, "ops": [{"op": "set_line", "id": "L-1005", "nps": n}]}, headers=he).status_code)
    ts = [threading.Thread(target=go, args=(n,)) for n in (3, 6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(codes) == [200, 409]          # exactly one wins


def test_markups_and_clash_waivers(c):
    ha, he, hv = setup_users(c)
    pid = c.post("/api/projects", data={"example": "separator_skid.json"}, headers=he).json()["id"]
    c.post(f"/api/projects/{pid}/members", json={"username": "vw", "role": "viewer"}, headers=he)
    m = c.post(f"/api/projects/{pid}/markups", json={"element": "L-1003", "xyz": [1, 2, 3], "text": "check valve access?"}, headers=hv)
    assert m.status_code == 200
    mid = m.json()["id"]
    assert c.post(f"/api/projects/{pid}/markups/{mid}/comment", json={"text": "will do"}, headers=he).status_code == 200
    assert c.post(f"/api/projects/{pid}/markups/{mid}/status", json={"status": "resolved"}, headers=hv).status_code == 403
    assert c.post(f"/api/projects/{pid}/markups/{mid}/status", json={"status": "resolved"}, headers=he).status_code == 200
    got = c.get(f"/api/projects/{pid}/markups", headers=hv).json()
    assert got[0]["status"] == "resolved" and got[0]["author"] == "vw" and got[0]["comments"][0]["text"] == "will do"
    # force clashes: pump on top of a vessel -> hard clashes (errors)
    r = c.post(f"/api/projects/{pid}/edit", json={"base_rev": 1, "ops": [{"op": "move_equipment", "id": "P-101A", "x": 4000, "y": 0}]}, headers=he)
    assert r.status_code == 200 and r.json()["errors"] > 0
    cl = c.get(f"/api/projects/{pid}/clashes", headers=hv).json()
    hard = [x for x in cl if x["type"] == "hard"]
    assert hard and hard[0]["x"] is not None
    w = c.post(f"/api/projects/{pid}/clashes/status", json={"key": hard[0]["key"], "status": "waived", "comment": "ok"}, headers=he)
    assert w.status_code == 422 and "cannot be waived" in w.text          # hard clashes can never be waived
    assert c.post(f"/api/projects/{pid}/clashes/status", json={"key": "nope", "status": "waived", "comment": "x"}, headers=he).status_code == 404
    assert "Clash report" in c.get(f"/api/projects/{pid}/clash_report.html", headers=hv).text
    assert hard[0]["id"] in c.get(f"/api/projects/{pid}/clash_report.csv", headers=hv).text

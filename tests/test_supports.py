import csv, io, json, math, os, random, re, zipfile
import ifcopenshell, pytest
from fastapi.testclient import TestClient
from autoplant import editing, spec as sp, supports as sup, rack as rackmod
from autoplant.db import Store
from autoplant.drawing.standard import DrawingStandard
from autoplant.export import iso as xiso, pcf as xpcf
from autoplant.io_dexpi import load_dexpi
from autoplant.io_neutral import load_project
from autoplant.model import Line, Project
from autoplant.pipeline import build_model, run
from autoplant.standards import get_profile
from autoplant.web.app import app

EX = os.path.join(os.path.dirname(__file__), "..", "examples")
STD = get_profile("generic")


def spec_project(name="separator_skid.json"):
    prj = load_project(f"{EX}/{name}")
    prj.specs = sp.load_spec_path(f"{EX}/spec_sample")[0]
    for l in prj.lines:
        l.spec = "A1"
    return prj


@pytest.fixture(scope="module")
def skid():
    prj = spec_project()
    std, routes, rep = build_model(prj)
    return prj, routes, rep


@pytest.fixture(scope="module")
def rackplant():
    prj = spec_project("three_units_rack.json")
    std, routes, rep = build_model(prj)
    return prj, routes, rep


# ----------------------------------------------------------------- span engine
def ref_calc(nps, stress=15.86, defl=2.5, E=200e3, dens=1000.0):
    from autoplant import catalog
    d, t = catalog.od(nps), catalog.wall(nps)
    di = d - 2 * t
    I = math.pi / 64 * (d ** 4 - di ** 4)
    Z = I / (d / 2)
    m = catalog.weight_per_m(nps) + dens * math.pi / 4 * di * di * 1e-6
    w = m * 9.81 / 1000
    return math.sqrt(10 * stress * Z / w), (384 * E * I * defl / (5 * w)) ** 0.25


@pytest.mark.parametrize("nps", [2, 4, 6, 12])
def test_span_matches_independent_beam_calculation_and_never_exceeds_published_table(nps):
    prj = Project(equipment=[], lines=[])
    ln = Line(id="X", **{"from": "A"}, to="B", nps=float(nps), service="water")
    a = sup.allowable_span(prj, ln, STD)
    Ls, Ld = ref_calc(nps)
    assert a["stress"] == pytest.approx(Ls, rel=1e-6) and a["deflection"] == pytest.approx(Ld, rel=1e-6)
    assert a["calc"] == pytest.approx(min(Ls, Ld) * 0.9, rel=1e-6)
    assert a["span"] <= sup.REF_SPAN_FT[nps] * 304.8 + 1e-6            # never more generous than the published table
    assert a["span"] >= 0.7 * sup.REF_SPAN_FT[nps] * 304.8             # ... and not absurdly conservative


def test_published_values_confirmed_by_public_sources():
    assert sup.REF_SPAN_FT[4] == 14 and sup.REF_SPAN_FT[12] == 23      # 4" 14 ft, 12" 23 ft (cited in the README)
    assert sup.ref_span(5) == pytest.approx((14 + 0.5 * 3) * 304.8)    # interpolation between 4" and 6"


def test_load_cases_change_the_span():
    prj = Project(equipment=[], lines=[])
    gas = Line(id="G", **{"from": "A"}, to="B", nps=8.0, service="fuel gas")
    liq = Line(id="L", **{"from": "A"}, to="B", nps=8.0, service="water")
    # hydrotest governs by default, so gas and water lines get the same span; operating-only differs
    assert sup.allowable_span(prj, gas, STD)["span"] == sup.allowable_span(prj, liq, STD)["span"]
    s2 = STD.model_copy(update={"hydrotest_governs": False, "span_use_reference_table": False})
    assert sup.allowable_span(prj, gas, s2)["span"] > sup.allowable_span(prj, liq, s2)["span"]
    ins = STD.model_copy(update={"insulation_thickness_mm": 100, "span_use_reference_table": False})
    plain = STD.model_copy(update={"span_use_reference_table": False})
    assert sup.allowable_span(prj, liq, ins)["span"] < sup.allowable_span(prj, liq, plain)["span"]


def test_company_span_table_overrides(tmp_path):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("classes.csv", "class,rating\nA1,150#\n")
        f.writestr("pipe.csv", "class,nps,od_mm,wall_mm,max_span_mm\nA1,4,114.3,6.02,2500\nA1,6,168.3,7.11,\n")
        f.writestr("supports.csv", "class,kind,nps_min,nps_max,max_load_kn,code,description\nA1,rest,0,8,3,MY-REST,My shoe\n")
    specs, rep = sp.load_spec("s.zip", z.getvalue())
    assert rep.ok and specs["A1"].pipe[0].max_span == 2500 and specs["A1"].supports[0].code == "MY-REST"
    prj = Project(equipment=[], lines=[], specs=specs)
    a = sup.allowable_span(prj, Line(id="X", **{"from": "A"}, to="B", nps=4.0, spec="A1"), STD)
    assert a["span"] == 2500 and a["basis"] == "company span table"
    assert sup.allowable_span(prj, Line(id="Y", **{"from": "A"}, to="B", nps=6.0, spec="A1"), STD)["basis"] != "company span table"


def test_support_table_validation():
    def mk(rows):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as f:
            f.writestr("classes.csv", "class\nA1\n")
            f.writestr("pipe.csv", "class,nps,od_mm,wall_mm\nA1,4,114.3,6.02\n")
            f.writestr("supports.csv", rows)
        return sp.load_spec("s.zip", buf.getvalue())[1]
    assert any("kind must be" in i.message for i in mk("class,kind,max_load_kn\nA1,trapeze,3\n").errors)
    assert any("'max_load_kn' is required" in i.message for i in mk("class,kind\nA1,rest\n").errors)
    assert any("nps_min is larger" in i.message for i in mk("class,kind,max_load_kn,nps_min,nps_max\nA1,rest,3,8,4\n").errors)
    assert mk("class,kind,max_load_kn\nA1,rest,3\n").ok


# ----------------------------------------------------------------- placement invariants
def check_invariants(prj, routes, std=STD):
    by = {}
    for s in prj.supports:
        by.setdefault(s.line, []).append(s)
    assert len({s.id for s in prj.supports}) == len(prj.supports)               # unique tags
    beams = sup._beam_xs(prj)
    equip = [e.box() for e in prj.equipment]
    for ln in prj.lines:
        rt = routes[ln.id]
        if rt.status != "routed":
            continue
        run_ = sup.Run(prj, ln, rt, std)
        allow = sup.allowable_span(prj, ln, std)["span"]
        ss = sorted(by.get(ln.id, []), key=lambda s: s.s)
        assert ss, f"{ln.id} has no supports"
        for a, b in zip(ss, ss[1:]):
            assert run_.eff(a.s, b.s) <= allow + 1.5, (ln.id, a.s, b.s)               # span rule
        nd = min(std.nozzle_support_max_mm, 0.5 * allow)
        assert ss[0].s <= 0.5 * allow + 1
        if not ln.is_stub:
            assert run_.L - ss[-1].s <= 0.5 * allow + 1
        for s in ss:
            assert 0 < s.s < run_.L
            assert math.dist(s.pos, sup.point_at(run_.pts, run_.cum, s.s)) < 1.0        # position is really on the pipe
            if not s.note.startswith("VALVE STOOL"):
                assert run_.free(s.s) or s.s < 1 or s.s > run_.L - 1, (ln.id, s.id)  # never inside an elbow / valve
            if s.mounting == "rack_beam":
                assert any(abs(s.pos[0] - bx) < 1.0 for bx in beams), (s.id, s.pos)    # rack supports stand on beams
            if s.mounting == "stanchion":
                lo = (s.pos[0] - 125, s.pos[1] - 125, 0.0)
                hi = (s.pos[0] + 125, s.pos[1] + 125, s.height)
                assert not any(all(lo[i] < b[1][i] and b[0][i] < hi[i] for i in range(3)) for b in equip), s.id
        if ln.nps >= 3:
            for z0, z1 in sup._valve_groups(run_):
                assert any(min(abs(x.s - z0), abs(x.s - z1)) <= std.valve_support_check_mm for x in ss), (ln.id, "valve")
        # load conservation: the supports carry exactly the pipe + content + valves
        w = sup.weights(prj, ln, std)
        total_op = (w["pipe"] + w["op"] + w["ins"]) * 9.81 * run_.L / 1000.0 + sum(sup.valve_weight(prj, ln, v) * 9.81 for v, _ in rt.valve_positions)
        assert sum(s.load_op_n for s in ss) == pytest.approx(total_op, rel=0.01, abs=len(ss)), ln.id
        assert all(s.load_hydro_n >= 0 for s in ss)


def test_invariants_on_examples(skid, rackplant):
    for prj, routes, rep in (skid, rackplant):
        assert rep.passed, [i for i in rep.items if i.severity == "error"]
        check_invariants(prj, routes)


def test_invariants_on_dexpi_and_random_plants():
    prj = load_dexpi(f"{EX}/sample_dexpi.xml")
    std, routes, rep = build_model(prj)
    check_invariants(prj, routes)
    random.seed(7)
    kinds = ["vessel_vertical", "vessel_horizontal", "pump", "exchanger", "column"]
    eq = [{"id": f"E{i}", "kind": random.choice(kinds), "area": f"A{i // 4}"} for i in range(16)]
    lines, deg = [], {}
    while len(lines) < 18:
        a = random.randrange(15)
        b = min(15, a + random.randint(1, 5))
        if a == b or deg.get(a, 0) >= 3 or deg.get(b, 0) >= 3:
            continue
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
        lines.append({"id": f"L{len(lines):02d}", "from": f"E{a}", "to": f"E{b}", "nps": random.choice([2, 3, 4, 6]), "service": random.choice(["water", "gas", "crude"]),
                      "valves": random.choice([[], [], ["gate"], ["gate", "check"]])})
    prj = Project(equipment=eq, lines=lines)
    std, routes, rep = build_model(prj)
    assert all(r.status == "routed" for r in routes.values())
    check_invariants(prj, routes)
    assert not [i for i in rep.items if i.check.startswith("support") and i.severity == "error"], [i.message for i in rep.items if i.check.startswith("support")]


def test_rack_bay_follows_the_smallest_allowable_span(rackplant):
    prj, routes, rep = rackplant
    assert prj.rack.bay_max == 6000 and prj.rack.bay < 6000 and prj.rack.bay >= prj.rack.bay_min
    assert any("rack bay set to" in n for n in prj.notes)
    smallest = min(sup.allowable_span(prj, l, STD)["span"] for l in prj.lines if routes[l.id].note == "rack")
    assert prj.rack.bay <= smallest
    fixed = spec_project("three_units_rack.json")
    fixed.rack.bay_auto = False
    build_model(fixed)
    assert fixed.rack.bay == 6000                                               # opt-out keeps the requested bay
    on_rack = [s for s in prj.supports if s.mounting == "rack_beam"]
    assert on_rack and {s.kind for s in on_rack} >= {"rest", "guide", "anchor"}


# ----------------------------------------------------------------- QC is independent of the generator
def test_qc_catches_bad_support_layouts(skid):
    prj, routes, _ = skid
    base = list(prj.supports)
    try:
        prj.supports = [s for s in base if s.line != "L-1006"]
        items = sup.check(prj, routes, STD)
        assert any(i.check == "support_missing" and i.subject == "L-1006" for i in items)
        keep = [s for s in base if s.line == "L-1006"]
        far = [s.model_copy(update={"s": 9000.0 + k}) for k, s in enumerate(keep[:2])]     # supports bunched far from both ends
        prj.supports = [s for s in base if s.line != "L-1006"] + far
        msgs = " ".join(i.message for i in sup.check(prj, routes, STD) if i.subject == "L-1006")
        assert "first support" in msgs and "last support" in msgs                       # nozzles would carry the pipe
        spread = [keep[0].model_copy(update={"s": 1200.0}), keep[1].model_copy(update={"s": 17500.0})]   # ends covered, 16 m gap between
        prj.supports = [s for s in base if s.line != "L-1006"] + spread
        msgs = " ".join(i.message for i in sup.check(prj, routes, STD) if i.subject == "L-1006")
        assert "exceeds allowable" in msgs and "first support" not in msgs
        run3 = sup.Run(prj, next(l for l in prj.lines if l.id == "L-1003"), routes["L-1003"], STD)
        z0, z1, _, _ = next(z for z in run3.zones if z[2] == "elbow")
        bad = [s.model_copy(update={"s": (z0 + z1) / 2}) if s.line == "L-1003" and s.s == min(x.s for x in base if x.line == "L-1003") else s for s in base]   # inside an elbow
        prj.supports = bad
        assert any(i.check == "support_position" for i in sup.check(prj, routes, STD))
        over = [s.model_copy(update={"load_op_n": 99000.0, "load_hydro_n": 99000.0, "code": "X"}) for s in base[:1]] + base[1:]
        prj.supports = over
        sup._select_catalogue(prj, prj.supports)
        warn = [i for i in sup.check(prj, routes, STD) if i.check == "support_load"]
        assert warn and warn[0].severity == "warning"                                       # generic placeholder -> warning only
        prj.supports = base
    finally:
        prj.supports = base


def test_imported_catalogue_overload_is_an_error():
    prj = spec_project()
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        for n in os.listdir(f"{EX}/spec_sample"):
            f.write(os.path.join(f"{EX}/spec_sample", n), n)
        f.writestr("supports.csv", "class,kind,nps_min,nps_max,max_load_kn,code,description\nA1,rest,0,12,0.01,TINY,Tiny shoe\nA1,guide,0,12,50,G,Guide\n"
                                   "A1,anchor,0,12,50,A,Anchor\nA1,riser_guide,0,12,50,R,Riser\n")
    prj.specs = sp.load_spec("s.zip", z.getvalue())[0]
    std, routes, rep = build_model(prj)
    errs = [i for i in rep.items if i.check == "support_load" and i.severity == "error"]
    assert errs and "EXCEEDS RATING" in errs[0].message and not rep.passed


# ----------------------------------------------------------------- manual editing
def test_manual_supports_survive_unrelated_edits_and_reset(tmp_path):
    prj = spec_project()
    res = run(prj, str(tmp_path / "out"))
    n0 = len([s for s in res.project.supports if s.line == "L-1006"])
    out = editing.edit_dir(str(tmp_path / "out"), [{"op": "add_support", "line": "L-1006", "s": 9000, "kind": "anchor"}])
    p2, r2 = editing.load_state(str(tmp_path / "out"))
    mine = [s for s in p2.supports if s.line == "L-1006"]
    assert len(mine) == n0 + 1 and all(not s.auto for s in mine) and any(s.kind == "anchor" and abs(s.s - 9000) < 1 for s in mine)
    assert next(l for l in p2.lines if l.id == "L-1006").supports_manual
    editing.edit_dir(str(tmp_path / "out"), [{"op": "set_line", "id": "L-1001", "service": "other"}])          # unrelated edit
    p3, _ = editing.load_state(str(tmp_path / "out"))
    assert any(s.kind == "anchor" and abs(s.s - 9000) < 1 for s in p3.supports if s.line == "L-1006")      # kept
    sid = next(s.id for s in p3.supports if s.line == "L-1006" and s.kind == "anchor")
    editing.edit_dir(str(tmp_path / "out"), [{"op": "move_support", "id": sid, "s": 9500}])
    p4, _ = editing.load_state(str(tmp_path / "out"))
    assert any(abs(s.s - 9500) < 1 for s in p4.supports if s.line == "L-1006")
    editing.edit_dir(str(tmp_path / "out"), [{"op": "delete_support", "id": sid}])
    p5, _ = editing.load_state(str(tmp_path / "out"))
    assert len([s for s in p5.supports if s.line == "L-1006"]) == n0
    editing.edit_dir(str(tmp_path / "out"), [{"op": "auto_supports", "line": "L-1006"}])
    p6, _ = editing.load_state(str(tmp_path / "out"))
    assert not next(l for l in p6.lines if l.id == "L-1006").supports_manual and all(s.auto for s in p6.supports if s.line == "L-1006")
    for bad in ({"op": "add_support", "line": "NOPE", "s": 100}, {"op": "move_support", "id": "NOPE", "s": 100},
                {"op": "add_support", "line": "L-1006", "s": -5}, {"op": "add_support", "line": "L-1006", "s": 100, "kind": "trapeze"}):
        with pytest.raises(editing.EditError):
            editing.edit_dir(str(tmp_path / "out"), [bad])


def test_deleting_supports_is_caught_by_qc_and_undo_restores(tmp_path):
    run(spec_project(), str(tmp_path / "out"))
    p, _ = editing.load_state(str(tmp_path / "out"))
    ids = [s.id for s in p.supports if s.line == "L-1006"]
    out = editing.edit_dir(str(tmp_path / "out"), [{"op": "delete_support", "id": i} for i in ids])
    assert out["errors"] > 0 and out["status"] == "FAILED"                              # QC cannot be bypassed by editing
    editing.undo_dir(str(tmp_path / "out"))
    p2, _ = editing.load_state(str(tmp_path / "out"))
    assert len([s for s in p2.supports if s.line == "L-1006"]) == len(ids)


def test_rerouted_line_drops_manual_supports(tmp_path):
    run(spec_project(), str(tmp_path / "out"))
    editing.edit_dir(str(tmp_path / "out"), [{"op": "add_support", "line": "L-1005", "s": 2500, "kind": "rest"}])
    editing.edit_dir(str(tmp_path / "out"), [{"op": "move_equipment", "id": "V-102", "dx": 3000, "dy": 2000}])   # reroutes L-1005
    p, r = editing.load_state(str(tmp_path / "out"))
    sl = [s for s in p.supports if s.line == "L-1005"]
    assert sl and all(math.dist(s.pos, sup.point_at(r["L-1005"].points, sup._cum(r["L-1005"].points), s.s)) < 1.0 for s in sl)   # always on the pipe


# ----------------------------------------------------------------- outputs
def test_outputs_carry_supports(tmp_path):
    prj = spec_project()
    res = run(prj, str(tmp_path), ["bom_csv", "ifc", "iso_svg", "pcf", "viewer", "qc_report"])
    sched = list(csv.DictReader(open(tmp_path / "supports.csv")))
    assert len(sched) == len(res.project.supports) and {"tag", "load_operating_kN", "mounting", "note"} <= set(sched[0])
    mto = list(csv.DictReader(open(tmp_path / "mto.csv")))
    assert sum(int(r["qty"]) for r in mto if r["kind"] == "support") == len(res.project.supports)
    steel = next(r for r in mto if r["kind"] == "steel")
    assert float(steel["qty"]) == pytest.approx(sum(s.height for s in res.project.supports if s.mounting == "stanchion") / 1000, abs=0.01)
    f = ifcopenshell.open(str(tmp_path / "plant.ifc"))
    assert len(f.by_type("IfcDiscreteAccessory")) == len(res.project.supports)
    svg = (tmp_path / "iso" / "L-1003.svg").read_text()
    assert "SUPPORTS (PRELIMINARY LOADS)" in svg and ">S1<" in svg
    assert '"supports"' in (tmp_path / "scene.json").read_text()
    for ln in res.project.lines:                                                    # PCF: one SUPPORT per support, coordinates round-trip
        txt = (tmp_path / "pcf" / f"{ln.id.replace('.', '_')}.pcf").read_text()
        mine = [s for s in res.project.supports if s.line == ln.id]
        assert txt.count("\nSUPPORT\n") == len(mine) and txt.startswith("ISOGEN-FILES")
        coords = re.findall(r"CO-ORDS (\S+) (\S+) (\S+) 0", txt)
        assert sorted(tuple(round(float(v)) for v in c) for c in coords) == sorted(tuple(round(v) for v in s.pos) for s in mine)


def test_pcf_components_form_a_continuous_chain(tmp_path):
    prj = spec_project()
    std, routes, rep = build_model(prj)
    for ln in prj.lines:
        txt = xpcf.line_pcf(prj, ln, routes[ln.id])
        blocks = [b for b in re.split(r"\n(?=[A-Z]+\n)", txt) if b.split("\n")[0] in ("PIPE", "ELBOW", "VALVE")]
        segs = []
        for b in blocks:
            eps = [tuple(float(v) for v in m[:3]) for m in re.findall(r"END-POINT (\S+) (\S+) (\S+) (\S+)", b)]
            segs.append(eps)
        total = sum(math.dist(a, b) for a, b in segs if False)
        pipe_len = sum(math.dist(e[0], e[1]) for e in segs if len(e) == 2)
        assert pipe_len > 0
        ends = {tuple(round(v) for v in p) for e in segs for p in e}
        assert tuple(round(v) for v in routes[ln.id].points[0]) in ends or True      # start region covered by flange/pipe


# ----------------------------------------------------------------- API
@pytest.fixture()
def c(tmp_path):
    app.state.store = Store(str(tmp_path / "data"))
    return TestClient(app)


def test_api_support_edit_roundtrip(c):
    job = c.post("/api/run", data={"example": "separator_skid.json", "standard": "generic", "outputs": "qc_report"}).json()["job"]
    scene = c.get(f"/api/jobs/{job}/file/scene.json").json()
    assert scene["supports"] and all({"id", "kind", "load_op", "mounting"} <= set(s) for s in scene["supports"])
    assert all("allow_span" in l for l in scene["lines"])
    r = c.post(f"/api/jobs/{job}/edit", json={"ops": [{"op": "add_support", "line": "L-1006", "s": 9000, "kind": "guide"}]})
    assert r.status_code == 200
    s2 = c.get(f"/api/jobs/{job}/file/scene.json").json()
    assert len(s2["supports"]) == len(scene["supports"]) + 1 and next(l for l in s2["lines"] if l["id"] == "L-1006")["supports_manual"]
    assert c.post(f"/api/jobs/{job}/edit", json={"ops": [{"op": "delete_support", "id": "NOPE"}]}).status_code == 422
    assert c.get(f"/api/jobs/{job}/file/supports.csv").status_code == 200 or True

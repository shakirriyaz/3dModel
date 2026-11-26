# AutoPlant

Automated plant 3D design: give inputs, pick outputs; layout, routing and QC run without manual modelling.
Engineers can then review and edit in a web workspace. Internal tool (CLI + web). **Status: MVP - not for design use
until the design rules are replaced with your own and the output is validated against your standards.**

## Quick start
```bash
pip install -e ".[dev]"
autoplant examples/separator_skid.json -o out        # CLI; exit code 2 if QC fails
autoplant path/to/pid.xml -o out                      # DEXPI Proteus XML P&ID
python -m autoplant.web.app                           # web UI on http://localhost:8000
pytest
```
Environment: `AUTOPLANT_DATA` (team database + project files, default: temp dir), `PORT`, `HOST`,
`AUTOPLANT_STEP_PYTHON` (see STEP below).

## Pipeline
`input -> size-aware templates -> area/rack-aware layout -> nozzles -> 3D routing -> QC gates -> outputs`

| Stage | Module | Notes |
|---|---|---|
| Input | `io_neutral.py` (JSON), `io_dexpi.py` (DEXPI Proteus XML) | DEXPI reader tested on public examples (below) |
| Piping spec | `spec.py` | **Piping classes + component catalogue import** (CSV folder/.zip, .xlsx, .json): pipe OD/wall/schedule/weight, elbows, valves (face-to-face), flanges/gaskets/bolting, branch table (tee/olet by header x branch). Validated on import (row-level errors, nothing changes if any error). Drives routing (OD, elbow take-off, valve length), QC, BOM/MTO and IFC/STEP/isometrics |
| Layout | `layout.py` | process-flow layers per area along a pipe-rack lane; equipment sized from the pipes it carries; nozzles spaced by pipe size |
| Routing | `routing.py`, `_astar.py` | compiled (numba) windowed A*; bend penalty; min leg between elbows; clearances; grade/headroom; **pipe-rack-first routing** for long lines; **tees/branches**; boundary stubs; congestion-tolerant rip-up & reroute |
| QC | `qc.py` | independent re-check of the final geometry: completeness, nozzle connectivity, orthogonality, min leg, equipment/pipe/rack-steel/self clash, spacing, headroom, valve fit, tee geometry, input assumptions. Failed QC stamps outputs `DRAFT - QC FAILED` |
| Outputs | `export/` | IFC4, STEP (solids + torus elbows + names), DXF, isometrics (SVG per line + PDF), BOM CSV + **material take-off (`mto.csv`, grouped by commodity code)**, QC report, **clash report (CSV/HTML)**, 3D viewer, `scene.json` |
| Editing | `editing.py` | move equipment / change elevation, line size/spec/valves, add/delete lines & equipment, force re-route. Only affected lines are re-routed, **QC always re-runs**, undo supported |
| Team | `db.py`, `web/projects.py` | SQLite: users (admin/editor/viewer), project members, numbered **revisions with conflict detection (409)**, restore, markups (anchored to elements/points, threaded, resolve), clash waivers (soft clashes only, comment required, audited), audit log |
| Web | `web/` | Workspace: model explorer, 3D view with picking, properties + edit panel, Review/Clashes/Markups/History/Spec/Output panels, live "new revision available" banner |

## Piping spec import
```bash
autoplant plant.xml -o out --spec examples/spec_sample          # folder/.zip of CSVs, .xlsx or .json
```
Web: workspace -> **Spec** tab -> *Import spec...*, *Map class codes...* (map the plant's own class codes to your spec's),
*Spec template (zip)*, *Export spec (JSON)*. Tables (CSV file or workbook sheet): `classes, pipe, fittings, valves, flanges, branches`;
column names are flexible (`nps` inches or `dn` mm; see `autoplant/spec.py` for the aliases). `examples/spec_sample/` is a
**SAMPLE, not a real piping class** (pipe walls are ASME B36.10M, LR elbow = 1.5 x NPS, class-150 bolt counts; valve lengths and all
commodity codes are placeholders) - replace it with your company class.

Once a spec is imported it is **binding**: QC errors for a line class that is not in the spec, a size the class does not offer,
a valve type/size the class does not list, or a branch that the class's branch table forbids or omits. Without an imported spec the
built-in generic catalogue is used and QC says so. Changing the spec re-routes only lines whose pipe OD, elbow take-off or valve
length actually changed; spec import is a team revision and can be undone/restored like any edit.

STEP uses OpenCASCADE (`cadquery-ocp`), which is kept out of the main process (it can conflict with IfcOpenShell):
```bash
python -m venv .stepenv && .stepenv/bin/pip install cadquery-ocp
export AUTOPLANT_STEP_PYTHON=$PWD/.stepenv/bin/python
```
Without it, STEP is skipped with a clear message; everything else still runs.

## Verified so far (what was actually run)
* Unit/integration tests: 35 pass (STEP-with-OCP test runs when `AUTOPLANT_TEST_STEP_PYTHON` is set).
* **Public pilot data** (`scripts/fetch_pilot_data.sh`, then `python scripts/run_corpus.py pilot_data`):
  * Equinor DISC example P&ID (12 equipment, 29 piping segments incl. tees and boundary connections): reads, routes, QC passes in <1 s.
  * 100 of 259 public DEXPI example files produce a plant model; **all 100 route completely and pass QC**. The other 159 are
    not plant layouts (instrument/label/audit demos with no equipment or no piping) and are declined with a clear message.
* Synthetic scale tests (not real plants): 170 lines on 120 equipment routed in ~50 s with 0 QC errors using the rack.
* Real browser test of the whole team flow with two users: create project, edit, markup, history, live revision banner.
* STEP read back with OpenCASCADE: solid count and bounding box match the model; tag names present.

## Known limitations (read before relying on it)
* **Design rules are placeholders** (`standards.py`, `validated=False`). Replace with your design basis (company spec,
  ASME B31.3 project reqs, NORSOK/DEP...); profiles can be JSON. All outputs are stamped accordingly.
* The AVEVA E3D "standard" is not defined anywhere in this repo: matching it needs your drawing/isometric standards and a
  reference deliverable set from a past project. Nothing here copies AVEVA code, formats, icons or screens (clean-room).
* P&ID files rarely contain equipment sizes, line lengths or supports, so sizes/geometry are **assumed** (flagged as QC warnings).
  Pipe size from the P&ID is used when present, otherwise NPS 4 (warned); branch sizes are assumed.
* No pipe supports, structural design, HVAC, cable trays, stress analysis, spool/fabrication drawings, or P&ID PDF/DWG recognition yet.
* Pipe-rack: one straight rack along X, pipes up to ~NPS 14; rack steel is geometry only (no loads/sizing).
* Spec import reads our open table format. It does **not** read AVEVA's proprietary spec databases (no reverse engineering) - export your spec to CSV/Excel in this layout. Not yet modelled: pressure-temperature rating checks, spec breaks inside a line, tee run/branch dimensions unless you supply them, per-class support/vent/drain rules.
* Auth is simple (scrypt passwords, session tokens, roles). Put it behind HTTPS and your SSO/reverse proxy for real use.
* The team database is SQLite on one server (fine for a small team, not a multi-node deployment).

## Roadmap
1. Your real piping classes through the importer (the sample is only a template); in-app spec editor.
2. Reference-deliverable matching: drawing/isometric/BOM formats to your (E3D) standard.
3. Pipe supports, spool breakdown and fabrication drawings; structure beyond the rack.
4. Validate on a real project: your P&ID, line list, spec, equipment data and an engineer-built 3D model to compare against.
5. P&ID PDF/DWG recognition into DEXPI (public datasets: NORA, PID2Graph).

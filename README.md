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
| Drawings | `drawing/` | **One drawing engine -> SVG, PDF and DXF.** Fabrication isometrics (framed A3 sheet, title block, true-iso projection, aligned dimensions, flow arrows, valve symbols, numbered shop/field welds, bolted-joint marks, spools with field welds at a configurable max length, item balloons, material/cut/weld/spool lists, end coordinates, north arrow, QC status stamp) and general arrangement sheets (plan + 2 elevations, standard scale, grid, tags, line numbers). Everything that differs between companies is a **drawing standard** JSON |
| Outputs | `export/` | IFC4, STEP (solids + torus elbows + names), DXF, isometrics (SVG per line + PDF), BOM CSV + **material take-off (`mto.csv`, grouped by commodity code)**, QC report, **clash report (CSV/HTML)**, 3D viewer, `scene.json` |
| Editing | `editing.py` | move equipment / change elevation, line size/spec/valves, add/delete lines & equipment, force re-route. Only affected lines are re-routed, **QC always re-runs**, undo supported |
| Team | `db.py`, `web/projects.py` | SQLite: users (admin/editor/viewer), project members, numbered **revisions with conflict detection (409)**, restore, markups (anchored to elements/points, threaded, resolve), clash waivers (soft clashes only, comment required, audited), audit log |
| Web | `web/` | Workspace: model explorer, 3D view with picking, properties + edit panel, Review/Clashes/Markups/History/Spec/Output panels, live "new revision available" banner |

## Pipe supports (automatic first pass, span-checked, load-scheduled)

**What the market does (public sources, details below):** AVEVA E3D uses a *preliminary supports -> Support Designer* workflow with vendor catalogues
(Lisega, Carpenter & Paterson, Pipe Supports Ltd) and produces fabrication drawings + MTO; Hexagon's CADWorx Support Modeler feeds Isogen and CAESAR II;
Bentley's OpenPlant Support Engineering has a 22,000+ item vendor catalogue and AutoPIPE adds an AI "Support Optimizer"
(genetic algorithm) on the stress side. In everything I found, *placement itself is still largely manual* and checked later by a stress engineer;
academic work (e.g. GA-based support optimisation) treats it as a stress-driven optimisation problem. **AutoPlant's angle:** do the whole
first pass automatically - where, what type, what load, what steel, what it costs - with checks that cannot be skipped, then hand over to
structural/stress/procurement (support schedule, MTO, PCF) instead of starting from nothing.

* **Span** = min( beam formula x safety factor, published water-filled steel-pipe spacing ), or your company table (`max_span_mm` in the spec).
  Beam formula: continuous beam `L = sqrt(10 S Z / w)` (S = 15.86 MPa, the ASME B31.1 2,300 psi basis) and sag `L = (384 E I d / 5w)^(1/4)` (d = 2.5 mm),
  `w` = pipe + water/operating content + insulation. Hydrotest (water-filled) governs by default. Calculated spans track the published table within
  7-22% on the generous side, which is why the table caps them (4" = 14 ft and 12" = 23 ft are confirmed by public sources).
* **Placement rules** (all configurable in the design-rule profile): within a nozzle-distance of every equipment nozzle; near every valve (valve groups
  share supports; a stool under the valve when no pipe is free); near every elbow and branch tee; guides on long runs and on risers; an anchor on very long runs;
  even spacing otherwise. Supports never stand in a fitting, on the rack they sit on **beams**, off the rack they get a stanchion that must not run through
  equipment or another line (else the support is flagged "needs steel"). Vertical pipe counts half in the span (a stated simplification).
* **Rack bay is designed from the pipes**: the bay is set from the shortest allowable span of the lines that use the rack (default 3-6 m, `rack.bay_auto`),
  so small-bore lines do not end up with 6 m between beams. Where a beam still does not fit, the support is flagged "ADD INTERMEDIATE RACK BEAM".
* **Loads**: preliminary vertical load per support (operating and hydrotest, tributary length + valves) - verified to add up to the line's actual weight;
  guide/anchor lateral loads are *friction estimates*. **Not a stress analysis**: no thermal movement, no code stress, no nozzle loads.
* **Catalogue**: smallest adequate support from the imported class (`supports` table: kind, NPS range, rated kN, code); without one, a generic placeholder
  catalogue is used and overloads are warnings. With an imported catalogue an overload is a QC error.
* **QC (independent re-check)**: span, end overhang, supports inside fittings, valve support, rack-beam alignment, stanchion clash, catalogue load, steel needed.
* **Outputs**: `supports.csv` schedule, supports + stanchion steel in `mto.csv`, symbols and a SUPPORTS table on every isometric, markers on the GA sheets,
  IfcDiscreteAccessory + stanchion columns in the IFC, solids in the STEP, 3D view/table in the workspace, and **PCF** files (`pcf/<line>.pcf`) with
  SUPPORT records for Isogen / CAESAR II / AutoPIPE import (**test with your receiving tool and mapping** - PCF dialects differ).
* **Manual editing**: add / move / delete a support, or hand a line back to the generator; QC re-runs every time and cannot be bypassed;
  manual supports are dropped if their line is re-routed.

Sources: [AVEVA multi-discipline supports](https://www.vizio.cz/eng/download/AVEVA_Multi-Discipline_Supports.pdf) - [Hexagon CADWorx/CAESAR II support data transfer](https://aliresources.hexagon.com/cadworx-analysis-solutions/efficient-pipe-supports-data-transfer-with-caesar-ii) -
[Bentley OpenPlant Support Engineering](https://www.bentley.com/en/products/openplant-support-engineering/) - [Bentley AutoPIPE](https://www.bentley.com/en/products/autopipe/) -
[GA-based support arrangement (Chiba 1996)](https://www.osti.gov/biblio/404350) - [pipe support spacing tables](https://blog.projectmaterials.com/quick-answers/pipes/pipe-support-spacing-span-tables/) -
[PCF SUPPORT record notes](https://www.sstusa.com/downloads/ReadmeSupplement_v730.pdf) - [MSS SP-58 overview](https://blog.ansi.org/ansi/ansi-mss-sp-58-2025-pipe-hangers-supports/?amp=1).
The secondary sources disagree on some stress/deflection bases, so confirm the span criteria against your governing code.

## Drawings and the drawing standard
```bash
autoplant plant.xml -o out --outputs iso_pdf,iso_svg,iso_dxf,ga_pdf,ga_dxf --drawing-standard my_standard.json
```
Outputs: `isometrics.pdf` (one sheet per line), `iso/*.svg`, `iso_dxf/*.dxf`, `general_arrangement.pdf/.dxf`. Workspace -> **Draw** tab:
*Drawing standard...* uploads a JSON (company, project no., doc-number patterns, revision, sheet sizes, text/line sizes, switches for
dimensions/welds/balloons/coordinates/spools, spool max length, weld/spool numbering, general notes); *Standard template (JSON)* downloads the
built-in `iso_generic` to start from. A standard change keeps the geometry and regenerates all drawings (team projects: a revision, undoable).

**What "matching E3D format" means here:** the sheets follow widely used piping-isometric conventions and are fully configurable, but
nothing is copied from AVEVA (no AVEVA layouts, symbols, fonts or code, and no reverse engineering). To match *your* company's
E3D/ISODRAFT output exactly, send one finished isometric + one GA drawing (PDF/DXF) from a past project and the standard's fields
and symbol set can be tuned to it. Known limits: dimension placement is heuristic (a dimension can land on pipe in a congested loop);
isometrics are one sheet per line (no continuation sheets / spool-per-sheet yet); a very long plant is drawn on a single GA sheet
(no match-lines); no insulation/painting/tracing symbols, pipe supports or instrument tags yet.

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
* Unit/integration tests: 63 pass (STEP-with-OCP test runs when `AUTOPLANT_TEST_STEP_PYTHON` is set).
* **Public pilot data** (`scripts/fetch_pilot_data.sh`, then `python scripts/run_corpus.py pilot_data`):
  * Equinor DISC example P&ID (12 equipment, 29 piping segments incl. tees and boundary connections): reads, routes, QC passes in <1 s.
  * 100 of 259 public DEXPI example files produce a plant model; **all 100 route completely and pass QC**. The other 159 are
    not plant layouts (instrument/label/audit demos with no equipment or no piping) and are declined with a clear message.
* Drawings: all 791 isometric sheets for the 100 public P&ID models generate without errors (<2 s each); fabrication breakdown is checked for consistency (cut pieces + fittings = line length; spools sum to line length; no spool over the limit unless flagged).
* Supports: all 100 public P&ID models pass QC with supports; an 84-equipment / 97-line plant gets 1,177 supports in ~11 s with no support errors; support loads on every line add up to its weight; spans never exceed the allowable (or QC says why).
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
* No structural design beyond a generic stanchion/rack, HVAC, cable trays, stress analysis (supports use preliminary loads; no thermal/spring-hanger design; valve weights are estimated unless your spec gives them), or P&ID PDF/DWG recognition yet.
* Pipe-rack: one straight rack along X, pipes up to ~NPS 14; rack steel is geometry only (no loads/sizing).
* Spec import reads our open table format. It does **not** read AVEVA's proprietary spec databases (no reverse engineering) - export your spec to CSV/Excel in this layout. Not yet modelled: pressure-temperature rating checks, spec breaks inside a line, tee run/branch dimensions unless you supply them, per-class support/vent/drain rules.
* Auth is simple (scrypt passwords, session tokens, roles). Put it behind HTTPS and your SSO/reverse proxy for real use.
* The team database is SQLite on one server (fine for a small team, not a multi-node deployment).

## Roadmap
1. Your real piping classes through the importer (the sample is only a template); in-app spec editor.
2. Reference-deliverable matching: drawing/isometric/BOM formats to your (E3D) standard.
3. Stress-informed supports (thermal movement, spring hangers, nozzle loads; close the loop with CAESAR II/AutoPIPE results), support-steel design, spool-per-sheet fabrication drawings.
4. Validate on a real project: your P&ID, line list, spec, equipment data and an engineer-built 3D model to compare against.
5. P&ID PDF/DWG recognition into DEXPI (public datasets: NORA, PID2Graph).

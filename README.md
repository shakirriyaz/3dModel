# AutoPlant

Automated plant 3D design: give inputs, pick outputs, layout + routing + QC run without manual modelling.
Internal tool; CLI and web app. **Status: MVP / proof of concept - not for design use.**

## Quick start
```bash
pip install -e ".[dev]"
autoplant examples/separator_skid.json -o out          # CLI (exit code 2 if QC fails)
python -m autoplant.web.app                            # web UI on http://localhost:8000
pytest
```

## Pipeline
`input -> templates -> auto-layout -> auto-nozzles -> A* 3D routing -> QC gates -> exports`

| Stage | Module | Notes |
|---|---|---|
| Input | `io_neutral.py` (JSON), `io_dexpi.py` (DEXPI Proteus XML, experimental) | P&ID/line list/equipment list in a neutral model |
| Layout | `layout.py` | process-flow layers on a grid; nozzles generated facing partner equipment |
| Routing | `routing.py` | orthogonal voxel A*: bends penalty, min leg between elbows (LR elbow take-off), clearances, grade/headroom, rack level preference |
| QC | `qc.py` | independent re-check of final geometry: completeness, nozzle connectivity, orthogonality, min leg, equipment/pipe clash, spacing, headroom, valve fit. Failed QC stamps outputs `DRAFT - QC FAILED` and the CLI exits 2 |
| Outputs | `export/` | IFC4, DXF (plan+elevation), isometrics (SVG per line + PDF), BOM CSV, QC report (JSON/MD), self-contained 3D viewer |

## Known limitations (be aware)
- **Design rules are placeholders** (`standards.py`, `validated=False`). Replace with your design basis (company spec, ASME B31.3 project reqs, NORSOK/DEP...) - profiles can be supplied as JSON. Outputs are stamped accordingly.
- No branches/tees, no structural steel, supports, cable trays, HVAC, stress analysis, instruments, P&ID image recognition yet.
- No STEP/RVM export yet (needs OpenCascade). Equipment is simplified geometry (AABB for clash).
- Routing is pure-Python voxel A*: fine for a skid / small unit; **a full plant needs hierarchical/area-based routing** (see roadmap).
- DEXPI reader is untested on real project files.
- Pipe/valve catalog data is a small built-in table; real piping specs/catalogs must be imported.

## Roadmap
1. Validate on pilot project (real P&ID, line list, spec, equipment data, reference 3D model) -> measure against engineers' model.
2. Piping spec/catalog import (branch tables, fittings, valves, flanges).
3. Tees/branches, supports, structural/pipe rack generation.
4. Area/hierarchical routing (rack-first corridor routing, then detailed) for full-plant scale; faster core (Rust/C++ or numba).
5. Rule packs per standard with engineer sign-off; STEP/RVM/PDMS-neutral export.
6. P&ID PDF/DWG -> DEXPI digitisation (open datasets: NORA, PID2Graph).

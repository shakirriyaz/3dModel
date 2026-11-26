"""Piping specifications (classes) and component catalogue: data model, import and validation.

A *piping class* defines, for a range of sizes: the pipe (OD / wall / schedule / weight), elbows and other
fittings (centre-to-end), valves (face-to-face), flanges + gaskets + bolting, and the *branch table*
(which tee / olet is used for header x branch size). Import formats (all share the same tables):

  * a folder or .zip of CSV files, * an .xlsx workbook with one sheet per table, * a .json file.

Tables (CSV file name = sheet name):  classes, pipe, fittings, valves, flanges, branches
Lines starting with '#' in CSV files are comments.  Sizes are NPS inches (or a `dn` column in mm).
The built-in generic catalogue (catalog.py) is used ONLY when no spec has been imported.
"""
from __future__ import annotations
import csv, io, json, math, os, re, zipfile
from typing import Literal, Optional
from pydantic import BaseModel
from . import catalog

# ----------------------------------------------------------------------------- data model


class PipeRow(BaseModel):
    nps: float
    od: float
    wall: float
    schedule: str = ""
    weight_kg_m: Optional[float] = None
    code: str = ""
    description: str = ""


class FittingRow(BaseModel):
    kind: Literal["elbow90", "elbow45", "tee", "reducer", "cap"]
    nps: float
    nps2: Optional[float] = None
    takeoff: Optional[float] = None          # centre-to-end (mm); for tees: run half-length C
    takeoff2: Optional[float] = None         # tees: branch M
    code: str = ""
    description: str = ""
    weight_kg: Optional[float] = None


class ValveRow(BaseModel):
    type: str
    nps: float
    ftf: float                                # face-to-face (mm)
    rating: str = ""
    end: str = "FLG"
    weight_kg: Optional[float] = None
    code: str = ""
    description: str = ""


class FlangeRow(BaseModel):
    nps: float
    type: str = "WN"
    rating: str = ""
    bolts: Optional[int] = None
    bolt_size: str = ""
    weight_kg: Optional[float] = None
    code: str = ""
    description: str = ""
    gasket_code: str = ""
    gasket_description: str = ""


class BranchRow(BaseModel):
    header_nps: float
    branch_nps: float
    type: str = "tee"                         # tee | reducing_tee | weldolet | sockolet | stub_in | none
    run_c: Optional[float] = None
    branch_m: Optional[float] = None
    code: str = ""
    description: str = ""


class PipeSpec(BaseModel):
    code: str
    description: str = ""
    rating: str = ""
    material: str = ""
    corrosion_allowance: float = 0.0
    elbow_type: Literal["LR", "SR"] = "LR"
    pipe: list[PipeRow] = []
    fittings: list[FittingRow] = []
    valves: list[ValveRow] = []
    flanges: list[FlangeRow] = []
    branches: list[BranchRow] = []


class Issue(BaseModel):
    severity: Literal["error", "warning"]
    table: str
    row: Optional[int] = None
    message: str


class ImportReport(BaseModel):
    ok: bool
    source: str
    classes: dict[str, dict]
    issues: list[Issue]

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == "warning"]


# ----------------------------------------------------------------------------- accessors used by the engine

ELBOW_FACTOR = {"LR": 1.5, "SR": 1.0}      # centre-to-end = factor x NPS x 25.4 (ASME B16.9 90 deg elbows)


class SpecView:
    """Dimensions for one piping class; `spec=None` -> built-in generic catalogue."""

    def __init__(self, spec: Optional[PipeSpec]):
        self.spec = spec
        self.builtin = spec is None
        self._pipe = {r.nps: r for r in spec.pipe} if spec else {}
        self._elb = {f.nps: f for f in spec.fittings if f.kind == "elbow90"} if spec else {}
        self._valves = {(v.type.lower(), v.nps): v for v in spec.valves} if spec else {}
        self._flg = {}
        if spec:
            for f in spec.flanges:
                self._flg.setdefault(f.nps, f)
        self._br = {(b.header_nps, b.branch_nps): b for b in spec.branches} if spec else {}

    def _nearest(self, table: dict, nps: float):
        return table[min(table, key=lambda k: abs(k - nps))]

    def has_size(self, nps: float) -> bool:
        return self.builtin or nps in self._pipe

    def pipe_row(self, nps):
        if nps in self._pipe:
            return self._pipe[nps]
        return None

    def od(self, nps: float) -> float:
        if nps in self._pipe:
            return self._pipe[nps].od
        return catalog.od(nps)

    def wall(self, nps: float) -> float:
        if nps in self._pipe:
            return self._pipe[nps].wall
        return catalog.wall(nps)

    def weight_per_m(self, nps: float) -> float:
        r = self._pipe.get(nps)
        if r is not None and r.weight_kg_m:
            return r.weight_kg_m
        d, t = self.od(nps), self.wall(nps)
        return math.pi * (d - t) * t * 7.85e-6 * 1000

    def takeoff(self, nps: float) -> float:
        f = self._elb.get(nps)
        if f is not None and f.takeoff:
            return f.takeoff
        factor = ELBOW_FACTOR[self.spec.elbow_type] if self.spec else 1.5
        return factor * nps * 25.4

    def valve_ftf(self, vtype: str, nps: float) -> float:
        v = self._valves.get((vtype.lower(), nps))
        if v is not None:
            return v.ftf
        return catalog.valve_ftf(nps)

    def valve_row(self, vtype: str, nps: float):
        return self._valves.get((vtype.lower(), nps))

    def valve_allowed(self, vtype: str, nps: float) -> bool:
        return self.builtin or (vtype.lower(), nps) in self._valves

    def flange_row(self, nps):
        return self._flg.get(nps)

    def branch_row(self, header_nps: float, branch_nps: float) -> Optional[BranchRow]:
        """Branch-table entry; None when the table has none (caller decides how strict to be)."""
        return self._br.get((header_nps, branch_nps))

    def default_branch_type(self, header_nps: float, branch_nps: float) -> str:
        if branch_nps >= header_nps:
            return "tee"
        return "reducing_tee" if branch_nps * 2 >= header_nps else "weldolet"

    def tee_half_length(self, header_nps: float, branch_nps: float) -> float:
        b = self.branch_row(header_nps, branch_nps)
        if b is not None and b.run_c:
            return b.run_c
        return self.takeoff(header_nps)       # conservative default


class Dims:
    """Spec-resolved dimensions for one line."""

    def __init__(self, view: SpecView, nps: float):
        self.view, self.nps = view, nps

    @property
    def od(self):
        return self.view.od(self.nps)

    @property
    def r(self):
        return self.od / 2

    @property
    def wall(self):
        return self.view.wall(self.nps)

    @property
    def takeoff(self):
        return self.view.takeoff(self.nps)

    @property
    def weight_per_m(self):
        return self.view.weight_per_m(self.nps)

    def ftf(self, vtype: str):
        return self.view.valve_ftf(vtype, self.nps)


# ----------------------------------------------------------------------------- import

TABLES = ("classes", "pipe", "fittings", "valves", "flanges", "branches")
ALIASES = {
    "class": "class", "spec": "class", "piping_class": "class", "pipe_class": "class", "code": "code_c",
    "nps": "nps", "size": "nps", "size_in": "nps", "nps_in": "nps", "dn": "dn",
    "od": "od", "od_mm": "od", "outside_diameter": "od",
    "wall": "wall", "wall_mm": "wall", "wt": "wall", "wall_thickness": "wall", "thickness_mm": "wall",
    "schedule": "schedule", "sch": "schedule",
    "weight_kg_per_m": "weight_kg_m", "kg_m": "weight_kg_m", "weight_kg_m": "weight_kg_m",
    "weight_kg": "weight_kg", "weight": "weight_kg",
    "ftf": "ftf", "face_to_face": "ftf", "face_to_face_mm": "ftf", "ftf_mm": "ftf",
    "takeoff": "takeoff", "takeoff_mm": "takeoff", "centre_to_end": "takeoff", "center_to_end": "takeoff",
    "centre_to_end_mm": "takeoff", "center_to_end_mm": "takeoff", "run_c": "takeoff", "run_c_mm": "takeoff",
    "branch_m": "takeoff2", "branch_m_mm": "takeoff2",
    "description": "description", "desc": "description", "commodity_code": "code_c", "item_code": "code_c",
    "rating": "rating", "pressure_rating": "rating", "material": "material",
    "corrosion_allowance": "corrosion_allowance", "corrosion_allowance_mm": "corrosion_allowance", "ca": "corrosion_allowance",
    "elbow_type": "elbow_type", "elbow": "elbow_type",
    "kind": "kind", "type": "type", "end": "end", "end_connection": "end",
    "nps2": "nps2", "nps_2": "nps2", "branch_nps": "branch_nps", "header_nps": "header_nps",
    "bolts": "bolts", "bolt_count": "bolts", "bolt_size": "bolt_size",
    "gasket_code": "gasket_code", "gasket_description": "gasket_description",
    "min_nps": "min_nps", "max_nps": "max_nps",
}


def _norm_header(h: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")
    return ALIASES.get(k, k)


def _read_csv(text: str) -> list[dict]:
    lines = [l for l in text.lstrip("﻿").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        return []
    rd = csv.reader(lines)
    head = [_norm_header(h) for h in next(rd)]
    return [{head[i]: (v.strip() if v is not None else "") for i, v in enumerate(row) if i < len(head)} for row in rd]


def _tables_from_zip(data: bytes) -> dict[str, list[dict]]:
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            base = os.path.splitext(os.path.basename(n))[0].lower()
            if base in TABLES and n.lower().endswith(".csv"):
                out[base] = _read_csv(z.read(n).decode("utf-8-sig", errors="replace"))
    return out


def _tables_from_dir(path: str) -> dict[str, list[dict]]:
    out = {}
    for t in TABLES:
        p = os.path.join(path, t + ".csv")
        if os.path.exists(p):
            out[t] = _read_csv(open(p, encoding="utf-8-sig").read())
    return out


def _tables_from_xlsx(data: bytes) -> dict[str, list[dict]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        name = ws.title.strip().lower()
        if name not in TABLES:
            continue
        rows = [r for r in ws.iter_rows(values_only=True) if r and any(c is not None and str(c).strip() for c in r)]
        rows = [r for r in rows if not str(r[0] or "").lstrip().startswith("#")]
        if not rows:
            out[name] = []
            continue
        head = [_norm_header(str(h or "")) for h in rows[0]]
        out[name] = [{head[i]: ("" if v is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)).strip())
                      for i, v in enumerate(r) if i < len(head)} for r in rows[1:]]
    return out


def _tables_from_json(data: bytes) -> dict[str, list[dict]]:
    j = json.loads(data.decode("utf-8-sig"))
    return {k.lower(): [{_norm_header(a): ("" if b is None else str(b)) for a, b in r.items()} for r in v]
            for k, v in j.items() if k.lower() in TABLES}


def _num(rows_issue, table, i, row, key, required=False, positive=True):
    v = row.get(key, "")
    if v in ("", None):
        if required:
            rows_issue.append(Issue(severity="error", table=table, row=i, message=f"'{key}' is required"))
        return None
    try:
        x = float(str(v).replace(",", "."))
    except ValueError:
        rows_issue.append(Issue(severity="error", table=table, row=i, message=f"'{key}' is not a number: {v!r}"))
        return None
    if positive and x <= 0:
        rows_issue.append(Issue(severity="error", table=table, row=i, message=f"'{key}' must be > 0 (got {v})"))
        return None
    return x


def _size(issues, table, i, row, key="nps", dnkey="dn", required=True):
    """NPS inches from `nps` or `dn` (mm -> nearest standard NPS)."""
    v = _num(issues, table, i, row, key)
    if v is not None:
        return v
    dn = _num(issues, table, i, row, dnkey)
    if dn is not None:
        return min(catalog.PIPE_OD, key=lambda s: abs(s * 25.4 - dn))
    if required and not any(i_.row == i and i_.table == table and i_.severity == "error" for i_ in issues):
        issues.append(Issue(severity="error", table=table, row=i, message="size missing (use 'nps' in inches or 'dn' in mm)"))
    return None


def build_specs(tables: dict[str, list[dict]], source: str) -> tuple[dict[str, PipeSpec], ImportReport]:
    issues: list[Issue] = []
    err = lambda t, r, m: issues.append(Issue(severity="error", table=t, row=r, message=m))
    warn = lambda t, r, m: issues.append(Issue(severity="warning", table=t, row=r, message=m))
    for t in ("classes", "pipe"):
        if not tables.get(t):
            err(t, None, f"table '{t}' is missing or empty")
    specs: dict[str, PipeSpec] = {}
    for i, r in enumerate(tables.get("classes", []), 2):
        code = r.get("class") or r.get("code_c", "")
        if not code:
            err("classes", i, "class code is empty")
            continue
        if code in specs:
            err("classes", i, f"duplicate class {code!r}")
            continue
        et = (r.get("elbow_type") or "LR").upper()
        if et not in ELBOW_FACTOR:
            err("classes", i, f"elbow_type must be LR or SR (got {et!r})")
            et = "LR"
        ca = _num(issues, "classes", i, r, "corrosion_allowance", positive=False) or 0.0
        specs[code] = PipeSpec(code=code, description=r.get("description", ""), rating=r.get("rating", ""),
                               material=r.get("material", ""), corrosion_allowance=ca, elbow_type=et)
    ranges = {r.get("class"): (_num([], "classes", 0, r, "min_nps"), _num([], "classes", 0, r, "max_nps")) for r in tables.get("classes", [])}

    def cls(table, i, r):
        c = r.get("class", "")
        if c not in specs:
            err(table, i, f"unknown class {c!r} (not in 'classes')")
            return None
        return specs[c]

    seen = set()
    for i, r in enumerate(tables.get("pipe", []), 2):
        s = cls("pipe", i, r)
        nps, od, wall = _size(issues, "pipe", i, r), _num(issues, "pipe", i, r, "od", True), _num(issues, "pipe", i, r, "wall", True)
        if s is None or nps is None or od is None or wall is None:
            continue
        if wall >= od / 2:
            err("pipe", i, f"wall {wall} is not smaller than half the OD {od}")
            continue
        if (s.code, nps) in seen:
            err("pipe", i, f"duplicate pipe row for class {s.code} NPS {nps:g}")
            continue
        seen.add((s.code, nps))
        mn, mx = ranges.get(s.code, (None, None))
        if (mn and nps < mn) or (mx and nps > mx):
            warn("pipe", i, f"NPS {nps:g} is outside the class range {mn or ''}-{mx or ''}")
        s.pipe.append(PipeRow(nps=nps, od=od, wall=wall, schedule=r.get("schedule", ""),
                              weight_kg_m=_num(issues, "pipe", i, r, "weight_kg_m"),
                              code=r.get("code_c", ""), description=r.get("description", "")))
    for s in specs.values():
        if not s.pipe:
            err("pipe", None, f"class {s.code} has no pipe rows")
    seen = set()
    for i, r in enumerate(tables.get("valves", []), 2):
        s = cls("valves", i, r)
        nps, ftf = _size(issues, "valves", i, r), _num(issues, "valves", i, r, "ftf", True)
        t = (r.get("type") or "").strip().lower()
        if not t:
            err("valves", i, "valve 'type' is required (gate, globe, check, ball, ...)")
        if s is None or nps is None or ftf is None or not t:
            continue
        if (s.code, t, nps) in seen:
            err("valves", i, f"duplicate valve {t} NPS {nps:g} in class {s.code}")
            continue
        seen.add((s.code, t, nps))
        s.valves.append(ValveRow(type=t, nps=nps, ftf=ftf, rating=r.get("rating", s.rating), end=r.get("end", "FLG") or "FLG",
                                 weight_kg=_num(issues, "valves", i, r, "weight_kg"), code=r.get("code_c", ""), description=r.get("description", "")))
    for i, r in enumerate(tables.get("fittings", []), 2):
        s = cls("fittings", i, r)
        kind = (r.get("kind") or "").strip().lower()
        if kind not in ("elbow90", "elbow45", "tee", "reducer", "cap"):
            err("fittings", i, f"kind must be elbow90, elbow45, tee, reducer or cap (got {kind!r})")
            continue
        nps = _size(issues, "fittings", i, r)
        if s is None or nps is None:
            continue
        s.fittings.append(FittingRow(kind=kind, nps=nps, nps2=_num(issues, "fittings", i, r, "nps2"),
                                     takeoff=_num(issues, "fittings", i, r, "takeoff"), takeoff2=_num(issues, "fittings", i, r, "takeoff2"),
                                     code=r.get("code_c", ""), description=r.get("description", ""),
                                     weight_kg=_num(issues, "fittings", i, r, "weight_kg")))
    for i, r in enumerate(tables.get("flanges", []), 2):
        s = cls("flanges", i, r)
        nps = _size(issues, "flanges", i, r)
        if s is None or nps is None:
            continue
        b = _num(issues, "flanges", i, r, "bolts")
        s.flanges.append(FlangeRow(nps=nps, type=(r.get("type") or "WN").upper(), rating=r.get("rating", s.rating),
                                   bolts=int(b) if b else None, bolt_size=r.get("bolt_size", ""),
                                   weight_kg=_num(issues, "flanges", i, r, "weight_kg"), code=r.get("code_c", ""),
                                   description=r.get("description", ""), gasket_code=r.get("gasket_code", ""),
                                   gasket_description=r.get("gasket_description", "")))
    seen = set()
    for i, r in enumerate(tables.get("branches", []), 2):
        s = cls("branches", i, r)
        h = _num(issues, "branches", i, r, "header_nps", True)
        b = _num(issues, "branches", i, r, "branch_nps", True)
        if s is None or h is None or b is None:
            continue
        t = (r.get("type") or "tee").strip().lower().replace(" ", "_").replace("-", "_")
        if t not in ("tee", "reducing_tee", "weldolet", "sockolet", "threadolet", "stub_in", "none"):
            err("branches", i, f"unknown branch type {t!r}")
            continue
        if (s.code, h, b) in seen:
            err("branches", i, f"duplicate branch {h:g}x{b:g} in class {s.code}")
            continue
        seen.add((s.code, h, b))
        for nn in (h, b):
            if nn not in {p.nps for p in s.pipe}:
                warn("branches", i, f"NPS {nn:g} has no pipe row in class {s.code}")
        s.branches.append(BranchRow(header_nps=h, branch_nps=b, type=t, run_c=_num(issues, "branches", i, r, "takeoff"),
                                    branch_m=_num(issues, "branches", i, r, "takeoff2"), code=r.get("code_c", ""),
                                    description=r.get("description", "")))
    for s in specs.values():
        sizes = {p.nps for p in s.pipe}
        no_elb = sorted(n for n in sizes if not any(f.kind == "elbow90" and f.nps == n and f.takeoff for f in s.fittings))
        if no_elb:
            warn("fittings", None, f"class {s.code}: no 90° elbow row for NPS {', '.join(f'{n:g}' for n in no_elb)} - "
                                   f"derived {s.elbow_type} ({ELBOW_FACTOR[s.elbow_type]:g} x NPS)")
        if not s.valves:
            warn("valves", None, f"class {s.code} defines no valves - lines in this class cannot carry valves")
        if not s.flanges:
            warn("flanges", None, f"class {s.code} defines no flanges - generic flange descriptions will be used in the BOM")
        if not s.branches:
            warn("branches", None, f"class {s.code} has no branch table - a default tee/olet rule is applied")
    summary = {c: {"description": s.description, "rating": s.rating, "material": s.material, "sizes": sorted({p.nps for p in s.pipe}),
                   "pipe": len(s.pipe), "valves": len(s.valves), "fittings": len(s.fittings), "flanges": len(s.flanges),
                   "branches": len(s.branches)} for c, s in specs.items()}
    ok = not any(i.severity == "error" for i in issues)
    return (specs if ok else {}), ImportReport(ok=ok, source=source, classes=summary, issues=issues)


def load_spec(name: str, data: bytes) -> tuple[dict[str, PipeSpec], ImportReport]:
    """Import from the uploaded file's bytes; format chosen by extension."""
    ext = os.path.splitext(name.lower())[1]
    try:
        if ext == ".zip":
            tables = _tables_from_zip(data)
        elif ext in (".xlsx", ".xlsm"):
            tables = _tables_from_xlsx(data)
        elif ext == ".json":
            tables = _tables_from_json(data)
        else:
            raise ValueError("unsupported spec format - use a .zip of CSV files, an .xlsx workbook or a .json file")
    except (zipfile.BadZipFile, ValueError, KeyError, json.JSONDecodeError) as e:
        return {}, ImportReport(ok=False, source=name, classes={}, issues=[Issue(severity="error", table="file", message=str(e))])
    return build_specs(tables, name)


def load_spec_path(path: str) -> tuple[dict[str, PipeSpec], ImportReport]:
    if os.path.isdir(path):
        return build_specs(_tables_from_dir(path), os.path.basename(path.rstrip("/")))
    return load_spec(os.path.basename(path), open(path, "rb").read())


def export_json(specs: dict[str, PipeSpec]) -> dict:
    """Round-trippable JSON (same tables as the CSV/XLSX import)."""
    out = {t: [] for t in TABLES}
    for s in specs.values():
        out["classes"].append({"class": s.code, "description": s.description, "rating": s.rating, "material": s.material,
                               "corrosion_allowance": s.corrosion_allowance, "elbow_type": s.elbow_type})
        for p in s.pipe:
            out["pipe"].append({"class": s.code, **p.model_dump(exclude={"code"}), "code": p.code})
        for f in s.fittings:
            out["fittings"].append({"class": s.code, **f.model_dump()})
        for v in s.valves:
            out["valves"].append({"class": s.code, **v.model_dump()})
        for f in s.flanges:
            out["flanges"].append({"class": s.code, **f.model_dump()})
        for b in s.branches:
            out["branches"].append({"class": s.code, "header_nps": b.header_nps, "branch_nps": b.branch_nps, "type": b.type,
                                    "takeoff": b.run_c, "takeoff2": b.branch_m, "code": b.code, "description": b.description})
    return out

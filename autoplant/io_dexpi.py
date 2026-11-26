"""DEXPI Proteus XML reader (tested on the public Equinor DISC/NOAKA examples and the DEXPI
TrainingTestCases; still validate on your own project exports - vendor dialects differ).

What it builds from a P&ID:
* Equipment (+ nozzles) from <Equipment>/<Nozzle>; tag from TagNameAssignmentClass.
* Piping networks = connected groups of nozzles/components linked by <Connection>s inside
  <PipingNetworkSegment>s.  A network with
    - 2 nozzles  -> one line;
    - >=3 nozzles -> a header (first two nozzles) plus branch lines that tee into it;
    - 1 nozzle   -> a stub to the plant boundary (off-page connector, blind flange, open end).
* Valves from the components in the network; size / piping class from generic attributes when present.
Anything it had to assume is written to project.notes and shows up as QC warnings.
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
import networkx as nx
from .model import Project, Equipment, Nozzle, Line
from .catalog import PIPE_OD

DEFAULT_NPS = 4.0


def _loc(tag):
    return tag.rsplit("}", 1)[-1]


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", str(s))


def _attrs(el) -> dict:
    """Generic attributes of this element only (its direct <GenericAttributes> blocks)."""
    out = {}
    for blk in el:
        if _loc(blk.tag) != "GenericAttributes":
            continue
        for g in blk:
            if _loc(g.tag) == "GenericAttribute" and g.get("Name"):
                out.setdefault(g.get("Name"), (g.get("Value", ""), g.get("Units", "")))
    return out


def _kind(cls: str | None) -> str:
    c = (cls or "").lower()
    if "pump" in c:
        return "pump"
    if "heatexchanger" in c or "heater" in c or "cooler" in c or "condenser" in c or "reboiler" in c:
        return "exchanger"
    if "tank" in c:
        return "tank"
    if "column" in c:
        return "column"
    if "vessel" in c or "drum" in c or "separator" in c:
        return "vessel_vertical"
    return "generic"


def _valve(cls: str | None) -> str | None:
    c = (cls or "").lower()
    if "valve" not in c and "fitting" not in c:
        return None
    for key, name in (("gate", "gate"), ("ball", "ball"), ("check", "check"), ("butterfly", "butterfly"),
                      ("globe", "globe"), ("safety", "relief"), ("relief", "relief")):
        if key in c:
            return name
    return "gate"


def _nps(attrs: dict) -> float | None:
    """Nominal size in inches (NPS) from DEXPI generic attributes.
    Real files use: '14″' / '0.5″' (inches), 'DN150' (mm), and zero-padded 4-digit values like
    '0600' = 6.00 in. Representation (human readable) attributes are preferred over numeric ones."""
    def near(x):
        return min(PIPE_OD, key=lambda s: abs(s - x))

    named = [(k, v, u) for k, (v, u) in attrs.items() if "NominalDiameter" in k and v]
    named.sort(key=lambda t: ("Numerical" in t[0], "In" in t[0].replace("Instrument", "") and "Out" in t[0]))
    for k, v, unit in named:
        vl = v.strip().lower()
        m = re.search(r"\d+(?:\.\d+)?", vl)
        if not m:
            continue
        x = float(m.group())
        if "dn" in vl:
            return near(x / 25.4)
        if any(sym in v for sym in ("″", '"', "”")) or "inch" in vl or "nps" in vl or vl.endswith("in"):
            return near(x)
        if (unit or "").lower() in ("mm", "millimetre", "millimeter"):
            return near(x / 25.4)
        digits = re.sub(r"\D", "", v)
        if len(v.strip()) == 4 and digits == v.strip():       # '0600' -> 6.00 in
            return near(x / 100.0)
        return near(x if x <= 48 else x / 25.4)
    return None


def load_dexpi(path: str) -> Project:
    root = ET.parse(path).getroot()
    parent = {c: p for p in root.iter() for c in p}
    notes: list[str] = []

    # ---- equipment & nozzles ----
    equipment: list[Equipment] = []
    nozzle_owner: dict[str, tuple[str, str]] = {}
    used = set()
    for el in root.iter():
        if _loc(el.tag) != "Equipment":
            continue
        nozzles = [n for n in el if _loc(n.tag) == "Nozzle"]
        if not nozzles and not el.get("ComponentClass"):
            continue                         # drawing shape, not an equipment item
        a = _attrs(el)
        tag = a.get("TagNameAssignmentClass", ("", ""))[0] or el.get("TagName") or el.get("ComponentName") or el.get("ID")
        tag = _safe(tag)
        while tag in used:
            tag += "_"
        used.add(tag)
        eq = Equipment(id=tag, kind=_kind(el.get("ComponentClass")))
        for n in nozzles:
            nid = _safe(n.get("ID"))
            na = _nps(_attrs(n))
            eq.nozzles.append(Nozzle(id=nid, nps=na))
            nozzle_owner[n.get("ID")] = (tag, nid)
        equipment.append(eq)
    if not equipment:
        raise ValueError("No equipment found - unsupported DEXPI dialect?")

    # ---- piping networks ----
    G = nx.Graph()
    seg_of: dict[str, str] = {}              # node id -> first segment id
    comp_class: dict[str, str] = {}
    for seg in root.iter():
        if _loc(seg.tag) != "PipingNetworkSegment":
            continue
        sid = seg.get("ID")
        G.add_node(sid, seg=True)
        for c in seg:
            if _loc(c.tag) == "PipingComponent":
                comp_class[c.get("ID")] = c.get("ComponentClass") or ""
                G.add_edge(sid, c.get("ID"))
        for c in seg:
            if _loc(c.tag) == "Connection":
                for end in (c.get("FromID"), c.get("ToID")):
                    if end:
                        G.add_edge(sid, end)
                if c.get("FromID") and c.get("ToID"):
                    G.add_edge(c.get("FromID"), c.get("ToID"))
    seg_el = {s.get("ID"): s for s in root.iter() if _loc(s.tag) == "PipingNetworkSegment"}

    lines: list[Line] = []
    used_ids = set()
    assumed_size = 0
    for comp in sorted(nx.connected_components(G), key=lambda c: sorted(c)[0]):
        nozzles = sorted((n for n in comp if n in nozzle_owner), key=lambda n: int(re.sub(r"\D", "", n) or 0))
        segs = [s for s in comp if s in seg_el]
        if not nozzles or not segs:
            continue
        attrs, valves = {}, []
        for sid in sorted(segs):
            for k, v in _attrs(seg_el[sid]).items():
                attrs.setdefault(k, v)
            sysel = parent.get(seg_el[sid])
            if sysel is not None:
                for k, v in _attrs(sysel).items():
                    attrs.setdefault(k, v)
        for cid in sorted(comp):
            v = _valve(comp_class.get(cid))
            if v:
                valves.append(v)
        nps = _nps(attrs)
        if nps is None:
            nps = next((nozzle_nps for nozzle_nps in
                        (next((nz.nps for e in equipment for nz in e.nozzles if nz.id == _safe(n) and nz.nps), None)
                         for n in nozzles) if nozzle_nps), None)
        if nps is None:
            nps, assumed_size = DEFAULT_NPS, assumed_size + 1
        spec = next((v for k, (v, _) in attrs.items() if "PipingClassCode" in k and v), "A1")
        sysel = parent.get(seg_el[sorted(segs)[0]])
        base = _safe(_attrs(sysel).get("ItemTagAssignmentClass", ("", ""))[0] if sysel is not None else "") or "L"
        lid = f"{base}-{_safe(sorted(segs)[0])}"
        while lid in used_ids:
            lid += "_"
        used_ids.add(lid)
        refs = [f"{nozzle_owner[n][0]}.{nozzle_owner[n][1]}" for n in nozzles]
        if len(refs) == 1:
            lines.append(Line(id=lid, **{"from": refs[0]}, to="@FREE", nps=nps, spec=spec, valves=valves))
        else:
            lines.append(Line(id=lid, **{"from": refs[0]}, to=refs[1], nps=nps, spec=spec, valves=valves))
            for k, r in enumerate(refs[2:], 1):
                lines.append(Line(id=f"{lid}-B{k}", **{"from": r}, to="@" + lid, nps=min(nps, 2.0), spec=spec))
    if any(ln.branch_of for ln in lines):
        notes.append("branch line sizes assumed as min(header NPS, 2) - the P&ID file gives no branch size")
    if assumed_size:
        notes.append(f"{assumed_size} piping network(s) had no nominal diameter in the file; NPS {DEFAULT_NPS:g} assumed")
    if not lines:
        raise ValueError("No piping networks with nozzles found")
    # drop equipment that no line touches (keeps the layout compact)
    touched = {n.split(".")[0] for ln in lines for n in (ln.from_, ln.to) if not n.startswith("@")}
    equipment = [e for e in equipment if e.id in touched]
    prj = Project(name=root.get("Name") or path.rsplit("/", 1)[-1], equipment=equipment, lines=lines)
    prj.notes = notes
    return prj

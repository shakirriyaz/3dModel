"""EXPERIMENTAL tolerant reader for DEXPI Proteus XML P&IDs.

Extracts Equipment (+Nozzle) and PipingNetworkSegments that connect two nozzles/equipment.
Written from the public format description and a hand-made sample - it MUST be validated
against real project exports (vendor dialects differ). Unknown classes map to 'generic'.
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from .model import Project, Equipment, Nozzle, Line
from .catalog import PIPE_OD, DEXPI_CLASS_TO_KIND


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _attrs(el):
    out = {}
    for ga in el.iter():
        if _local(ga.tag) == "GenericAttribute":
            out[ga.get("Name", "")] = (ga.get("Value", ""), ga.get("Units", ""))
    return out


def _nps(attrs, default=4.0):
    for k, (v, unit) in attrs.items():
        if "NominalDiameter" in k:
            m = re.search(r"[\d.]+", v or "")
            if not m:
                continue
            x = float(m.group())
            u = (unit or "").lower()
            if "dn" in (v or "").lower() or u in ("mm", "millimetre", "millimeter"):
                x = x / 25.4
            return min(PIPE_OD, key=lambda s: abs(s - x))
    return default


def load_dexpi(path: str) -> Project:
    root = ET.parse(path).getroot()
    equipment, nz_owner, id_to_tag = [], {}, {}
    for el in root.iter():
        if _local(el.tag) != "Equipment":
            continue
        eid = el.get("TagName") or el.get("ID")
        cls = el.get("ComponentClass", "")
        e = Equipment(id=eid, kind=DEXPI_CLASS_TO_KIND.get(cls, "generic"))
        id_to_tag[el.get("ID")] = eid
        for nz in el:
            if _local(nz.tag) == "Nozzle":
                nid = nz.get("TagName") or nz.get("ID")
                e.nozzles.append(Nozzle(id=nid, nps=_nps(_attrs(nz), 0) or None))
                nz_owner[nz.get("ID")] = (eid, nid)
        equipment.append(e)
    lines = []
    for seg in root.iter():
        if _local(seg.tag) != "PipingNetworkSegment":
            continue
        refs = []
        for c in seg:
            if _local(c.tag) == "Connection":
                for k in ("FromID", "ToID"):
                    rid = c.get(k)
                    if rid in nz_owner:
                        refs.append(f"{nz_owner[rid][0]}.{nz_owner[rid][1]}")
                    elif rid in id_to_tag:
                        refs.append(id_to_tag[rid])
        refs = list(dict.fromkeys(refs))
        if len(refs) < 2:
            continue
        a = _attrs(seg)
        spec = next((v for k, (v, _) in a.items() if "PipingClassCode" in k and v), "A1")
        lid = seg.get("TagName") or next((v for k, (v, _) in a.items() if "LineNumber" in k or "TagName" in k), None) or seg.get("ID")
        lines.append(Line(id=lid, **{"from": refs[0]}, to=refs[1], nps=_nps(a), spec=spec))
    if not equipment or not lines:
        raise ValueError("No equipment/lines found - file may use an unsupported DEXPI dialect")
    return Project(name=root.get("Name") or path.rsplit("/", 1)[-1], equipment=equipment, lines=lines)

"""Drawing standard: every choice that differs between companies lives here (JSON-loadable).
Built-in profiles follow common piping-fabrication isometric conventions; they are NOT a copy of any vendor's
format. To match your company standard, export `iso_generic`, edit the JSON and upload it (workspace Draw tab / --drawing-standard)."""
from __future__ import annotations
import datetime, json, os
from typing import Literal, Optional
from pydantic import BaseModel

PAPER_MM = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841)}


class DrawingStandard(BaseModel):
    name: str = "iso_generic"
    description: str = "Generic piping fabrication isometric (ISO 128-style line conventions)"
    # --- sheet
    iso_sheet: Literal["A4", "A3", "A2", "A1", "A0"] = "A3"
    ga_sheet: Literal["A4", "A3", "A2", "A1", "A0"] = "A1"
    margin: float = 8.0                 # paper margin to the outer frame (mm)
    frame_gap: float = 5.0              # outer to inner frame (mm)
    # --- title block / identification
    company: str = "COMPANY NAME"
    client: str = ""
    project_no: str = ""
    doc_number_iso: str = "{project}-ISO-{line}-{sheet:02d}"
    doc_number_ga: str = "{project}-GA-{view}-{sheet:02d}"
    revision: str = "A"
    revision_note: str = "Issued for review"
    drawn_by: str = "AUTOPLANT"
    checked_by: str = ""
    approved_by: str = ""
    date: str = ""                      # empty -> today
    units: str = "mm"
    # --- text / lines (paper mm)
    font: str = "Helvetica"
    text_h: float = 2.5
    dim_h: float = 2.2
    title_h: float = 4.0
    w_pipe: float = 0.7
    w_thin: float = 0.25
    w_frame: float = 0.5
    # --- isometric content switches
    show_dimensions: bool = True
    show_flow_arrows: bool = True
    show_welds: bool = True
    show_balloons: bool = True
    show_coordinates: bool = True
    show_north: bool = True
    show_spools: bool = True
    # --- fabrication
    spool_max_length: float = 12000.0   # transport / shop limit; longer runs get field welds
    spool_fmt: str = "S{n}"
    weld_fmt: str = "W{n}"
    field_weld_prefix: str = "FW"
    min_dim_leg: float = 0.0            # do not dimension legs shorter than this (mm)
    notes: list[str] = [
        "ALL DIMENSIONS IN MILLIMETRES UNLESS NOTED.",
        "DIMENSIONS ARE CENTRE-LINE TO CENTRE-LINE OF FITTINGS.",
        "ELEVATIONS (EL) ARE TO PIPE CENTRE-LINE FROM PLANT DATUM.",
        "PLANT NORTH AS SHOWN. DRAWING NOT TO SCALE.",
        "FIELD WELDS ARE SHOWN WITH A FLAG AND PREFIX FW.",
    ]

    def paper(self, kind: str = "iso") -> tuple[float, float]:
        return PAPER_MM[self.iso_sheet if kind == "iso" else self.ga_sheet]

    def today(self) -> str:
        return self.date or datetime.date.today().isoformat()


BUILTIN = {
    "iso_generic": DrawingStandard(),
}


def get_standard(name_or_path: Optional[str]) -> DrawingStandard:
    if not name_or_path:
        return BUILTIN["iso_generic"]
    if name_or_path in BUILTIN:
        return BUILTIN[name_or_path]
    if os.path.isfile(name_or_path):
        return DrawingStandard(**json.load(open(name_or_path)))
    raise ValueError(f"unknown drawing standard {name_or_path!r}; built-in: {sorted(BUILTIN)}")

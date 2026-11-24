"""Neutral project model (what the pipeline consumes and produces). Units: mm."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict

Vec = tuple[float, float, float]
DIRS = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0),
        "+z": (0, 0, 1), "-z": (0, 0, -1)}


class Nozzle(BaseModel):
    id: str
    direction: Optional[Literal["+x", "-x", "+y", "-y", "+z", "-z"]] = None
    offset: Optional[Vec] = None      # from equipment base centre (x,y at centre, z at elevation)
    nps: Optional[float] = None


class Equipment(BaseModel):
    id: str
    kind: str = "generic"
    shape: Optional[Literal["box", "cylinder_v", "cylinder_h"]] = None
    size: Optional[Vec] = None        # bounding box sx, sy, sz
    elevation: Optional[float] = None
    position: Optional[tuple[float, float]] = None   # fixed centre x,y (else auto-placed)
    nozzles: list[Nozzle] = []

    def box(self) -> tuple[Vec, Vec]:
        assert self.position is not None and self.size is not None and self.elevation is not None
        (x, y), (sx, sy, sz), z = self.position, self.size, self.elevation
        return (x - sx / 2, y - sy / 2, z), (x + sx / 2, y + sy / 2, z + sz)

    def nozzle_pos(self, n: Nozzle) -> Vec:
        (x, y), z = self.position, self.elevation
        ox, oy, oz = n.offset
        return (x + ox, y + oy, z + oz)


class Line(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    from_: str = Field(alias="from")      # "EQUIP.NOZZLE" or "EQUIP"
    to: str
    nps: float
    spec: str = "A1"
    service: str = ""
    valves: list[str] = []                # e.g. ["gate", "check"]


class Project(BaseModel):
    name: str = "project"
    standard: str = "generic"
    grid_pitch: float = 250.0
    rack_elevation: Optional[float] = 4500.0
    max_height: float = 12000.0
    margin: float = 6000.0
    equipment: list[Equipment]
    lines: list[Line]

    def eq(self, eid: str) -> Equipment:
        for e in self.equipment:
            if e.id == eid:
                return e
        raise KeyError(eid)


class Route(BaseModel):
    line_id: str
    status: Literal["routed", "failed"]
    points: list[Vec] = []
    valve_positions: list[tuple[str, Vec]] = []
    note: str = ""


class QCItem(BaseModel):
    check: str
    severity: Literal["error", "warning"]
    subject: str
    message: str


class QCReport(BaseModel):
    passed: bool
    errors: int
    warnings: int
    checks_run: list[str]
    items: list[QCItem]
    standard: str
    standard_validated: bool


def split_ref(ref: str) -> tuple[str, Optional[str]]:
    if "." in ref:
        e, n = ref.split(".", 1)
        return e, n
    return ref, None

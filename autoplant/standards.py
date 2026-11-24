"""Design-rule profiles. ALL VALUES ARE PLACEHOLDERS (validated=False).

They must be replaced by the owner's piping design basis (company spec, ASME B31.3
project requirements, NORSOK/Shell DEP etc.) before any output is used for design.
Custom profiles can be supplied as JSON with the same keys.
"""
from __future__ import annotations
from pydantic import BaseModel


class Profile(BaseModel):
    name: str
    description: str = ""
    validated: bool = False
    pipe_gap: float = 150.0          # min surface-to-surface gap between pipes (mm)
    equip_clearance: float = 300.0   # min pipe surface to equipment (mm)
    equip_spacing: float = 3000.0    # min gap between equipment (layout)
    weld_gap: float = 100.0          # min straight pipe between fittings (mm)
    headroom: float = 2200.0         # no horizontal pipes between grade_band and headroom
    grade_band: float = 400.0
    grade_clearance: float = 150.0   # min pipe soffit above grade (mm)
    bend_penalty: float = 1500.0     # routing cost per bend (mm-equivalent)
    max_bends_per_line: int = 12


PROFILES = {
    "generic": Profile(name="generic", description="Generic oil & gas placeholder rules"),
    "compact_skid": Profile(name="compact_skid", description="Tighter skid-mounted placeholder rules",
                            pipe_gap=100, equip_clearance=200, equip_spacing=1800, headroom=2100),
}


def get_profile(name_or_path: str) -> Profile:
    if name_or_path in PROFILES:
        return PROFILES[name_or_path]
    import json, os
    if os.path.isfile(name_or_path):
        with open(name_or_path) as f:
            return Profile(**json.load(f))
    raise ValueError(f"Unknown standard profile {name_or_path!r}; available: {sorted(PROFILES)}")

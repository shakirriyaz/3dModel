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
    # --- pipe supports (all PLACEHOLDER engineering defaults; set your own design basis)
    supports_enabled: bool = True
    span_stress_mpa: float = 15.86             # bending stress limit for span (2,300 psi, ASME B31.1 water-filled basis)
    span_deflection_mm: float = 2.5            # sag limit
    span_safety: float = 0.9                   # factor on the calculated span
    span_use_reference_table: bool = True      # never allow more than the published water-filled steel-pipe spacing
    youngs_modulus_mpa: float = 200000.0
    content_density: dict = {"gas": 15.0, "vapor": 15.0, "vapour": 15.0, "steam": 8.0, "air": 1.2, "flare": 5.0,
                             "crude": 850.0, "oil": 850.0, "hydrocarbon": 700.0, "condensate": 700.0, "water": 1000.0}
    default_content_density: float = 1000.0    # unknown service: water-filled (conservative)
    hydrotest_governs: bool = True             # span uses the heavier of operating and water-filled
    insulation_thickness_mm: float = 0.0
    insulation_density: float = 150.0
    nozzle_support_max_mm: float = 2000.0      # first support at most this far from an equipment nozzle (and <= 0.5 span)
    valve_support_max_mm: float = 600.0        # target: support within this distance of a valve (NPS >= 3)
    valve_support_check_mm: float = 1500.0     # QC tolerance when short legs leave no room for the target
    elbow_support_max_factor: float = 0.5      # elbow within this fraction of the span from a support
    riser_guide_spacing_mm: float = 6000.0
    guide_every_n_supports: int = 3
    anchor_min_run_mm: float = 30000.0         # long straight run gets one anchor (confirm with stress analysis)
    friction_coefficient: float = 0.3
    stanchion_max_height_mm: float = 3000.0
    stanchion_section_mm: float = 250.0        # stanchion footprint used for clash checks


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

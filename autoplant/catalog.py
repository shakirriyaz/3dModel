"""Pipe, fitting and equipment reference data.

Pipe OD / Sch40 wall follow ASME B36.10M as commonly tabulated. Valve face-to-face
lengths are approximate (class 150 gate valve, ASME B16.10) - verify before issue.
"""
from __future__ import annotations
import math

PIPE_OD = {0.5: 21.3, 0.75: 26.7, 1: 33.4, 1.5: 48.3, 2: 60.3, 3: 88.9, 4: 114.3,
           6: 168.3, 8: 219.1, 10: 273.1, 12: 323.9, 14: 355.6, 16: 406.4,
           18: 457.2, 20: 508.0, 24: 609.6}
SCH40_WALL = {0.5: 2.77, 0.75: 2.87, 1: 3.38, 1.5: 3.68, 2: 3.91, 3: 5.49, 4: 6.02,
              6: 7.11, 8: 8.18, 10: 9.27, 12: 10.31, 14: 11.13, 16: 12.70,
              18: 14.27, 20: 15.09, 24: 17.48}
VALVE_FTF = {1: 108, 1.5: 165, 2: 178, 3: 203, 4: 229, 6: 267, 8: 292, 10: 330,
             12: 356, 14: 381, 16: 406, 18: 432, 20: 457, 24: 508}

# kind -> (shape, size(sx,sy,sz) mm, default elevation mm)
EQUIP_TEMPLATES = {
    "vessel_vertical": ("cylinder_v", (1800, 1800, 5000), 0),
    "vessel_horizontal": ("cylinder_h", (6000, 2000, 2000), 800),
    "pump": ("box", (1800, 800, 900), 0),
    "exchanger": ("cylinder_h", (5000, 900, 900), 600),
    "tank": ("cylinder_v", (8000, 8000, 8000), 0),
    "column": ("cylinder_v", (1500, 1500, 15000), 0),
    "generic": ("box", (2000, 2000, 2000), 0),
}
DEXPI_CLASS_TO_KIND = {"Vessel": "vessel_vertical", "Tank": "tank", "Pump": "pump",
                       "CentrifugalPump": "pump", "HeatExchanger": "exchanger",
                       "Column": "column", "ProcessColumn": "column"}


def _lookup(table, nps):
    if nps in table:
        return table[nps]
    keys = sorted(table)
    return table[min(keys, key=lambda k: abs(k - nps))]


def od(nps: float) -> float:
    return _lookup(PIPE_OD, nps)


def wall(nps: float) -> float:
    return _lookup(SCH40_WALL, nps)


def elbow_takeoff(nps: float) -> float:
    """90 deg long-radius elbow centre-to-end = 1.5 x NPS (mm)."""
    return 1.5 * nps * 25.4


def valve_ftf(nps: float) -> float:
    return _lookup(VALVE_FTF, nps)


def weight_per_m(nps: float) -> float:
    d, t = od(nps), wall(nps)
    return math.pi * (d - t) * t * 7.85e-6 * 1000  # kg/m

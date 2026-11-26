"""Generate examples/spec_sample/*.csv - a SAMPLE piping spec used as a template and for tests.
NOT a real piping class: pipe OD/wall are ASME B36.10M values, elbow centre-to-end is the standard LR/SR
formula, bolt counts follow ASME B16.5 class 150; valve lengths and all commodity codes are placeholders."""
import csv, os
from autoplant import catalog

OUT = os.path.join(os.path.dirname(__file__), "..", "examples", "spec_sample")
os.makedirs(OUT, exist_ok=True)
NOTE = ["# SAMPLE DATA - NOT A REAL PIPING CLASS. Replace with your company piping class before any real use."]
SIZES_A = [0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 10, 12]
SCH80 = {0.5: 3.73, 0.75: 3.91, 1: 4.55, 1.5: 5.08, 2: 5.54, 3: 7.62, 4: 8.56, 6: 10.97, 8: 12.70, 10: 15.09, 12: 17.48}
BOLTS150 = {0.5: (4, "1/2"), 0.75: (4, "1/2"), 1: (4, "1/2"), 1.5: (4, "1/2"), 2: (4, "5/8"), 3: (4, "5/8"), 4: (8, "5/8"),
            6: (8, "3/4"), 8: (8, "3/4"), 10: (12, "7/8"), 12: (12, "7/8")}


def write(name, header, rows):
    with open(os.path.join(OUT, name + ".csv"), "w", newline="") as f:
        for n in NOTE:
            f.write(n + "\n")
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


write("classes", ["class", "description", "rating", "material", "corrosion_allowance_mm", "elbow_type", "min_nps", "max_nps"], [
    ["A1", "Carbon steel 150# (SAMPLE)", "150#", "ASTM A106 Gr.B", 1.5, "LR", 0.5, 12],
    ["B2", "Carbon steel 300# heavy wall (SAMPLE)", "300#", "ASTM A106 Gr.B", 3.0, "LR", 0.5, 12],
    ["S1", "Stainless 316L 150# (SAMPLE)", "150#", "ASTM A312 TP316L", 0.0, "SR", 0.5, 10]])
pipe = []
for c, sizes, wall in (("A1", SIZES_A, catalog.SCH40_WALL), ("B2", SIZES_A, SCH80), ("S1", [s for s in SIZES_A if s <= 10], catalog.SCH40_WALL)):
    sch = {"A1": "SCH 40", "B2": "SCH 80", "S1": "SCH 40S"}[c]
    for n in sizes:
        pipe.append([c, n, catalog.PIPE_OD[n], wall[n], sch, "", f"PIPE-{c}-{n:g}", f"Pipe {n:g}in {sch} (SAMPLE code)"])
write("pipe", ["class", "nps", "od_mm", "wall_mm", "schedule", "weight_kg_per_m", "code", "description"], pipe)
fit = []
for c, sizes in (("A1", SIZES_A), ("B2", SIZES_A)):          # S1 deliberately has no rows -> derived SR elbows (shows the warning)
    for n in sizes:
        fit.append([c, "elbow90", n, "", round(1.5 * n * 25.4, 1), "", f"ELB90-{c}-{n:g}", f"90 deg LR elbow BW {n:g}in (SAMPLE code)"])
write("fittings", ["class", "kind", "nps", "nps2", "takeoff_mm", "takeoff2_mm", "code", "description"], fit)
valves = []
for c, types in (("A1", ("gate", "globe", "check", "ball")), ("B2", ("gate", "check")), ("S1", ("gate", "ball"))):
    for t in types:
        for n in [s for s in SIZES_A if s >= 1 and (c != "S1" or s <= 10)]:
            valves.append([c, t, n, catalog.VALVE_FTF.get(n, ""), {"A1": "150#", "B2": "300#", "S1": "150#"}[c], "FLG", "",
                           f"VLV-{t.upper()}-{c}-{n:g}", f"{t.title()} valve {n:g}in (SAMPLE - ftf is generic, verify)"])
write("valves", ["class", "type", "nps", "face_to_face_mm", "rating", "end", "weight_kg", "code", "description"], valves)
flg = []
for c in ("A1", "B2", "S1"):
    for n in [s for s in SIZES_A if c != "S1" or s <= 10]:
        b, bs = BOLTS150[n]
        flg.append([c, n, "WN", {"A1": "150#", "B2": "300#", "S1": "150#"}[c], b, bs, "", f"FLG-WN-{c}-{n:g}", f"Flange WN {n:g}in (SAMPLE code)",
                    f"GSK-{c}-{n:g}", f"Spiral wound gasket {n:g}in (SAMPLE code)"])
write("flanges", ["class", "nps", "type", "rating", "bolts", "bolt_size", "weight_kg", "code", "description", "gasket_code", "gasket_description"], flg)
br = []
for c in ("A1", "B2"):
    sizes = [s for s in SIZES_A if s >= 1]
    for h in sizes:
        for b in sizes:
            if b > h:
                continue
            t = "tee" if b == h else ("reducing_tee" if b * 2 >= h else "weldolet")
            br.append([c, h, b, t, "", "", f"BR-{t.upper()}-{c}-{h:g}x{b:g}", f"{t.replace('_', ' ')} {h:g}x{b:g} (SAMPLE code)"])
write("branches", ["class", "header_nps", "branch_nps", "type", "takeoff_mm", "takeoff2_mm", "code", "description"], br)
print("written to", os.path.abspath(OUT))

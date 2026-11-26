"""Clash report: every geometric QC finding with a stable id, 3D location, type and review status."""
from __future__ import annotations
import csv, hashlib, html, io
from .model import QCReport

CLASH_CHECKS = {"equipment_clash", "pipe_clash", "rack_clash", "self_clash", "equipment_overlap", "tee_geometry"}


def clash_id(key: str) -> str:
    return "C-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper()


def rows(report: QCReport, status: dict | None = None) -> list[dict]:
    """status: {key: {"status": "waived", "comment": ..., "user": ...}} (from the project database)."""
    status = status or {}
    out = []
    for i in report.items:
        if i.check not in CLASH_CHECKS:
            continue
        kind = "hard" if i.severity == "error" else "soft"
        a, _, b = i.subject.partition("/")
        st = status.get(i.key, {})
        out.append({"id": clash_id(i.key), "key": i.key, "check": i.check, "type": kind, "item_a": a, "item_b": b,
                    "x": None if i.at is None else i.at[0], "y": None if i.at is None else i.at[1],
                    "z": None if i.at is None else i.at[2], "message": i.message,
                    "status": st.get("status", "open"), "comment": st.get("comment", ""), "by": st.get("user", "")})
    out.sort(key=lambda r: (r["type"] != "hard", r["check"], r["id"]))
    return out


def to_csv(rs: list[dict]) -> str:
    buf = io.StringIO()
    cols = ["id", "type", "check", "item_a", "item_b", "x", "y", "z", "message", "status", "comment", "by"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rs)
    return buf.getvalue()


def to_html(rs: list[dict], name: str, stamp: str) -> str:
    hard = sum(1 for r in rs if r["type"] == "hard")
    soft = len(rs) - hard
    waived = sum(1 for r in rs if r["status"] == "waived")
    trs = "".join(
        f"<tr class='{r['type']}'><td>{r['id']}</td><td>{r['type']}</td><td>{html.escape(r['check'])}</td>"
        f"<td>{html.escape(r['item_a'])}</td><td>{html.escape(r['item_b'])}</td>"
        f"<td>{'' if r['x'] is None else f'{r['x']:.0f}, {r['y']:.0f}, {r['z']:.0f}'}</td>"
        f"<td>{html.escape(r['message'])}</td><td>{r['status']}</td><td>{html.escape(r['comment'])}</td></tr>"
        for r in rs)
    return f"""<!doctype html><meta charset="utf-8"><title>Clash report</title>
<style>body{{font:14px system-ui,Arial;margin:24px;color:#1a2230}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #d5dbe6;padding:4px 8px;text-align:left;font-size:13px}}th{{background:#f3f5f9}}
tr.hard td:nth-child(2){{color:#b3261e;font-weight:600}}tr.soft td:nth-child(2){{color:#9a6700}}</style>
<h1>Clash report - {html.escape(name)}</h1><p><b>{html.escape(stamp)}</b></p>
<p>{hard} hard clashes (errors), {soft} soft clashes / clearance warnings, {waived} waived. Waivers apply to warnings only.</p>
<table><tr><th>ID</th><th>Type</th><th>Check</th><th>Item A</th><th>Item B</th><th>Location (mm)</th><th>Finding</th><th>Status</th><th>Comment</th></tr>
{trs or '<tr><td colspan=9>No clashes.</td></tr>'}</table>"""

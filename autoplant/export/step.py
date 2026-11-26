"""STEP export via an isolated worker process (needs the `cadquery-ocp` wheel in some Python)."""
from __future__ import annotations
import os, subprocess, sys

WORKER = os.path.join(os.path.dirname(__file__), "step_worker.py")


class StepUnavailable(RuntimeError):
    pass


def _python() -> str:
    return os.environ.get("AUTOPLANT_STEP_PYTHON") or sys.executable


def available() -> tuple[bool, str]:
    try:
        r = subprocess.run([_python(), WORKER, "--check"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return (r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1] if r.returncode else "ok")


def write_step(scene_json: str, out_path: str):
    ok, msg = available()
    if not ok:
        raise StepUnavailable("STEP export needs OpenCASCADE: `pip install cadquery-ocp` in a separate venv and set "
                              f"AUTOPLANT_STEP_PYTHON to that python ({msg})")
    r = subprocess.run([_python(), WORKER, scene_json, out_path], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError("STEP export failed: " + (r.stderr or r.stdout).strip()[-400:])

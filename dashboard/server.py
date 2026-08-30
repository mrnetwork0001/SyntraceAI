"""SyntraceAI Mission Control - local dashboard server.

Serves the landing page, the dashboard SPA, and a small JSON API over the engine:

    GET  /               landing page
    GET  /app            Mission Control dashboard UI
    GET  /api/targets    report sets available (demo, humanize, humanize exhaustive)
    GET  /api/reports    latest baseline + mutation reports (?target=<id>)
    POST /api/run/{kind} launch `python main.py <kind>` (baseline | mutate | full)
    GET  /api/status     run state + live log tail

Usage: python dashboard/server.py [--port 8377]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import argparse
import json
import re
import subprocess
import threading
from collections import deque

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="SyntraceAI Mission Control")

VALID_KINDS = ("baseline", "mutate", "full")
LOG_LINES = 400

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "kind": None,
    "returncode": None,
    "log": deque(maxlen=LOG_LINES),
}


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _pump_output(proc: subprocess.Popen, kind: str) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        with _lock:
            _state["log"].append(line.rstrip("\n"))
    proc.wait()
    with _lock:
        _state["running"] = False
        _state["returncode"] = proc.returncode
        _state["log"].append(f"--- {kind} finished with exit code {proc.returncode} ---")


@app.get("/")
def landing() -> FileResponse:
    return FileResponse(Path(__file__).parent / "landing.html")


@app.get("/app")
def mission_control() -> FileResponse:
    return FileResponse(Path(__file__).parent / "index.html")


_TARGET_RE = re.compile(r"^[a-z0-9_]{0,40}$")


def _report_prefixes() -> list[str]:
    """Report-set prefixes present in reports/ ("" is the demo target)."""
    reports_dir = REPO_ROOT / "reports"
    prefixes: set[str] = set()
    for path in reports_dir.glob("*mutation_report.json") if reports_dir.is_dir() else []:
        prefixes.add(path.name[: -len("mutation_report.json")].rstrip("_"))
    return sorted(prefixes, key=lambda p: (p != "", p))


@app.get("/api/targets")
def targets() -> JSONResponse:
    labels = {"": "sample_app (demo)", "humanize": "humanize 4.16.0 (38-bank)",
              "humanize_full": "humanize 4.16.0 (exhaustive)"}
    return JSONResponse([
        {"id": prefix, "label": labels.get(prefix, prefix)} for prefix in _report_prefixes()
    ])


@app.get("/api/reports")
def reports(target: str = "") -> JSONResponse:
    if not _TARGET_RE.match(target):
        return JSONResponse({"error": "invalid target"}, status_code=400)
    prefix = f"{target}_" if target else ""
    baseline_prefix = "humanize_" if target.startswith("humanize") else prefix
    traj_dir = REPO_ROOT / "trajectories"
    trajectories = sorted(p.name for p in traj_dir.glob("*.json")) if traj_dir.is_dir() else []
    return JSONResponse({
        "target": target,
        "baseline": _read_json(REPO_ROOT / "reports" / f"{baseline_prefix}baseline_report.json"),
        "mutation": _read_json(REPO_ROOT / "reports" / f"{prefix}mutation_report.json"),
        "trajectories": trajectories,
    })


@app.post("/api/run/{kind}")
def run(kind: str) -> JSONResponse:
    if kind not in VALID_KINDS:
        return JSONResponse({"started": False, "error": f"unknown kind {kind!r}"}, status_code=400)
    with _lock:
        if _state["running"]:
            return JSONResponse({"started": False, "error": "a run is already in progress"}, status_code=409)
        _state.update(running=True, kind=kind, returncode=None)
        _state["log"].clear()
        _state["log"].append(f"--- launching: python main.py {kind} ---")
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "main.py"), kind],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={"PYTHONUNBUFFERED": "1", "TERM": "dumb", **__import__("os").environ},
    )
    threading.Thread(target=_pump_output, args=(proc, kind), daemon=True).start()
    return JSONResponse({"started": True, "kind": kind})


@app.get("/api/status")
def status() -> JSONResponse:
    with _lock:
        return JSONResponse({
            "running": _state["running"],
            "kind": _state["kind"],
            "returncode": _state["returncode"],
            "log_tail": list(_state["log"]),
        })


def main() -> int:
    parser = argparse.ArgumentParser(description="SyntraceAI Mission Control dashboard")
    parser.add_argument("--port", type=int, default=8377)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

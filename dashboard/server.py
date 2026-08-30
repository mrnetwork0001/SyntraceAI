"""SyntraceAI Mission Control - local dashboard server.

Serves the landing page, the dashboard SPA, and a small JSON API over the engine:

    GET  /               landing page
    GET  /app            Mission Control dashboard UI
    GET  /docs           documentation (Swagger API reference is at /api/docs)
    GET  /static/*       brand assets (logo lockup, mark, favicons)
    GET  /api/targets    report sets available (demo, humanize, humanize exhaustive)
    GET  /api/reports    latest baseline + mutation reports (?target=<id>)
    GET  /api/reset      what a reset of ?target=<id> would delete, and whether
                         that set is protected repo evidence
    POST /api/reset      delete that report set's saved artifacts
    GET  /api/presets    runnable project presets bundled with the repo
    POST /api/run/{kind} launch `python main.py <kind>` (baseline | mutate | full),
                         optionally against ?target=<path> (any local project)
    GET  /api/status     run state + live log tail

Two modes:

  local (default)  everything, including launching campaigns
  public           read-only. Set SYNTRACE_PUBLIC=1 (or pass --public) to serve a
                   world-reachable instance: no run, no reset, no filesystem paths.
                   The run endpoints execute the test suite of any directory they
                   are given and delete files, so they must never face the internet.

Usage: python dashboard/server.py [--port 8377] [--host 0.0.0.0] [--public]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import argparse
import json
import os
import re
import subprocess
import threading
from collections import deque
from datetime import datetime, timezone

from advanced.run_mutation import (
    HEALED_TEST_BASENAME,
    report_slug,
    sibling_output_paths,
)
from advanced.target_config import CONFIG_FILENAME, TargetConfigError, load_target_config

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# FastAPI serves its own Swagger UI at /docs by default, which would shadow the
# documentation page. The generated API reference moves to /api/docs.
app = FastAPI(title="SyntraceAI Mission Control", docs_url="/api/docs", redoc_url=None)

#: Read-only mode for a publicly reachable instance. The campaign endpoints copy a
#: named directory and run the test suite inside it, and the reset endpoint deletes
#: files; neither is safe to expose, and neither is needed to show a saved result.
PUBLIC_MODE = os.environ.get("SYNTRACE_PUBLIC") == "1"


def _public_refusal() -> JSONResponse:
    return JSONResponse(
        {
            "error": (
                "This is a read-only public instance: it can show saved reports but "
                "cannot run campaigns or delete anything. Clone the repository and run "
                "`python dashboard/server.py` locally for the full tool."
            )
        },
        status_code=403,
    )

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


def _read_report(path: Path) -> dict | None:
    """Read a report and stamp it with when it was written.

    The report file itself is deliberately byte-stable (no timestamps), so the
    age comes from the file's mtime. The dashboard needs it to say plainly that
    what you are looking at is a saved run rather than something live.
    """
    data = _read_json(path)
    if data is None:
        return None
    if PUBLIC_MODE:
        # A deployed instance serves files unpacked by the build, which stamps
        # them with a fixed mtime - Vercel uses 2018-10-20. Reporting that as
        # the measurement time told visitors the results were eight years old.
        # No honest age is available here, so none is claimed.
        return data
    try:
        stamped = dict(data)
        stamped["generated_at"] = (
            datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        )
        return stamped
    except OSError:
        return data


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


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.middleware("http")
async def always_revalidate(request, call_next):
    """Make the browser check before reusing anything it has cached.

    Nothing here sent a Cache-Control header, so browsers fell back to
    heuristic caching and would serve a stale page or logo after an edit -
    confusing during development and worse mid-demo. ETag and Last-Modified
    are still sent, so revalidation is a cheap 304 rather than a re-download.
    """
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/")
def landing() -> FileResponse:
    return FileResponse(Path(__file__).parent / "landing.html")


@app.get("/app")
def mission_control() -> FileResponse:
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/docs")
def docs() -> FileResponse:
    return FileResponse(Path(__file__).parent / "docs.html")


_TARGET_RE = re.compile(r"^[a-z0-9_]{0,40}$")


def _report_prefixes() -> list[str]:
    """Report-set prefixes present in reports/ ("" is the demo target).

    Both campaign and baseline-only reports count: a project audited with
    "Run Baseline Audit" alone must still be selectable, otherwise its run
    succeeds and the dashboard silently keeps showing another project.
    """
    reports_dir = REPO_ROOT / "reports"
    prefixes: set[str] = set()
    if reports_dir.is_dir():
        for suffix in ("mutation_report.json", "baseline_report.json"):
            for path in reports_dir.glob("*" + suffix):
                prefix = path.name[: -len(suffix)].rstrip("_")
                if _TARGET_RE.match(prefix):  # keep the listing and the route in sync
                    prefixes.add(prefix)
    return sorted(prefixes, key=lambda p: (p != "", p))


@app.get("/api/targets")
def targets() -> JSONResponse:
    labels = {"": "sample_app (demo)", "humanize": "humanize 4.16.0 (38-bank)",
              "humanize_full": "humanize 4.16.0 (exhaustive)"}
    return JSONResponse([
        {"id": prefix, "label": labels.get(prefix, prefix)} for prefix in _report_prefixes()
    ])


@app.get("/api/presets")
def presets() -> JSONResponse:
    """Runnable project presets bundled with the repo (targets/*)."""
    targets_dir = REPO_ROOT / "targets"
    out = []
    labels = {"sample_app": "Demo: AI ticket-triage app", "humanize": "humanize 4.16.0 (third-party)"}
    if not targets_dir.is_dir():
        return JSONResponse(out)
    # Demo first: it is the one a first-time visitor should run.
    ordered = sorted(targets_dir.iterdir(), key=lambda p: (p.name != "sample_app", p.name))
    for path in ordered:
        if not path.is_dir() or not (path / "tests").is_dir():
            continue
        rel = f"targets/{path.name}"
        out.append({
            "path": rel,
            "label": labels.get(path.name, path.name),
            "report_id": report_slug(rel),
        })
    return JSONResponse(out)


@app.get("/api/reports")
def reports(target: str = "") -> JSONResponse:
    if not _TARGET_RE.match(target):
        return JSONResponse({"error": "invalid target"}, status_code=400)
    if target not in _report_prefixes():
        return JSONResponse({"error": f"unknown report set {target!r}"}, status_code=404)
    prefix = f"{target}_" if target else ""
    traj_dir = REPO_ROOT / "trajectories"
    trajectories = sorted(p.name for p in traj_dir.glob("*.json")) if traj_dir.is_dir() else []
    # Only an exactly matching baseline report is paired with a campaign: the
    # exhaustive humanize set has no 253-bug baseline, so it shows none rather
    # than the frozen-bank baseline's incomparable 36/38.
    return JSONResponse({
        "target": target,
        "baseline": _read_report(REPO_ROOT / "reports" / f"{prefix}baseline_report.json"),
        "mutation": _read_report(REPO_ROOT / "reports" / f"{prefix}mutation_report.json"),
        "trajectories": trajectories,
    })


# --------------------------------------------------------------------------
# Reset: clearing a report set
#
# Everything the dashboard shows is read from reports/ on disk - there is no
# server-side session to clear. So a reset is a file operation, and the eight
# report files for the bundled targets are the committed evidence the README
# and CHANGELOG cite. Deleting those from a UI button would silently gut the
# repo's own claims, so the bundled sets are protected and only report sets
# from the user's own projects can be deleted.
# --------------------------------------------------------------------------


def _report_set_target(prefix: str) -> Path | None:
    """The target directory a report set was produced from.

    Taken from the report's own ``target`` field rather than re-derived from
    the filename: the slug is a one-way hash of the absolute path, so the
    report is the only thing that knows which project it came from.
    """
    pre = f"{prefix}_" if prefix else ""
    for kind in ("mutation", "baseline"):
        data = _read_json(REPO_ROOT / "reports" / f"{pre}{kind}_report.json")
        raw = (data or {}).get("target")
        if isinstance(raw, str) and raw.strip():
            path = Path(raw.strip()).expanduser()
            return path if path.is_absolute() else REPO_ROOT / path
    return None


def _is_protected(prefix: str) -> bool:
    """True if this report set must not be deleted through the dashboard.

    Protected means "produced from a target vendored in this repo" - the demo
    and humanize sets. A set whose target cannot be identified is protected
    too: refusing to delete is the recoverable mistake.
    """
    target = _report_set_target(prefix)
    if target is None:
        return True
    try:
        target.resolve().relative_to((REPO_ROOT / "targets").resolve())
        return True
    except (ValueError, OSError):
        return False


def _healed_test_path(target_dir: Path) -> Path | None:
    """The generated assertion file a campaign wrote into the target's suite."""
    try:
        config = load_target_config(target_dir)
    except (TargetConfigError, OSError):
        return None
    healed = target_dir / config.tests_dir / HEALED_TEST_BASENAME
    try:  # never step outside the target, whatever the adapter claims
        healed.resolve().relative_to(target_dir.resolve())
    except (ValueError, OSError):
        return None
    return healed


def _reset_paths(prefix: str) -> list[Path]:
    """Every artifact a run of this report set produced, existing or not.

    Kept in step with how the engine names its outputs: the JSON and HTML
    reports for both kinds, the trajectory derived from the campaign's JSON
    path, and the healed-test file written into the target's own suite.
    """
    reports_dir = REPO_ROOT / "reports"
    pre = f"{prefix}_" if prefix else ""
    paths = [
        reports_dir / f"{pre}{kind}_report{ext}"
        for kind in ("mutation", "baseline")
        for ext in (".json", ".html")
    ]
    campaign_json = f"reports/{pre}mutation_report.json"
    _, trajectory = sibling_output_paths(campaign_json, None, None)
    paths.append(REPO_ROOT / trajectory)

    target_dir = _report_set_target(prefix)
    if target_dir is not None and target_dir.is_dir():
        healed = _healed_test_path(target_dir)
        if healed is not None:
            paths.append(healed)
    return paths


def _describe(path: Path) -> str:
    """Repo-relative where possible, so the confirm dialog reads as file paths."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except (ValueError, OSError):
        return str(path)


def _reset_plan(target: str) -> dict:
    protected = _is_protected(target)
    target_dir = _report_set_target(target)
    return {
        "target": target,
        "target_path": _describe(target_dir) if target_dir else None,
        "protected": protected,
        "files": [] if protected else [_describe(p) for p in _reset_paths(target) if p.exists()],
    }


@app.get("/api/reset")
def reset_plan(target: str = "") -> JSONResponse:
    """What deleting this report set would remove - nothing is touched here."""
    if PUBLIC_MODE:  # the plan lists absolute paths on the host
        return _public_refusal()
    if not _TARGET_RE.match(target):
        return JSONResponse({"error": "invalid target"}, status_code=400)
    if target not in _report_prefixes():
        return JSONResponse({"error": f"unknown report set {target!r}"}, status_code=404)
    return JSONResponse(_reset_plan(target))


@app.post("/api/reset")
def reset(target: str = "") -> JSONResponse:
    """Delete one report set's saved artifacts."""
    if PUBLIC_MODE:
        return _public_refusal()
    if not _TARGET_RE.match(target):
        return JSONResponse({"error": "invalid target"}, status_code=400)
    if target not in _report_prefixes():
        return JSONResponse({"error": f"unknown report set {target!r}"}, status_code=404)
    with _lock:
        if _state["running"]:
            return JSONResponse(
                {"error": "a run is in progress - wait for it to finish"}, status_code=409
            )
    if _is_protected(target):
        return JSONResponse(
            {
                "error": (
                    "this report set came from a target bundled with the repo, and its "
                    "reports are the committed evidence the README cites - they are not "
                    "deletable from here. Use 'Clear the view', or delete the files with "
                    "git if you really mean to."
                )
            },
            status_code=403,
        )

    deleted, failed = [], []
    for path in _reset_paths(target):
        try:
            path.unlink()
        except FileNotFoundError:
            continue  # never ran, or already gone
        except OSError as exc:
            failed.append(f"{_describe(path)}: {exc.strerror or exc}")
            continue
        deleted.append(_describe(path))
    if failed:
        return JSONResponse({"deleted": deleted, "failed": failed}, status_code=500)
    return JSONResponse({"deleted": deleted})


@app.post("/api/run/{kind}")
def run(kind: str, target: str = "") -> JSONResponse:
    """Launch a campaign, optionally against the user's own project directory.

    ``target`` is a filesystem path (the tool is local-only and never uploads
    code, so a project is named by path, not supplied as an upload). It is
    passed to the CLI as a single argv element - never through a shell - and
    the report set it will write is returned as ``report_id`` so the UI can
    select it when the run finishes.
    """
    if PUBLIC_MODE:
        return _public_refusal()
    if kind not in VALID_KINDS:
        return JSONResponse({"started": False, "error": f"unknown kind {kind!r}"}, status_code=400)

    extra: list[str] = []
    report_id = ""
    label = f"python main.py {kind}"
    if target.strip():
        path = Path(target.strip()).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.is_dir():
            return JSONResponse(
                {"started": False, "error": f"not a directory: {path}"}, status_code=400
            )
        # Fail fast with something actionable rather than starting a campaign
        # that dies in the clean-suite gate with a wall of coverage warnings.
        if not (path / CONFIG_FILENAME).is_file() and not any(path.glob("*/*.py")):
            return JSONResponse(
                {
                    "started": False,
                    "error": (
                        f"{path} does not look like a Python project: no package with "
                        f".py files and no {CONFIG_FILENAME}. Point at the project root "
                        "(the directory containing your package and its tests)."
                    ),
                },
                status_code=400,
            )
        rel = str(path)
        try:  # keep repo-relative paths tidy in the log and reports
            rel = str(path.resolve().relative_to(REPO_ROOT))
        except ValueError:
            pass
        extra = ["--target", rel]
        report_id = report_slug(rel)
        label = f"python main.py {kind} --target {rel}"

    with _lock:
        if _state["running"]:
            return JSONResponse({"started": False, "error": "a run is already in progress"}, status_code=409)
        _state.update(running=True, kind=kind, returncode=None)
        _state["log"].clear()
        _state["log"].append(f"--- launching: {label} ---")
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "main.py"), kind, *extra],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={"PYTHONUNBUFFERED": "1", "TERM": "dumb", **os.environ},
    )
    threading.Thread(target=_pump_output, args=(proc, kind), daemon=True).start()
    return JSONResponse({"started": True, "kind": kind, "report_id": report_id})


@app.get("/api/config")
def config() -> JSONResponse:
    """What this instance allows, so the UI can present itself honestly."""
    return JSONResponse({"public": PUBLIC_MODE})


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
    parser.add_argument(
        "--public",
        action="store_true",
        help="read-only mode: serve saved reports but refuse to run or delete anything",
    )
    args = parser.parse_args()

    global PUBLIC_MODE
    PUBLIC_MODE = PUBLIC_MODE or args.public
    if args.host not in ("127.0.0.1", "localhost", "::1") and not PUBLIC_MODE:
        # Binding outside the loopback puts the run and reset endpoints on the
        # network. Refuse rather than let it happen by accident.
        parser.error(
            f"refusing to bind {args.host} without --public: the run endpoint executes "
            "the test suite of any directory it is given and reset deletes files. "
            "Use --public for a world-reachable instance."
        )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

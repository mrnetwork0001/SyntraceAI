"""Isolated per-mutant pytest execution (ARCHITECTURE.md §8).

Each evaluation copies the target project into a fresh temporary directory,
overwrites exactly one file with the patched source, and runs the target's
test suite there under a hard timeout. The sandbox directory is always
removed, even on crash or timeout. Exit codes map onto ``Outcome``:

    exit 0  -> SURVIVED   (suite green: the bug slipped through)
    exit 1  -> KILLED     (tests failed: the bug was detected)
    timeout -> TIMEOUT    (runaway mutant, killed by the resource guard)
    other   -> ERROR      (collection/import/internal error — still detected)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from advanced.core_types import Outcome, TestRunResult

#: Exact pytest invocation mandated by the contract (prefixed with sys.executable).
PYTEST_ARGS: tuple[str, ...] = ("-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider")

#: Maximum number of characters of combined stdout+stderr kept in ``stdout_tail``.
STDOUT_TAIL_CHARS = 2000

#: Sentinel exit code recorded when no real exit code exists (timeout / crash).
NO_EXIT_CODE = -1

_FAILED_LINE_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)


def _as_text(data: str | bytes | None) -> str:
    """Normalize subprocess output that may arrive as str, bytes, or None."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _combined_tail(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    """Last ``STDOUT_TAIL_CHARS`` characters of stdout and stderr combined."""
    parts = [text for text in (_as_text(stdout), _as_text(stderr)) if text]
    return "\n".join(parts)[-STDOUT_TAIL_CHARS:]


def _parse_failed_tests(output: str) -> list[str]:
    """Best-effort extraction of failed test ids from ``pytest -q`` output.

    Matches the ``FAILED path::name`` lines of the short-test-summary section;
    order is preserved and duplicates dropped.
    """
    seen: dict[str, None] = {}
    for match in _FAILED_LINE_RE.finditer(output):
        seen.setdefault(match.group(1))
    return list(seen)


def _classify_exit(exit_code: int) -> Outcome:
    """Map a pytest exit code onto the contract's Outcome semantics."""
    if exit_code == 0:
        return Outcome.SURVIVED
    if exit_code == 1:
        return Outcome.KILLED
    return Outcome.ERROR


def _run_pytest(cwd: Path, timeout_s: float) -> TestRunResult:
    """Run the frozen pytest command in ``cwd`` with a hard timeout."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [sys.executable, *PYTEST_ARGS]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run has already killed the child at this point.
        return TestRunResult(
            outcome=Outcome.TIMEOUT,
            duration_s=round(time.monotonic() - start, 3),
            exit_code=NO_EXIT_CODE,
            failed_tests=[],
            stdout_tail=_combined_tail(exc.stdout, exc.stderr),
        )
    duration = round(time.monotonic() - start, 3)
    combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    return TestRunResult(
        outcome=_classify_exit(proc.returncode),
        duration_s=duration,
        exit_code=proc.returncode,
        failed_tests=_parse_failed_tests(combined),
        stdout_tail=combined[-STDOUT_TAIL_CHARS:],
    )


def run_suite(project_dir: Path, *, timeout_s: float = 120.0) -> TestRunResult:
    """Run the test suite of ``project_dir`` in place (no copy) with a timeout.

    Used for the clean-suite gate: exit code 0 (outcome SURVIVED) means the
    suite is green on unmodified code.
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise FileNotFoundError(f"project directory does not exist: {project_dir}")
    return _run_pytest(project_dir, timeout_s)


def evaluate_patch(
    target_dir: Path,
    rel_path: str,
    patched_source: str,
    *,
    timeout_s: float = 60.0,
) -> TestRunResult:
    """Evaluate one patched file against the target's suite in a fresh sandbox.

    Copies ``target_dir`` to a temporary directory (skipping ``__pycache__``
    and ``.pytest_cache``), overwrites ``rel_path`` with ``patched_source``,
    runs pytest there, and removes the sandbox unconditionally.
    """
    target_dir = Path(target_dir)
    if not target_dir.is_dir():
        raise FileNotFoundError(f"target directory does not exist: {target_dir}")
    tmp_root = Path(tempfile.mkdtemp(prefix="syntrace_sbx_"))
    try:
        work = tmp_root / "work"
        shutil.copytree(
            target_dir,
            work,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        dest = (work / rel_path).resolve()
        if not dest.is_relative_to(work.resolve()):
            raise ValueError(f"rel_path escapes the sandbox: {rel_path!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(patched_source, encoding="utf-8")
        return _run_pytest(work, timeout_s)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def evaluate_many(
    target_dir: Path,
    items: list[tuple[str, str, str]],
    *,
    jobs: int | None = None,
    timeout_s: float = 60.0,
    on_result: Callable[[str, TestRunResult], None] | None = None,
) -> dict[str, TestRunResult]:
    """Evaluate many ``(item_id, rel_path, patched_source)`` patches in parallel.

    Runs ``evaluate_patch`` on a thread pool (the work is subprocess-bound so
    threads are the right tool). ``jobs`` defaults to ``min(8, cpu_count)``.
    ``on_result`` is invoked from the calling thread as each item completes —
    completion order, not submission order. A crash while evaluating one item
    is recorded as an ERROR result for that item and never affects the others.
    """
    if jobs is None:
        jobs = min(8, os.cpu_count() or 1)
    jobs = max(1, jobs)
    results: dict[str, TestRunResult] = {}
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures: dict[Future[TestRunResult], str] = {
            pool.submit(evaluate_patch, target_dir, rel_path, patched_source, timeout_s=timeout_s): item_id
            for item_id, rel_path, patched_source in items
        }
        for future in as_completed(futures):
            item_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # one bad item must not sink the batch
                result = TestRunResult(
                    outcome=Outcome.ERROR,
                    duration_s=0.0,
                    exit_code=NO_EXIT_CODE,
                    failed_tests=[],
                    stdout_tail=f"{type(exc).__name__}: {exc}"[-STDOUT_TAIL_CHARS:],
                )
            results[item_id] = result
            if on_result is not None:
                on_result(item_id, result)
    return results

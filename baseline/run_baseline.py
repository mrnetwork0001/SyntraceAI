"""Baseline auditor (ARCHITECTURE.md §11).

Measures, honestly and from a real run, how much of the frozen 50-bug
SyntraceAI bank the target's ORIGINAL test suite actually detects - and puts
that number next to the suite's line-coverage percentage (the "false
confidence gap").

Flow:
1. Copy the target to a tmp dir; run ``coverage run -m pytest`` there.
   Abort with exit code 2 unless the suite is fully green. Record total
   line coverage from ``coverage json``.
2. Build the frozen bank: 38 seeded AST code mutants + 12 prompt
   perturbations (the shared benchmark harness).
3. Evaluate every bug in an isolated sandbox against the original suite,
   with a live progress display.
4. Render the results with rich, write a JSON report, exit 0.

Usage (from repo root)::

    python baseline/run_baseline.py [--target targets/sample_app] [--jobs N]
                                    [--seed 1337] [--json reports/baseline_report.json]
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, NoReturn

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from advanced.ast_mutator import enumerate_mutants, select_bank
from advanced.core_types import (
    CODE_BANK_SIZE,
    DEFAULT_SEED,
    PROMPT_BANK_SIZE,
    Mutant,
    Perturbation,
    TestRunResult,
)
from advanced.prompt_perturbator import enumerate_perturbations
from advanced.run_mutation import coverage_run_command
from advanced.sandbox_runner import evaluate_many
from advanced.target_config import TargetConfig, load_target_config

_AUDIT_TIMEOUT_S = 300.0

_console = Console()
_err_console = Console(stderr=True)


def _abort(message: str) -> NoReturn:
    """Print a clear failure message and exit with code 2."""
    _err_console.print(f"[bold red]BASELINE ABORT:[/bold red] {message}")
    raise SystemExit(2)


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _resolve_target(raw: str) -> Path:
    """Resolve --target against the cwd, falling back to the repo root."""
    raw_path = Path(raw)
    candidates = [raw_path] if raw_path.is_absolute() else [Path.cwd() / raw, REPO_ROOT / raw]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    looked = ", ".join(str(c) for c in candidates)
    _abort(f"target directory not found: {raw!r} (looked in: {looked})")


def _run_coverage_audit(
    target_dir: Path, source_package: str, omit: tuple[str, ...] = ()
) -> tuple[float, int]:
    """Run the target's suite under coverage.py in a throwaway copy.

    Returns ``(total line coverage percent, passing test count)``. Aborts
    with exit code 2 if the suite is not fully green - a baseline audit of a
    broken suite would be meaningless.
    """
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory(prefix="syntrace_baseline_") as tmp:
        work = Path(tmp) / "target"
        shutil.copytree(
            target_dir, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
        )
        try:
            run = subprocess.run(
                # Same measurement scope as the advanced campaign (package
                # only, adapter excludes omitted) so the two numbers compare.
                coverage_run_command(source_package, omit),
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                timeout=_AUDIT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            _abort(f"coverage audit timed out after {_AUDIT_TIMEOUT_S:.0f}s")
        output = run.stdout + run.stderr
        if run.returncode != 0:
            _abort(
                "the target's test suite is NOT green on the pristine code "
                f"(pytest exit code {run.returncode}). Fix the suite before auditing. "
                f"Output tail:\n{output[-2000:]}"
            )
        match = re.search(r"(\d+) passed", output)
        tests_passed = int(match.group(1)) if match else 0
        try:
            cov = subprocess.run(
                [sys.executable, "-m", "coverage", "json", "-o", "coverage.json"],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                timeout=_AUDIT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            _abort(f"'coverage json' timed out after {_AUDIT_TIMEOUT_S:.0f}s")
        if cov.returncode != 0:
            _abort(
                f"'coverage json' failed (exit code {cov.returncode}): "
                f"{(cov.stdout + cov.stderr)[-2000:]}"
            )
        try:
            totals = json.loads((work / "coverage.json").read_text(encoding="utf-8"))["totals"]
            percent = float(totals["percent_covered"])
        except (OSError, KeyError, TypeError, ValueError) as exc:
            _abort(f"could not parse coverage.json totals: {exc}")
    return round(percent, 2), tests_passed


def _build_bank(
    target_dir: Path, seed: int, config: TargetConfig
) -> tuple[list[Mutant], list[Perturbation]]:
    """Build the frozen bug bank; abort if its composition is violated.

    Code-only targets (no prompt module in the adapter config) get the
    38-mutant code bank alone; prompt-capable targets add the 12 perturbations.
    """
    all_mutants = enumerate_mutants(target_dir)
    bank = select_bank(all_mutants, size=CODE_BANK_SIZE, seed=seed)
    perturbations = enumerate_perturbations(target_dir)
    if len(bank) != CODE_BANK_SIZE:
        _abort(
            f"frozen bank violated: expected {CODE_BANK_SIZE} code mutants, "
            f"got {len(bank)} (enumerated {len(all_mutants)} candidates)"
        )
    expected_prompts = PROMPT_BANK_SIZE if config.has_prompts else 0
    if len(perturbations) != expected_prompts:
        _abort(
            f"frozen bank violated: expected {expected_prompts} prompt perturbations, "
            f"got {len(perturbations)}"
        )
    return bank, perturbations


def _evaluate_bank(
    target_dir: Path,
    mutants: list[Mutant],
    perturbations: list[Perturbation],
    jobs: int | None,
    prompt_rel_path: str | None,
) -> dict[str, TestRunResult]:
    """Run every bank bug through the sandbox with a live progress display."""
    items: list[tuple[str, str, str]] = [
        (m.mutant_id, m.file_path, m.mutated_source) for m in mutants
    ]
    items += [
        (p.perturbation_id, str(prompt_rel_path), p.mutated_source) for p in perturbations
    ]
    detected_count = 0
    lock = threading.Lock()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=_console,
    ) as progress:
        task_id = progress.add_task(f"Injecting {len(items)} bugs", total=len(items))

        def on_result(item_id: str, result: TestRunResult) -> None:
            nonlocal detected_count
            with lock:
                if result.outcome.detected:
                    detected_count += 1
                progress.update(
                    task_id,
                    advance=1,
                    description=f"Injecting {len(items)} bugs - {detected_count} detected",
                )

        results = evaluate_many(target_dir, items, jobs=jobs, on_result=on_result)
    return results


def _summarize(
    mutants: list[Mutant],
    perturbations: list[Perturbation],
    results: dict[str, TestRunResult],
    *,
    target_label: str,
    seed: int,
    line_coverage_pct: float,
    tests_passed: int,
    wall_time_s: float,
) -> dict[str, Any]:
    """Aggregate sandbox results into the JSON report structure."""
    meta: list[tuple[str, str, str, str]] = [
        (m.mutant_id, "code", m.operator_name, m.location) for m in mutants
    ]
    meta += [
        (p.perturbation_id, "prompt", p.operator_name, p.location) for p in perturbations
    ]
    meta.sort(key=lambda row: row[0])

    per_kind: dict[str, dict[str, Any]] = {
        "code": {"total": 0, "detected": 0},
        "prompt": {"total": 0, "detected": 0},
    }
    per_operator: dict[str, dict[str, int]] = {}
    result_rows: list[dict[str, Any]] = []
    for item_id, kind, operator, location in meta:
        if item_id not in results:
            _abort(f"sandbox returned no result for bank item {item_id}")
        run = results[item_id]
        detected = run.outcome.detected
        per_kind[kind]["total"] += 1
        per_kind[kind]["detected"] += int(detected)
        op_counts = per_operator.setdefault(operator, {"total": 0, "detected": 0})
        op_counts["total"] += 1
        op_counts["detected"] += int(detected)
        result_rows.append(
            {
                "id": item_id,
                "kind": kind,
                "operator": operator,
                "location": location,
                "outcome": run.outcome.value,
                "detected": detected,
            }
        )

    for counts in per_kind.values():
        counts["detection_pct"] = _pct(counts["detected"], counts["total"])
    total = len(result_rows)
    detected_total = sum(1 for row in result_rows if row["detected"])
    return {
        "report": "baseline",
        "target": target_label,
        "seed": seed,
        "line_coverage_pct": line_coverage_pct,
        "tests_passed": tests_passed,
        "total": total,
        "detected": detected_total,
        "detection_pct": _pct(detected_total, total),
        "per_kind": per_kind,
        "per_operator": dict(sorted(per_operator.items())),
        "results": result_rows,
        "wall_time_s": round(wall_time_s, 2),
    }


def _render_report(report: dict[str, Any]) -> None:
    """Render the baseline audit with rich: summary, per-operator table, gap."""
    kinds = report["per_kind"]
    summary = Table(
        title=f"Baseline audit - original suite vs the frozen {report['total']}-bug bank"
    )
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Line coverage", f"{report['line_coverage_pct']:.1f}%")
    summary.add_row("Tests passed (green suite)", str(report["tests_passed"]))
    summary.add_row(
        "Bugs detected - overall",
        f"{report['detected']}/{report['total']} ({report['detection_pct']:.1f}%)",
    )
    summary.add_row(
        "Bugs detected - code mutants",
        f"{kinds['code']['detected']}/{kinds['code']['total']} "
        f"({kinds['code']['detection_pct']:.1f}%)",
    )
    summary.add_row(
        "Bugs detected - prompt perturbations",
        f"{kinds['prompt']['detected']}/{kinds['prompt']['total']} "
        f"({kinds['prompt']['detection_pct']:.1f}%)",
    )
    _console.print(summary)

    op_table = Table(title="Per-operator detection")
    op_table.add_column("Operator")
    op_table.add_column("Detected", justify="right")
    op_table.add_column("Total", justify="right")
    for name, counts in report["per_operator"].items():
        op_table.add_row(name, str(counts["detected"]), str(counts["total"]))
    _console.print(op_table)

    gap = report["line_coverage_pct"] - report["detection_pct"]
    sentence = (
        f"False-confidence gap: {report['line_coverage_pct']:.0f}% line coverage, "
        f"{report['detection_pct']:.0f}% bug detection "
        f"({report['detected']}/{report['total']} injected bugs caught by the original suite)."
    )
    _console.print(
        Panel(sentence, title="What coverage hides", border_style="red" if gap > 0 else "green")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_baseline.py",
        description=(
            "Audit a target's ORIGINAL test suite against the frozen SyntraceAI "
            "bug bank (38 code mutants, +12 prompt perturbations for prompt-capable "
            "targets) and report the false-confidence gap."
        ),
    )
    parser.add_argument(
        "--target",
        default="targets/sample_app",
        help="target project directory (default: %(default)s)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="parallel sandbox workers (default: min(8, CPU count))",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="bank selection seed (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default="reports/baseline_report.json",
        help="path of the JSON report to write (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    target_dir = _resolve_target(args.target)
    _console.print(f"[bold]SyntraceAI baseline audit[/bold] - target: {target_dir}")

    config = load_target_config(target_dir)
    _console.print(
        f"  package: {config.source_package}  prompts: "
        f"{'yes' if config.has_prompts else 'no (code-only bank)'}"
    )

    # The baseline audits the ORIGINAL suite: a healed-assertion file left
    # behind by a previous advanced campaign would inflate every number here.
    healed_path = target_dir / config.tests_dir / "test_healed_assertions.py"
    try:
        healed_path.resolve().relative_to(target_dir.resolve())
    except ValueError:
        _abort(f"tests dir resolves outside the target: {healed_path}")
    if healed_path.exists():
        healed_path.unlink()
        _console.print(
            "  removed previously generated healed-assertion suite "
            "(baseline measures the un-hardened suite)"
        )

    _console.print("Step 1/3: line-coverage audit of the pristine target ...")
    line_coverage_pct, tests_passed = _run_coverage_audit(
        target_dir, config.source_package, config.coverage_omit(target_dir)
    )
    _console.print(
        f"  suite is green: {tests_passed} tests passed, "
        f"line coverage {line_coverage_pct:.1f}%"
    )

    prompt_count = PROMPT_BANK_SIZE if config.has_prompts else 0
    _console.print(
        f"Step 2/3: building the frozen bank ({CODE_BANK_SIZE} code mutants + "
        f"{prompt_count} prompt perturbations, seed {args.seed}) ..."
    )
    mutants, perturbations = _build_bank(target_dir, args.seed, config)

    _console.print("Step 3/3: evaluating the bank against the ORIGINAL suite ...")
    results = _evaluate_bank(
        target_dir, mutants, perturbations, args.jobs, config.prompt_templates
    )

    wall_time_s = time.perf_counter() - started
    report = _summarize(
        mutants,
        perturbations,
        results,
        target_label=args.target,
        seed=args.seed,
        line_coverage_pct=line_coverage_pct,
        tests_passed=tests_passed,
        wall_time_s=wall_time_s,
    )
    _render_report(report)

    json_path = Path(args.json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _console.print(f"JSON report written to {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

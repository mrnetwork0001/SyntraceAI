"""SyntraceAI advanced campaign orchestrator.

Runs the full adversarial mutation campaign against a target project:

1. Clean-suite gate: the target's test suite must be green before injection.
2. Build the frozen bug bank (38 AST code mutants + 12 prompt perturbations).
3. Evaluate every bug in an isolated sandbox, in parallel.
4. Auto-heal survivors via differential input search; write hardened assertions.
5. Re-run survivors against the healed suite and report the final mutation score.

Usage: python advanced/run_mutation.py [--target targets/sample_app] [--seed 1337]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import argparse
import json
import shutil
import subprocess
import tempfile
import time

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from advanced import ast_mutator, prompt_perturbator, report, sandbox_runner, test_healer
from advanced.core_types import (
    CODE_BANK_SIZE,
    DEFAULT_SEED,
    PROMPT_BANK_SIZE,
    CampaignResult,
    Mutant,
    MutantResult,
    Outcome,
    Perturbation,
    TestRunResult,
)
from advanced.target_config import load_target_config
from advanced.trajectory_logger import TrajectoryLogger

HEALED_TEST_BASENAME = "test_healed_assertions.py"
DEFAULT_JSON = "reports/mutation_report.json"
DEFAULT_TRAJECTORY = "trajectories/agent_trace_02.json"

console = Console()


def sibling_output_paths(json_arg: str, html_arg: str | None, trajectory_arg: str | None) -> tuple[str, str]:
    """Derive the HTML report and trajectory paths from ``--json`` when not given.

    A custom ``--json`` (e.g. a second target's report) must never silently
    overwrite the default target's committed HTML report or trajectory, so the
    siblings follow the JSON path's stem unless set explicitly.
    """
    json_path = Path(json_arg)
    html = html_arg or str(json_path.with_suffix(".html"))
    if trajectory_arg:
        trajectory = trajectory_arg
    elif json_arg == DEFAULT_JSON:
        trajectory = DEFAULT_TRAJECTORY
    else:
        stem = json_path.stem.removesuffix("_mutation_report").removesuffix("_report")
        trajectory = f"trajectories/{stem}_trace.json"
    return html, trajectory


def coverage_run_command(source_package: str, omit: tuple[str, ...] = ()) -> list[str]:
    """coverage.py invocation measuring exactly the mutated scope.

    ``--source`` restricts measurement to the target package and ``--omit``
    drops the adapter's excluded modules, so the reported line coverage and
    the mutation score describe the same code.
    """
    command = [sys.executable, "-m", "coverage", "run", f"--source={source_package}"]
    if omit:
        command.append("--omit=" + ",".join(omit))
    return command + ["-m", "pytest", "-q"]


def measure_line_coverage(
    target_dir: Path,
    source_package: str,
    timeout_s: float = 180.0,
    omit: tuple[str, ...] = (),
) -> float | None:
    """Run coverage.py over the target suite in a scratch copy; return total percent."""
    tmp = Path(tempfile.mkdtemp(prefix="syntrace_cov_"))
    try:
        work = tmp / target_dir.name
        shutil.copytree(
            target_dir, work,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        run = subprocess.run(
            coverage_run_command(source_package, omit),
            cwd=work, capture_output=True, text=True, timeout=timeout_s,
        )
        if run.returncode != 0:
            return None
        subprocess.run(
            [sys.executable, "-m", "coverage", "json", "-o", "coverage.json"],
            cwd=work, capture_output=True, text=True, timeout=60,
        )
        data = json.loads((work / "coverage.json").read_text())
        return round(float(data["totals"]["percent_covered"]), 1)
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def to_result(
    bug_id: str, kind: str, operator: str, location: str, description: str,
    run: TestRunResult, phase: str,
) -> MutantResult:
    return MutantResult(
        mutant_id=bug_id,
        kind=kind,
        operator_name=operator,
        location=location,
        outcome=run.outcome,
        detected=run.outcome.detected,
        duration_s=round(run.duration_s, 2),
        failed_tests=run.failed_tests,
        phase=phase,
        description=description,
    )


def evaluate_bank(
    target_dir: Path,
    mutants: list[Mutant],
    perturbations: list[Perturbation],
    *,
    jobs: int | None,
    phase: str,
    progress_label: str,
    prompt_rel_path: str | None = None,
) -> list[MutantResult]:
    if perturbations and not prompt_rel_path:
        raise ValueError("prompt perturbations require the target's prompt module path")
    items: list[tuple[str, str, str]] = [
        (m.mutant_id, m.file_path, m.mutated_source) for m in mutants
    ] + [
        (p.perturbation_id, str(prompt_rel_path), p.mutated_source)
        for p in perturbations
    ]
    meta: dict[str, tuple[str, str, str, str]] = {}
    for m in mutants:
        meta[m.mutant_id] = ("code", m.operator_name, m.location, m.description)
    for p in perturbations:
        meta[p.perturbation_id] = ("prompt", p.operator_name, p.location, p.description)

    results: list[MutantResult] = []
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(progress_label, total=len(items))

        def on_result(item_id: str, run: TestRunResult) -> None:
            progress.advance(task)

        run_map = sandbox_runner.evaluate_many(
            target_dir, items, jobs=jobs, on_result=on_result,
        )

    for item_id, run in sorted(run_map.items()):
        kind, operator, location, description = meta[item_id]
        results.append(to_result(item_id, kind, operator, location, description, run, phase))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SyntraceAI adversarial mutation campaign")
    parser.add_argument("--target", default="targets/sample_app")
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--max-code-mutants", type=int, default=CODE_BANK_SIZE)
    parser.add_argument("--no-heal", action="store_true", help="skip auto-healing phase")
    parser.add_argument("--json", default=DEFAULT_JSON)
    parser.add_argument("--html", default=None,
                        help="default: the --json path with an .html suffix")
    parser.add_argument("--trajectory", default=None,
                        help="default: trajectories/agent_trace_02.json for the default "
                             "report path, otherwise trajectories/<json stem>_trace.json")
    parser.add_argument("--fail-under", type=float, default=None,
                        help="exit 1 if final mutation score falls below this percent")
    args = parser.parse_args(argv)
    args.html, args.trajectory = sibling_output_paths(args.json, args.html, args.trajectory)

    started = time.monotonic()
    target_dir = (REPO_ROOT / args.target).resolve()
    if not target_dir.is_dir():
        console.print(f"[red]Target not found:[/red] {target_dir}")
        return 2

    traj = TrajectoryLogger(
        REPO_ROOT / args.trajectory,
        task="Adversarial mutation campaign against target application",
        agent="SyntraceAI Advanced Engine",
    )

    config = load_target_config(target_dir)
    console.rule("[bold]SyntraceAI — Adversarial Mutation Campaign")
    console.print(
        f"Target: [cyan]{args.target}[/cyan]  Package: [cyan]{config.source_package}[/cyan]  "
        f"Prompts: {'yes' if config.has_prompts else 'no (code-only campaign)'}  "
        f"Seed: {args.seed}"
    )

    # Start every campaign from the un-hardened suite so runs are reproducible.
    healed_rel_path = Path(config.tests_dir) / HEALED_TEST_BASENAME
    healed_path = target_dir / healed_rel_path
    if healed_path.exists():
        healed_path.unlink()
        console.print("[dim]Removed previously generated healed-assertion suite for a clean run.[/dim]")

    # Step 1 — clean-suite gate.
    console.print("\n[bold]Step 1[/bold] — Clean-suite gate")
    gate = sandbox_runner.run_suite(target_dir)
    if gate.exit_code != 0:
        console.print("[red]ABORT: target test suite is not green before mutation.[/red]")
        console.print(gate.stdout_tail)
        return 2
    console.print(f"[green]Suite green[/green] in {gate.duration_s:.1f}s")
    line_cov = measure_line_coverage(
        target_dir, config.source_package, omit=config.exclude
    )
    if line_cov is not None:
        console.print(f"Baseline line coverage: [bold]{line_cov}%[/bold]")
    traj.log_step(
        "Verify target suite is green and measure baseline line coverage",
        "run_command",
        command="pytest -q && coverage run -m pytest",
        tool_output=f"suite green; line coverage {line_cov}%",
    )

    # Step 2 — build the frozen bug bank.
    console.print("\n[bold]Step 2[/bold] — Building the adversarial bug bank")
    all_mutants = ast_mutator.enumerate_mutants(target_dir)
    mutants = ast_mutator.select_bank(all_mutants, size=args.max_code_mutants, seed=args.seed)
    perturbations = prompt_perturbator.enumerate_perturbations(target_dir)
    console.print(
        f"AST mutation sites discovered: {len(all_mutants)} → bank of "
        f"[bold]{len(mutants)}[/bold] code mutants + [bold]{len(perturbations)}[/bold] "
        f"prompt perturbations = {len(mutants) + len(perturbations)} bugs"
    )
    traj.log_step(
        "Enumerate AST mutation sites and prompt perturbations; select frozen bank",
        "run_command",
        command=f"enumerate_mutants + select_bank(seed={args.seed})",
        tool_output=(
            f"{len(all_mutants)} candidate sites; bank = {len(mutants)} code + "
            f"{len(perturbations)} prompt bugs"
        ),
    )

    # Step 3 — sandboxed evaluation of the full bank.
    console.print("\n[bold]Step 3[/bold] — Sandboxed evaluation (original test suite)")
    original_results = evaluate_bank(
        target_dir, mutants, perturbations,
        jobs=args.jobs, phase="original_suite", progress_label="evaluating bank",
        prompt_rel_path=config.prompt_templates,
    )
    campaign = CampaignResult(
        target=args.target,
        seed=args.seed,
        line_coverage_pct=line_cov,
        total_bugs=len(original_results),
        code_mutants=len(mutants),
        prompt_perturbations=len(perturbations),
        original_results=original_results,
    )
    console.print(
        f"Detected [bold]{campaign.detected_original}/{campaign.total_bugs}[/bold] "
        f"({campaign.score_original:.1f}%) with the original suite"
    )
    traj.log_step(
        "Evaluate all injected bugs in isolated sandboxes with the original suite",
        "run_command",
        command="sandbox_runner.evaluate_many(bank)",
        tool_output=(
            f"pre-heal detection {campaign.detected_original}/{campaign.total_bugs} "
            f"({campaign.score_original:.1f}%)"
        ),
    )

    survived_ids = {r.mutant_id for r in original_results if r.outcome is Outcome.SURVIVED}
    surviving_mutants = [m for m in mutants if m.mutant_id in survived_ids]
    surviving_perts = [p for p in perturbations if p.perturbation_id in survived_ids]

    if not args.no_heal and survived_ids:
        # Step 4 — auto-heal survivors.
        console.print(
            f"\n[bold]Step 4[/bold] — Auto-healing {len(surviving_mutants)} code + "
            f"{len(surviving_perts)} prompt survivors"
        )
        healed, unhealable = test_healer.heal_survivors(
            target_dir, surviving_mutants, seed=args.seed,
        )
        prompt_tests = (
            test_healer.build_prompt_contract_tests(target_dir, surviving_perts)
            if surviving_perts else []
        )
        campaign.healed_tests = healed + prompt_tests
        campaign.unhealable_mutant_ids = unhealable
        console.print(
            f"Synthesized [bold]{len(campaign.healed_tests)}[/bold] hardened assertion tests "
            f"({len(unhealable)} survivor(s) classified likely-equivalent/unhealable)"
        )
        traj.log_step(
            "Differential input search over survivors; synthesize hardened assertions",
            "write_to_file",
            target=str(healed_rel_path),
            tool_output=(
                f"{len(campaign.healed_tests)} healed tests generated; "
                f"{len(unhealable)} unhealable"
            ),
        )

        if campaign.healed_tests:
            test_healer.write_healed_test_file(
                target_dir, campaign.healed_tests, tests_dir=config.tests_dir
            )

            # Gate again: healed tests must pass on the pristine target.
            healed_gate = sandbox_runner.run_suite(target_dir)
            if healed_gate.exit_code != 0:
                console.print("[red]ABORT: healed suite is not green on the pristine target.[/red]")
                console.print(healed_gate.stdout_tail)
                return 2

            # Step 5 — re-run survivors against the healed suite.
            console.print("\n[bold]Step 5[/bold] — Re-running survivors against the healed suite")
            campaign.rerun_results = evaluate_bank(
                target_dir, surviving_mutants, surviving_perts,
                jobs=args.jobs, phase="healed_suite", progress_label="re-running survivors",
                prompt_rel_path=config.prompt_templates,
            )
            traj.log_step(
                "Re-run surviving bugs against the auto-healed assertion suite",
                "run_command",
                command="sandbox_runner.evaluate_many(survivors, healed suite)",
                tool_output=(
                    f"final mutation score {campaign.detected_final}/{campaign.total_bugs} "
                    f"({campaign.score_final:.1f}%)"
                ),
            )

    campaign.wall_time_s = round(time.monotonic() - started, 1)

    json_path = REPO_ROOT / args.json
    html_path = REPO_ROOT / args.html
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    report.write_json(campaign, json_path)
    report.write_html(campaign, html_path)
    traj.save()
    console.print()
    report.render_terminal(campaign)

    def display(path: Path) -> str:
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return str(path)  # --json/--html may point outside the repo

    console.print(
        f"\nReports: [cyan]{display(json_path)}[/cyan], "
        f"[cyan]{display(html_path)}[/cyan]  "
        f"Trajectory: [cyan]{args.trajectory}[/cyan]"
    )

    summary = (
        f"Mutation Score: {campaign.score_final:.1f}% | "
        f"Injected Bugs Detected: {campaign.detected_final}/{campaign.total_bugs} | "
        f"Auto-Healed Assertion Tests Generated: {len(campaign.healed_tests)}"
    )
    console.print(f"\n[bold green]{summary}[/bold green]")

    if args.fail_under is not None and campaign.score_final < args.fail_under:
        console.print(f"[red]Final score below --fail-under={args.fail_under}[/red]")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

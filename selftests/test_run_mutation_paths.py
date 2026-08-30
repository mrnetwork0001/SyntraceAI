"""Selftests: sibling report/trajectory path derivation in run_mutation."""

from __future__ import annotations

from advanced.run_mutation import DEFAULT_JSON, DEFAULT_TRAJECTORY, sibling_output_paths


def test_default_json_keeps_demo_paths() -> None:
    html, traj = sibling_output_paths(DEFAULT_JSON, None, None)
    assert html == "reports/mutation_report.html"
    assert traj == DEFAULT_TRAJECTORY


def test_custom_json_never_touches_demo_artifacts() -> None:
    html, traj = sibling_output_paths("reports/humanize_mutation_report.json", None, None)
    assert html == "reports/humanize_mutation_report.html"
    assert traj == "trajectories/humanize_trace.json"


def test_full_campaign_stem() -> None:
    html, traj = sibling_output_paths("reports/humanize_full_mutation_report.json", None, None)
    assert html == "reports/humanize_full_mutation_report.html"
    assert traj == "trajectories/humanize_full_trace.json"


def test_explicit_values_win() -> None:
    html, traj = sibling_output_paths(
        "reports/x.json", "out/custom.html", "trajectories/agent_trace_09.json"
    )
    assert html == "out/custom.html"
    assert traj == "trajectories/agent_trace_09.json"

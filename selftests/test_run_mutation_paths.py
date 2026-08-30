"""Selftests: report path derivation in run_mutation (siblings + per-target)."""

from __future__ import annotations

import pytest

from advanced.run_mutation import (
    DEFAULT_JSON,
    DEFAULT_TRAJECTORY,
    default_json_path,
    report_slug,
    sibling_output_paths,
)


@pytest.mark.parametrize(
    ("target", "slug"),
    [
        ("targets/sample_app", ""),          # the demo owns the unprefixed names
        ("targets/sample_app/", ""),
        ("targets/humanize", "humanize"),
        ("/Users/me/My Project", "my_project"),
        ("~/code/foo/", "foo"),
        ("../weird name!!", "weird_name"),
    ],
)
def test_report_slug(target: str, slug: str) -> None:
    assert report_slug(target) == slug


def test_user_project_never_overwrites_demo_reports() -> None:
    assert default_json_path("targets/sample_app") == DEFAULT_JSON
    assert default_json_path("targets/sample_app", "baseline") == "reports/baseline_report.json"
    assert default_json_path("~/code/my-app") == "reports/my_app_mutation_report.json"
    assert default_json_path("~/code/my-app", "baseline") == "reports/my_app_baseline_report.json"
    # ...and its sibling html/trajectory follow it, not the demo's.
    html, traj = sibling_output_paths(default_json_path("~/code/my-app"), None, None)
    assert html == "reports/my_app_mutation_report.html"
    assert traj == "trajectories/my_app_trace.json"


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

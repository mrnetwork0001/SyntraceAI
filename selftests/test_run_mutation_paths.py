"""Selftests: report path derivation in run_mutation (siblings + per-target)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from advanced.run_mutation import (
    DEFAULT_JSON,
    DEFAULT_TRAJECTORY,
    default_json_path,
    report_slug,
    sibling_output_paths,
)


@pytest.mark.parametrize("target", ["targets/sample_app", "targets/sample_app/"])
def test_demo_keeps_the_unprefixed_report_names(target: str) -> None:
    assert report_slug(target) == ""


def test_only_the_real_demo_directory_gets_the_unprefixed_names(tmp_path: Path) -> None:
    """A project that merely happens to be NAMED sample_app must not collide.

    Regression: slugification keyed on the basename, so an outside project
    called sample_app - or any name with no ASCII alphanumerics at all - wrote
    straight over the committed demo evidence.
    """
    for name in ("sample_app", "Sample App", "SAMPLE-APP", "проект", "中文项目", "..."):
        outside = tmp_path / name
        outside.mkdir()
        assert report_slug(str(outside)) != "", name


def test_same_basename_different_projects_do_not_collide(tmp_path: Path) -> None:
    a, b = tmp_path / "one" / "my-app", tmp_path / "two" / "my_app"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert report_slug(str(a)) != report_slug(str(b))
    assert report_slug(str(a)) == report_slug(str(a))  # and stable


def test_slug_is_bounded_and_route_safe(tmp_path: Path) -> None:
    long_name = tmp_path / ("syntrace_customer_billing_service_backend_v2_" * 4)
    long_name.mkdir()
    slug = report_slug(str(long_name))
    assert len(slug) <= 40 and re.fullmatch(r"[a-z0-9_]+", slug), slug


def test_bundled_targets_keep_their_documented_names() -> None:
    assert report_slug("targets/humanize") == "humanize"
    assert report_slug("targets/sample_app") == ""


def test_user_project_never_overwrites_demo_reports() -> None:
    assert default_json_path("targets/sample_app") == DEFAULT_JSON
    assert default_json_path("targets/sample_app", "baseline") == "reports/baseline_report.json"
    user = default_json_path("~/code/my-app")
    assert user.startswith("reports/my_app_") and user.endswith("_mutation_report.json")
    assert user != DEFAULT_JSON
    # ...and its sibling html/trajectory follow it, not the demo's.
    html, traj = sibling_output_paths(user, None, None)
    assert html.endswith("_mutation_report.html") and html != "reports/mutation_report.html"
    assert traj.startswith("trajectories/") and traj != DEFAULT_TRAJECTORY


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

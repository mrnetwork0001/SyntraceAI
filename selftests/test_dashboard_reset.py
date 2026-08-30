"""Selftests for the dashboard reset endpoints (dashboard/server.py).

The reset button deletes files, and the eight report files for the bundled
targets are the committed evidence the README and CHANGELOG cite. These tests
pin the protection rule: a report set produced from a target vendored in this
repo - or one whose origin cannot be established - is never deletable through
the API, and only a user's own project can be cleared.

Every test runs against a fake repo root, so a failure can never reach the real
reports/ directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard import server


def _payload(response) -> dict | list:
    return json.loads(response.body)


@pytest.fixture(autouse=True)
def idle_state():
    """Reset endpoints refuse mid-run, so start every test from a known idle."""
    before = dict(server._state)
    server._state.update(running=False, kind=None, returncode=None)
    yield
    server._state.update(before)


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "reports").mkdir()
    (tmp_path / "trajectories").mkdir()
    (tmp_path / "targets").mkdir()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    return tmp_path


def _project(root: Path, name: str) -> Path:
    """A minimal project the target adapter accepts: app/ plus tests/."""
    target = root / name
    (target / "app").mkdir(parents=True)
    (target / "app" / "__init__.py").write_text("")
    (target / "tests").mkdir()
    return target


def _write_set(repo: Path, prefix: str, target: Path | str, *, healed: bool = True) -> None:
    pre = f"{prefix}_" if prefix else ""
    reports = repo / "reports"
    (reports / f"{pre}mutation_report.json").write_text(json.dumps({"target": str(target)}))
    (reports / f"{pre}mutation_report.html").write_text("<html></html>")
    stem = f"{pre}mutation_report".removesuffix("_mutation_report") or "agent_trace_02"
    trajectory = repo / "trajectories" / (
        "agent_trace_02.json" if not prefix else f"{stem}_trace.json"
    )
    trajectory.write_text("{}")
    if healed:
        tests_dir = Path(target) / "tests"
        if tests_dir.is_dir():
            (tests_dir / "test_healed_assertions.py").write_text("def test_x(): pass\n")


def test_bundled_target_report_set_is_protected(fake_repo: Path) -> None:
    _project(fake_repo / "targets", "sample_app")
    _write_set(fake_repo, "", "targets/sample_app")

    assert server._is_protected("") is True
    plan = _payload(server.reset_plan(target=""))
    assert plan["protected"] is True
    assert plan["files"] == []  # a protected set never even lists what it holds

    response = server.reset(target="")
    assert response.status_code == 403
    assert "committed evidence" in _payload(response)["error"]
    assert (fake_repo / "reports" / "mutation_report.json").exists()


def test_report_set_of_unknown_origin_is_protected(fake_repo: Path) -> None:
    """No ``target`` field means we cannot prove it is safe to delete."""
    (fake_repo / "reports" / "mystery_mutation_report.json").write_text(json.dumps({"seed": 1337}))

    assert server._is_protected("mystery") is True
    assert server.reset(target="mystery").status_code == 403
    assert (fake_repo / "reports" / "mystery_mutation_report.json").exists()


def test_external_project_report_set_is_deleted_with_its_healed_tests(fake_repo: Path) -> None:
    project = _project(fake_repo.parent / "outside", "myapp")
    _write_set(fake_repo, "myapp_ab12ef", project)
    healed = project / "tests" / "test_healed_assertions.py"
    assert healed.exists()

    plan = _payload(server.reset_plan(target="myapp_ab12ef"))
    assert plan["protected"] is False
    assert len(plan["files"]) == 4  # json, html, trajectory, healed tests

    response = server.reset(target="myapp_ab12ef")
    assert response.status_code == 200
    assert len(_payload(response)["deleted"]) == 4

    assert not (fake_repo / "reports" / "myapp_ab12ef_mutation_report.json").exists()
    assert not (fake_repo / "reports" / "myapp_ab12ef_mutation_report.html").exists()
    assert not (fake_repo / "trajectories" / "myapp_ab12ef_trace.json").exists()
    assert not healed.exists()
    # The project's own tests are the user's, not ours.
    assert (project / "tests").is_dir()
    assert (project / "app" / "__init__.py").exists()


def test_plan_lists_only_files_that_exist(fake_repo: Path) -> None:
    project = _project(fake_repo.parent / "outside2", "solo")
    _write_set(fake_repo, "solo_9f0011", project, healed=False)
    (fake_repo / "reports" / "solo_9f0011_mutation_report.html").unlink()

    files = _payload(server.reset_plan(target="solo_9f0011"))["files"]
    assert files == [
        "reports/solo_9f0011_mutation_report.json",
        "trajectories/solo_9f0011_trace.json",
    ]


def test_reset_refuses_while_a_campaign_is_running(fake_repo: Path) -> None:
    project = _project(fake_repo.parent / "outside3", "busy")
    _write_set(fake_repo, "busy_c0ffee", project)
    server._state["running"] = True

    response = server.reset(target="busy_c0ffee")
    assert response.status_code == 409
    assert "in progress" in _payload(response)["error"]
    assert (fake_repo / "reports" / "busy_c0ffee_mutation_report.json").exists()


@pytest.mark.parametrize("bad", ["../../etc", "Humanize", "a" * 41, "no-dashes"])
def test_reset_rejects_invalid_target_names(fake_repo: Path, bad: str) -> None:
    for call in (server.reset_plan, server.reset):
        assert call(target=bad).status_code == 400


def test_reset_rejects_a_report_set_that_does_not_exist(fake_repo: Path) -> None:
    for call in (server.reset_plan, server.reset):
        assert call(target="ghost").status_code == 404

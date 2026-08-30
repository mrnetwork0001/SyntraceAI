"""Selftests for the read-only public mode (dashboard/server.py).

A publicly reachable instance must not expose the two endpoints that act on the
host: ``/api/run`` copies a named directory and executes the test suite inside
it, and ``/api/reset`` deletes files. Neither is needed to display a saved
report, so both are refused - and so is the reset *plan*, which would otherwise
enumerate absolute paths on the host.

These are the tests that would fail if someone deployed the full app by
accident, so they assert the refusals directly rather than trusting the flag.
"""

from __future__ import annotations

import json

import pytest

from dashboard import server


def _payload(response) -> dict:
    return json.loads(response.body)


@pytest.fixture
def public(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "PUBLIC_MODE", True)


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "PUBLIC_MODE", False)


@pytest.mark.parametrize(
    "call",
    [
        lambda: server.run("mutate", target="targets/sample_app"),
        lambda: server.run("baseline", target="/etc"),
        lambda: server.reset(target=""),
        lambda: server.reset_plan(target=""),
    ],
)
def test_public_mode_refuses_every_acting_endpoint(public, call) -> None:
    response = call()
    assert response.status_code == 403
    assert "read-only" in _payload(response)["error"]


def test_public_mode_still_serves_saved_results(public) -> None:
    """The whole point of the public instance: the evidence stays readable."""
    assert server.config().status_code == 200
    assert _payload(server.config())["public"] is True
    assert server.targets().status_code == 200
    assert server.presets().status_code == 200
    reports = server.reports(target="")
    assert reports.status_code == 200
    assert _payload(reports)["mutation"]["score_final"] == 98.0


def test_local_mode_does_not_refuse(local) -> None:
    assert _payload(server.config())["public"] is False
    # Not started here - only that the public gate is not what answers.
    assert server.reset_plan(target="").status_code != 403


def test_run_endpoint_rejects_unknown_kind_before_doing_anything(local) -> None:
    assert server.run("rm-rf", target="targets/sample_app").status_code == 400


def test_public_mode_claims_no_measurement_time(public) -> None:
    """A deployed build's file mtimes are stamped by the packer, not by a run.

    Vercel uses a fixed 2018-10-20, which the dashboard rendered as "2872 days
    ago" - telling visitors the committed results were eight years old. No
    honest age is available on a deployed instance, so none is reported.
    """
    mutation = _payload(server.reports(target=""))["mutation"]
    assert "generated_at" not in mutation
    assert mutation["score_final"] == 98.0   # the result itself still shows


def test_local_mode_does_report_a_measurement_time(local) -> None:
    mutation = _payload(server.reports(target=""))["mutation"]
    assert "generated_at" in mutation

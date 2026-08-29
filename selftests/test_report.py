"""Selftests for advanced.report (ARCHITECTURE.md §10).

Builds a synthetic CampaignResult with mixed outcomes, a healed test, and an
unhealable mutant, then checks all three renderers: JSON parses and carries
the score fields, HTML is self-contained (operator names present, zero
external references), and the terminal renderer runs against an in-memory
rich console.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from advanced import report
from advanced.core_types import CampaignResult, HealedTest, MutantResult, Outcome


def _campaign() -> CampaignResult:
    original = [
        MutantResult(
            "M001", "code", "ComparisonOperatorSwap", "app/pricing.py:12",
            Outcome.KILLED, True, 0.41,
            ["tests/test_pricing.py::test_discount"], "original_suite", "`>=` -> `>`",
        ),
        MutantResult(
            "M002", "code", "ArithmeticOperatorSwap", "app/pricing.py:30",
            Outcome.SURVIVED, False, 0.52, [], "original_suite", "`+` -> `-`",
        ),
        MutantResult(
            "M003", "code", "BoundaryValueMutation", "app/scoring.py:9",
            Outcome.TIMEOUT, True, 5.0, [], "original_suite", "`100` -> `101`",
        ),
        MutantResult(
            "M004", "code", "ReturnValueMutation", "app/scoring.py:22",
            Outcome.SURVIVED, False, 0.5, [], "original_suite", "`return x` -> `return None`",
        ),
        MutantResult(
            "P001", "prompt", "RoleStripping", "prompt:SYSTEM_PROMPT",
            Outcome.SURVIVED, False, 0.6, [], "original_suite", "drop the TriageBot role line",
        ),
        MutantResult(
            "P002", "prompt", "FewShotDrop", "prompt:FEW_SHOT_BLOCK",
            Outcome.ERROR, True, 0.33, [], "original_suite", "FEW_SHOT_BLOCK -> ''",
        ),
    ]
    healed = [
        HealedTest(
            mutant_id="M002",
            function_name="apply_coupon",
            module="app.pricing",
            input_repr="(100.0, 50.0)",
            expected_repr="60.0",
            test_name="test_healed_M002_apply_coupon",
            test_source=(
                "def test_healed_M002_apply_coupon():\n"
                "    assert apply_coupon(100.0, 50.0) == pytest.approx(60.0)\n"
            ),
        )
    ]
    rerun = [
        MutantResult(
            "M002", "code", "ArithmeticOperatorSwap", "app/pricing.py:30",
            Outcome.KILLED, True, 0.48,
            ["tests/test_healed_assertions.py::test_healed_M002_apply_coupon"],
            "healed_suite", "`+` -> `-`",
        ),
        MutantResult(
            "P001", "prompt", "RoleStripping", "prompt:SYSTEM_PROMPT",
            Outcome.KILLED, True, 0.5,
            ["tests/test_healed_assertions.py::test_healed_prompt_contract_1"],
            "healed_suite", "drop the TriageBot role line",
        ),
    ]
    return CampaignResult(
        target="targets/sample_app",
        seed=1337,
        line_coverage_pct=91.2,
        total_bugs=6,
        code_mutants=4,
        prompt_perturbations=2,
        original_results=original,
        healed_tests=healed,
        unhealable_mutant_ids=["M004"],
        rerun_results=rerun,
        wall_time_s=42.5,
    )


def test_write_json_parses_and_carries_scores(tmp_path: Path) -> None:
    campaign = _campaign()
    out = tmp_path / "reports" / "campaign.json"  # parent dir is created on demand
    report.write_json(campaign, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["score_original"] == 50.0          # 3 of 6 detected pre-heal
    assert data["score_final"] == 83.3             # 5 of 6 detected post-heal
    assert data["detected_final"] == 5
    assert len(data["final_results"]) == 6
    finals = {r["mutant_id"]: r for r in data["final_results"]}
    assert finals["M002"]["outcome"] == "killed"
    assert finals["M002"]["phase"] == "healed_suite"
    assert finals["M004"]["outcome"] == "survived"
    assert data["unhealable_mutant_ids"] == ["M004"]


def test_write_html_self_contained(tmp_path: Path) -> None:
    campaign = _campaign()
    out = tmp_path / "campaign.html"
    report.write_html(campaign, out)
    doc = out.read_text(encoding="utf-8")
    assert doc.startswith("<!DOCTYPE html>")
    # Every operator in the campaign is named in the report.
    for operator in (
        "ComparisonOperatorSwap", "ArithmeticOperatorSwap", "BoundaryValueMutation",
        "ReturnValueMutation", "RoleStripping", "FewShotDrop",
    ):
        assert operator in doc
    # Survivor, healed test, and unhealable evidence.
    assert "M004" in doc
    assert "test_healed_M002_apply_coupon" in doc
    # Fully self-contained: no external references and no scripts.
    assert "http" not in doc.lower()
    assert "<script" not in doc.lower()
    assert "<style>" in doc


def test_render_terminal_runs_on_in_memory_console() -> None:
    campaign = _campaign()
    buffer = StringIO()
    console = Console(file=buffer, width=200)
    report.render_terminal(campaign, console=console)
    text = buffer.getvalue()
    assert "SyntraceAI" in text
    assert "ArithmeticOperatorSwap" in text
    assert "M004" in text                          # survivor listed
    assert "test_healed_M002_apply_coupon" in text  # healed test listed
    assert "83.3%" in text                          # final score shown


def test_empty_campaign_renders_without_errors(tmp_path: Path) -> None:
    campaign = CampaignResult(
        target="targets/empty",
        seed=1337,
        line_coverage_pct=None,
        total_bugs=0,
        code_mutants=0,
        prompt_perturbations=0,
    )
    report.write_json(campaign, tmp_path / "empty.json")
    report.write_html(campaign, tmp_path / "empty.html")
    console = Console(file=StringIO(), width=120)
    report.render_terminal(campaign, console=console)
    data = json.loads((tmp_path / "empty.json").read_text(encoding="utf-8"))
    assert data["score_final"] == 0.0
    doc = (tmp_path / "empty.html").read_text(encoding="utf-8")
    assert "http" not in doc.lower()


def test_gap_points() -> None:
    campaign = _campaign()
    assert report._gap_points(campaign) == pytest.approx(41.2)  # 91.2 - 50.0
    campaign.line_coverage_pct = None
    assert report._gap_points(campaign) is None

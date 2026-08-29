"""Frozen shared types for the SyntraceAI engine.

Single source of truth for every dataclass exchanged between modules.
See docs/ARCHITECTURE.md — do not redefine these anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

CODE_BANK_SIZE = 38
PROMPT_BANK_SIZE = 12
DEFAULT_SEED = 1337


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


class Outcome(str, Enum):
    KILLED = "killed"        # test suite failed (exit 1) — bug detected
    SURVIVED = "survived"    # test suite passed — bug slipped through
    TIMEOUT = "timeout"      # runaway mutant killed by resource guard — detected
    ERROR = "error"          # suite broke loudly (collection/internal error) — detected
    NOT_RUN = "not_run"

    @property
    def detected(self) -> bool:
        return self in (Outcome.KILLED, Outcome.TIMEOUT, Outcome.ERROR)


@dataclass
class Mutant:
    mutant_id: str            # "M001"… assigned after bank selection
    file_path: str            # relative to target root, e.g. "app/pricing.py"
    operator_name: str        # e.g. "ComparisonOperatorSwap"
    line_no: int
    col_offset: int
    function_name: str        # enclosing function qualname, "" if module level
    original_snippet: str
    mutated_snippet: str
    mutated_source: str       # full mutated file source (guaranteed to compile)
    description: str = ""

    @property
    def location(self) -> str:
        return f"{self.file_path}:{self.line_no}"


@dataclass
class Perturbation:
    perturbation_id: str      # "P001"…"P012"
    template_name: str        # constant name in app/prompt_templates.py
    operator_name: str
    description: str
    perturbed_template: str
    mutated_source: str       # full mutated prompt_templates.py source

    @property
    def location(self) -> str:
        return f"prompt:{self.template_name}"


@dataclass
class TestRunResult:
    outcome: Outcome
    duration_s: float
    exit_code: int
    failed_tests: list[str] = field(default_factory=list)
    stdout_tail: str = ""


@dataclass
class MutantResult:
    mutant_id: str
    kind: str                 # "code" | "prompt"
    operator_name: str
    location: str
    outcome: Outcome
    detected: bool
    duration_s: float
    failed_tests: list[str] = field(default_factory=list)
    phase: str = "original_suite"   # "original_suite" | "healed_suite"
    description: str = ""


@dataclass
class HealedTest:
    mutant_id: str
    function_name: str
    module: str               # e.g. "app.pricing"
    input_repr: str
    expected_repr: str
    test_name: str
    test_source: str


@dataclass
class CampaignResult:
    target: str
    seed: int
    line_coverage_pct: float | None
    total_bugs: int
    code_mutants: int
    prompt_perturbations: int
    original_results: list[MutantResult] = field(default_factory=list)
    healed_tests: list[HealedTest] = field(default_factory=list)
    unhealable_mutant_ids: list[str] = field(default_factory=list)
    rerun_results: list[MutantResult] = field(default_factory=list)
    wall_time_s: float = 0.0

    @property
    def detected_original(self) -> int:
        return sum(1 for r in self.original_results if r.detected)

    @property
    def score_original(self) -> float:
        if not self.original_results:
            return 0.0
        return 100.0 * self.detected_original / len(self.original_results)

    def final_results(self) -> list[MutantResult]:
        """Best-known result per bug: rerun result wins over the original one."""
        merged: dict[str, MutantResult] = {r.mutant_id: r for r in self.original_results}
        merged.update({r.mutant_id: r for r in self.rerun_results})
        return sorted(merged.values(), key=lambda r: r.mutant_id)

    @property
    def detected_final(self) -> int:
        return sum(1 for r in self.final_results() if r.detected)

    @property
    def score_final(self) -> float:
        finals = self.final_results()
        if not finals:
            return 0.0
        return 100.0 * self.detected_final / len(finals)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detected_original"] = self.detected_original
        data["score_original"] = round(self.score_original, 1)
        data["detected_final"] = self.detected_final
        data["score_final"] = round(self.score_final, 1)
        data["final_results"] = [asdict(r) for r in self.final_results()]
        return data

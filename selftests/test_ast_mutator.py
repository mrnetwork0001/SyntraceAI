"""Selftests for advanced/ast_mutator.py (ARCHITECTURE.md §6).

Builds a tiny fixture target inline in a tmp dir with hand-countable
mutation sites, then checks operator coverage, compile-cleanliness,
docstring preservation, qualified function names, determinism (two
enumerations byte-identical, even from two separate directories), and
select_bank stratification + ID assignment.
"""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from advanced.ast_mutator import (
    ALL_OPERATORS,
    enumerate_mutants,
    select_bank,
)
from advanced.core_types import Mutant

BILLING_SOURCE = '''"""Fixture module docstring."""

TAX_RATE = 0.25


def add_fee(amount: float, fee: float) -> float:
    """Docstring for add_fee."""
    return amount + fee


def tier(amount: float) -> str:
    if amount >= 50 and amount < 100:
        return "mid"
    if amount == 0:
        return "zero"
    return "low"


class Grader:
    """Class docstring."""

    def passing(self, score: int) -> bool:
        if score > 70:
            return True
        return False
'''

SCORING_SOURCE = '''"""Scoring helpers."""


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value into [low, high]."""
    if value < low:
        return low
    if value > high:
        return high
    return value
'''

PROMPT_TEMPLATES_SOURCE = '''THRESHOLD = 5
PROMPT = "Priority must be an integer from 1 (lowest) to 5 (critical)."
'''

INIT_SOURCE = "VERSION_MAJOR = 3\n"

TESTS_HELPER_SOURCE = "LIMIT = 9\n"

# Per-operator counts for BILLING_SOURCE after cross-operator deduplication:
# ConstantMutation's n+1 on the comparison constants 50/100/0/70 renders the
# same file as BoundaryValueMutation's +1 there, and the sort key places
# BoundaryValueMutation first, so those four ConstantMutation sites drop out.
EXPECTED_BILLING_COUNTS = {
    "ArithmeticOperatorSwap": 1,   # amount + fee
    "ComparisonOperatorSwap": 4,   # >=50, <100, ==0, >70
    "BooleanOperatorSwap": 1,      # and
    "ConditionNegation": 3,        # three if statements
    "ConstantMutation": 3,         # 0.25 -> 1.25, True -> False, False -> True
    "BoundaryValueMutation": 8,    # (50, 100, 0, 70) x (+1, -1)
    "ReturnValueMutation": 6,      # six non-None returns
}
EXPECTED_SCORING_COUNTS = {
    "ComparisonOperatorSwap": 2,   # < low, > high
    "ConditionNegation": 2,
    "ReturnValueMutation": 3,
}


def build_target(root: Path) -> Path:
    """Write the fixture target project under *root* and return it."""
    app = root / "app"
    (app / "tests").mkdir(parents=True)
    (app / "__init__.py").write_text(INIT_SOURCE, encoding="utf-8")
    (app / "billing.py").write_text(BILLING_SOURCE, encoding="utf-8")
    (app / "scoring.py").write_text(SCORING_SOURCE, encoding="utf-8")
    (app / "prompt_templates.py").write_text(PROMPT_TEMPLATES_SOURCE, encoding="utf-8")
    (app / "tests" / "helper.py").write_text(TESTS_HELPER_SOURCE, encoding="utf-8")
    return root


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    return build_target(tmp_path / "target")


@pytest.fixture()
def mutants(target: Path) -> list[Mutant]:
    return enumerate_mutants(target)


def serialize(bank: list[Mutant]) -> str:
    return json.dumps([asdict(m) for m in bank], sort_keys=True)


def counts_by_operator(bank: list[Mutant], file_path: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in bank:
        if m.file_path == file_path:
            out[m.operator_name] = out.get(m.operator_name, 0) + 1
    return out


def find(bank: list[Mutant], **fields: object) -> list[Mutant]:
    return [m for m in bank if all(getattr(m, k) == v for k, v in fields.items())]


class TestEnumeration:
    def test_per_operator_counts(self, mutants: list[Mutant]) -> None:
        assert counts_by_operator(mutants, "app/billing.py") == EXPECTED_BILLING_COUNTS
        assert counts_by_operator(mutants, "app/scoring.py") == EXPECTED_SCORING_COUNTS
        assert len(mutants) == sum(EXPECTED_BILLING_COUNTS.values()) + sum(
            EXPECTED_SCORING_COUNTS.values()
        )

    def test_expected_mutants_exist(self, mutants: list[Mutant]) -> None:
        assert find(
            mutants,
            operator_name="ArithmeticOperatorSwap",
            function_name="add_fee",
            original_snippet="amount + fee",
            mutated_snippet="amount - fee",
        )
        assert find(
            mutants,
            operator_name="ComparisonOperatorSwap",
            function_name="tier",
            original_snippet="amount >= 50",
            mutated_snippet="amount > 50",
        )
        assert find(
            mutants,
            operator_name="BooleanOperatorSwap",
            function_name="tier",
            mutated_snippet="amount >= 50 or amount < 100",
        )
        assert find(
            mutants,
            operator_name="ConditionNegation",
            function_name="tier",
            mutated_snippet="if not amount == 0:",
        )
        assert find(
            mutants,
            operator_name="ConstantMutation",
            function_name="",
            original_snippet="0.25",
            mutated_snippet="1.25",
        )
        boundary = find(
            mutants,
            operator_name="BoundaryValueMutation",
            function_name="tier",
            original_snippet="amount >= 50",
        )
        assert {m.mutated_snippet for m in boundary} == {"amount >= 51", "amount >= 49"}
        assert find(
            mutants,
            operator_name="ReturnValueMutation",
            function_name="add_fee",
            original_snippet="return amount + fee",
            mutated_snippet="return None",
        )

    def test_every_mutant_compiles_and_differs_from_original(
        self, mutants: list[Mutant]
    ) -> None:
        original_renders = {
            "app/billing.py": ast.unparse(ast.parse(BILLING_SOURCE)),
            "app/scoring.py": ast.unparse(ast.parse(SCORING_SOURCE)),
        }
        for m in mutants:
            compile(m.mutated_source, m.file_path, "exec")  # must not raise
            assert m.mutated_source != original_renders[m.file_path]
            assert m.mutant_id == ""  # IDs come from select_bank only
            assert m.description

    def test_docstrings_untouched(self, mutants: list[Mutant]) -> None:
        expected = {
            "app/billing.py": {
                "": "Fixture module docstring.",
                "add_fee": "Docstring for add_fee.",
                "tier": None,
                "Grader": "Class docstring.",
                "Grader.passing": None,
            },
            "app/scoring.py": {"": "Scoring helpers.", "clamp": "Clamp value into [low, high]."},
        }
        for m in mutants:
            tree = ast.parse(m.mutated_source)
            found: dict[str, str | None] = {"": ast.get_docstring(tree)}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    key = node.name if node.name != "passing" else "Grader.passing"
                    found[key] = ast.get_docstring(node)
            assert found == expected[m.file_path], m.location

    def test_skips_init_tests_and_excluded_files(self, mutants: list[Mutant]) -> None:
        files = {m.file_path for m in mutants}
        assert files == {"app/billing.py", "app/scoring.py"}

    def test_exclude_override(self, target: Path) -> None:
        with_templates = enumerate_mutants(target, exclude=())
        files = {m.file_path for m in with_templates}
        assert "app/prompt_templates.py" in files
        # THRESHOLD = 5 mutates; the PROMPT string constant never does.
        template_mutants = find(with_templates, file_path="app/prompt_templates.py")
        assert [m.original_snippet for m in template_mutants] == ["5"]

    def test_sorted_by_file_line_col_operator(self, mutants: list[Mutant]) -> None:
        keys = [(m.file_path, m.line_no, m.col_offset, m.operator_name) for m in mutants]
        assert keys == sorted(keys)

    def test_function_qualnames(self, mutants: list[Mutant]) -> None:
        assert {m.function_name for m in mutants} == {
            "",
            "add_fee",
            "tier",
            "Grader.passing",
            "clamp",
        }
        assert find(mutants, original_snippet="score > 70")[0].function_name == "Grader.passing"

    def test_missing_app_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            enumerate_mutants(tmp_path / "nowhere")


class TestDeterminism:
    def test_two_runs_byte_identical(self, target: Path) -> None:
        assert serialize(enumerate_mutants(target)) == serialize(enumerate_mutants(target))

    def test_identical_across_directories(self, tmp_path: Path) -> None:
        first = build_target(tmp_path / "copy_a")
        second = build_target(tmp_path / "copy_b")
        assert serialize(enumerate_mutants(first)) == serialize(enumerate_mutants(second))

    def test_select_bank_byte_identical(self, mutants: list[Mutant]) -> None:
        assert serialize(select_bank(mutants, size=12, seed=1337)) == serialize(
            select_bank(mutants, size=12, seed=1337)
        )

    def test_select_bank_seed_sensitive(self, mutants: list[Mutant]) -> None:
        # Verified stable: both calls are fully deterministic, so this
        # comparison has one fixed outcome on every machine.
        assert serialize(select_bank(mutants, size=12, seed=1337)) != serialize(
            select_bank(mutants, size=12, seed=42)
        )


class TestSelectBank:
    def test_ids_assigned_in_selection_order(self, mutants: list[Mutant]) -> None:
        bank = select_bank(mutants, size=12, seed=1337)
        assert [m.mutant_id for m in bank] == [f"M{i:03d}" for i in range(1, 13)]

    def test_first_round_covers_every_group_once(self, mutants: list[Mutant]) -> None:
        group_keys = sorted({(m.file_path, m.operator_name) for m in mutants})
        assert len(group_keys) == 10
        bank = select_bank(mutants, size=12, seed=1337)
        # Round one drains sorted (file, operator) keys in order, one each.
        assert [(m.file_path, m.operator_name) for m in bank[:10]] == group_keys
        assert {m.file_path for m in bank[:10]} == {"app/billing.py", "app/scoring.py"}

    def test_operator_names_match_classes(self, mutants: list[Mutant]) -> None:
        bank = select_bank(mutants, size=12, seed=1337)
        assert {m.operator_name for m in bank[:7]} <= {op.name for op in ALL_OPERATORS}
        assert len({op.name for op in ALL_OPERATORS}) == 7

    def test_oversized_request_returns_everything(self, mutants: list[Mutant]) -> None:
        bank = select_bank(mutants, size=1000, seed=1337)
        assert len(bank) == len(mutants)
        assert [m.mutant_id for m in bank] == [
            f"M{i:03d}" for i in range(1, len(mutants) + 1)
        ]
        # Ignoring the assigned IDs, the bank is a permutation of the input.
        assert sorted(serialize([replace(m, mutant_id="")]) for m in bank) == sorted(
            serialize([m]) for m in mutants
        )

    def test_inputs_not_modified(self, mutants: list[Mutant]) -> None:
        before = serialize(mutants)
        select_bank(mutants, size=12, seed=1337)
        assert serialize(mutants) == before

    def test_empty_and_zero_size(self, mutants: list[Mutant]) -> None:
        assert select_bank([], size=12, seed=1337) == []
        assert select_bank(mutants, size=0, seed=1337) == []

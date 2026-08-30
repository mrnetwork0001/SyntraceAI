"""Selftests: module-level healing via the module API, anchored sweeps."""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from advanced.ast_mutator import enumerate_mutants
from advanced.core_types import Mutant
from advanced.test_healer import (
    _anchor_values,
    _interleaved_product,
    heal_survivors,
    write_healed_test_file,
)

MODULE = '''"""Unit scaling with a module-level threshold table."""

from __future__ import annotations

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any

POWERS = (3, 6, 9, 12)
SUFFIXES = ("k", "M", "G", "T")


def scale(value: float, precision: int = 1) -> str:
    """Return value with the largest applicable suffix."""
    for power, suffix in zip(reversed(POWERS), reversed(SUFFIXES)):
        if abs(value) >= 10 ** power:
            return f"{value / 10 ** power:.{precision}f}{suffix}"
    return f"{value:.{precision}f}"
'''

TESTS = '''
from mylib.units import scale


def test_small_values_unchanged():
    assert scale(5.0) == "5.0"


def test_thousands():
    assert scale(2500.0) == "2.5k"
'''


def _build_target(tmp_path: Path) -> Path:
    target = tmp_path / "target"
    (target / "mylib").mkdir(parents=True)
    (target / "mylib" / "__init__.py").write_text("")
    (target / "mylib" / "units.py").write_text(MODULE)
    (target / "tests").mkdir()
    (target / "tests" / "__init__.py").write_text("")
    (target / "tests" / "test_units.py").write_text(TESTS)
    (target / "syntrace_target.json").write_text(
        '{"source_package": "mylib", "prompt_templates": null}'
    )
    return target


def _run_pytest(project: Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=project, capture_output=True, text=True,
    ).returncode


def test_module_level_constant_healed_through_module_api(tmp_path: Path) -> None:
    target = _build_target(tmp_path)
    mutants = enumerate_mutants(target)
    table_mutants = [
        m for m in mutants
        if m.function_name == "" and m.operator_name == "ConstantMutation"
        and m.original_snippet == "12"
    ]
    assert table_mutants, "expected a module-level mutation of the POWERS table"
    mutant: Mutant = replace(table_mutants[0], mutant_id="M999")
    assert mutant.mutated_snippet == "13"

    healed, unhealable = heal_survivors(target, [mutant])
    assert unhealable == []
    assert len(healed) == 1
    test = healed[0]
    assert test.function_name == "scale"      # observed through the module API
    assert "10**12" in test.input_repr or "1000000000000" in test.input_repr

    # The emitted test passes on the pristine target and kills the mutant.
    write_healed_test_file(target, healed)
    assert _run_pytest(target) == 0
    (target / "mylib" / "units.py").write_text(mutant.mutated_source)
    assert _run_pytest(target) == 1


def test_type_checking_flag_is_never_mutated(tmp_path: Path) -> None:
    target = _build_target(tmp_path)
    for m in enumerate_mutants(target):
        assert not (m.line_no == 5 and m.operator_name == "ConstantMutation"), (
            "TYPE_CHECKING = False must be protected from mutation"
        )


def test_anchored_sweeps_precede_product() -> None:
    fn = ast.parse("def f(a: int, b: str = 'x', c: float = -2.5): ...").body[0]
    assert isinstance(fn, ast.FunctionDef)
    pools = [[0, 1, 2], ["p", "q"], [0.0, 9.0]]
    anchors = _anchor_values(fn, pools)
    assert anchors == [0, "x", -2.5]
    candidates = _interleaved_product(pools, 100, 1337, anchors=anchors)
    assert candidates[0] == (0, "x", -2.5)
    sweep = candidates[1 : 1 + 2 + 2 + 2]
    assert (2, "x", -2.5) in sweep and (0, "q", -2.5) in sweep and (0, "x", 9.0) in sweep
    assert len(candidates) == len({tuple(map(repr, c)) for c in candidates})  # deduped

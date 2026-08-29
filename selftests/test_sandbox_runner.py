"""Selftests for advanced.sandbox_runner (ARCHITECTURE.md §8).

Builds a tiny throwaway pytest project in tmp and exercises every outcome
path: green suite, killed patch, runaway (timeout) patch, syntax-error patch,
parallel evaluation with progress callbacks, per-item crash isolation, and
guaranteed sandbox cleanup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from advanced import sandbox_runner
from advanced.core_types import Outcome

GREEN_CALC = """\
def add(a: int, b: int) -> int:
    return a + b


def sub(a: int, b: int) -> int:
    return a - b
"""

BROKEN_ADD = GREEN_CALC.replace("return a + b", "return a - b")
BROKEN_SUB = GREEN_CALC.replace("return a - b", "return a + b")
RUNAWAY = "while True:\n    pass\n" + GREEN_CALC
SYNTAX_ERROR = "def add(a, b:\n    return a + b\n"

TEST_FILE = """\
from app.calc import add, sub


def test_add_small():
    assert add(2, 3) == 5


def test_sub_small():
    assert sub(5, 2) == 3
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "app").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (proj / "app" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "app" / "calc.py").write_text(GREEN_CALC, encoding="utf-8")
    (proj / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "tests" / "test_calc.py").write_text(TEST_FILE, encoding="utf-8")
    return proj


def test_run_suite_green(project: Path) -> None:
    result = sandbox_runner.run_suite(project)
    assert result.exit_code == 0
    assert result.outcome is Outcome.SURVIVED
    assert not result.outcome.detected
    assert result.failed_tests == []
    assert result.duration_s > 0.0


def test_evaluate_patch_killed_with_failed_ids(project: Path) -> None:
    result = sandbox_runner.evaluate_patch(project, "app/calc.py", BROKEN_ADD)
    assert result.outcome is Outcome.KILLED
    assert result.exit_code == 1
    assert result.outcome.detected
    assert "tests/test_calc.py::test_add_small" in result.failed_tests
    assert 0 < len(result.stdout_tail) <= 2000


def test_evaluate_patch_does_not_modify_target(project: Path) -> None:
    sandbox_runner.evaluate_patch(project, "app/calc.py", BROKEN_ADD)
    assert (project / "app" / "calc.py").read_text(encoding="utf-8") == GREEN_CALC


def test_evaluate_patch_timeout(project: Path) -> None:
    result = sandbox_runner.evaluate_patch(project, "app/calc.py", RUNAWAY, timeout_s=3.0)
    assert result.outcome is Outcome.TIMEOUT
    assert result.outcome.detected
    assert result.exit_code == sandbox_runner.NO_EXIT_CODE
    assert result.duration_s >= 2.5  # ran up to the guard, not a fast failure


def test_evaluate_patch_syntax_error_is_detected(project: Path) -> None:
    result = sandbox_runner.evaluate_patch(project, "app/calc.py", SYNTAX_ERROR)
    assert result.outcome in (Outcome.ERROR, Outcome.KILLED)
    assert result.outcome.detected


def test_evaluate_patch_rejects_sandbox_escape(project: Path) -> None:
    with pytest.raises(ValueError):
        sandbox_runner.evaluate_patch(project, "../escape.py", "x = 1\n")


def test_evaluate_patch_cleans_up_tmpdirs(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []
    real_mkdtemp = sandbox_runner.tempfile.mkdtemp

    def spying_mkdtemp(*args: object, **kwargs: object) -> str:
        path = real_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]
        created.append(path)
        return path

    monkeypatch.setattr(sandbox_runner.tempfile, "mkdtemp", spying_mkdtemp)
    sandbox_runner.evaluate_patch(project, "app/calc.py", BROKEN_ADD)
    with pytest.raises(ValueError):
        sandbox_runner.evaluate_patch(project, "../escape.py", "x = 1\n")
    assert created, "evaluate_patch never created a sandbox"
    assert all(not Path(p).exists() for p in created), "sandbox dirs were left behind"


def test_evaluate_many_keyed_results_and_callbacks(project: Path) -> None:
    items = [
        ("A", "app/calc.py", GREEN_CALC),
        ("B", "app/calc.py", BROKEN_ADD),
        ("C", "app/calc.py", SYNTAX_ERROR),
        ("D", "app/calc.py", BROKEN_SUB),
    ]
    seen: list[str] = []

    def on_result(item_id: str, result: sandbox_runner.TestRunResult) -> None:
        seen.append(item_id)
        assert result.outcome is not Outcome.NOT_RUN

    results = sandbox_runner.evaluate_many(project, items, jobs=4, on_result=on_result)
    assert set(results) == {"A", "B", "C", "D"}
    assert len(seen) == 4
    assert sorted(seen) == ["A", "B", "C", "D"]
    assert results["A"].outcome is Outcome.SURVIVED
    assert results["B"].outcome is Outcome.KILLED
    assert "tests/test_calc.py::test_add_small" in results["B"].failed_tests
    assert results["C"].outcome.detected
    assert results["D"].outcome is Outcome.KILLED


def test_evaluate_many_isolates_a_crashing_item(project: Path) -> None:
    # rel_path "app" is a directory: writing the patch raises inside the worker.
    items = [
        ("ok", "app/calc.py", BROKEN_ADD),
        ("boom", "app", "not a file"),
    ]
    results = sandbox_runner.evaluate_many(project, items, jobs=2)
    assert set(results) == {"ok", "boom"}
    assert results["ok"].outcome is Outcome.KILLED
    assert results["boom"].outcome is Outcome.ERROR
    assert results["boom"].exit_code == sandbox_runner.NO_EXIT_CODE
    assert results["boom"].stdout_tail  # carries the exception text


def test_evaluate_many_empty_items(project: Path) -> None:
    assert sandbox_runner.evaluate_many(project, []) == {}


def test_missing_directories_raise() -> None:
    ghost = Path("/nonexistent/syntrace/ghost")
    with pytest.raises(FileNotFoundError):
        sandbox_runner.run_suite(ghost)
    with pytest.raises(FileNotFoundError):
        sandbox_runner.evaluate_patch(ghost, "app/calc.py", "x = 1\n")

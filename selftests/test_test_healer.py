"""Selftests for advanced/test_healer.py (ARCHITECTURE.md §9).

Builds a tiny fixture target in tmp, hand-crafts real AST-mutated mutants,
runs the differential healer against them, and proves the honesty claims:

- healed tests carry a *genuinely* discriminating input (both function
  versions are executed here to demonstrate the disagreement);
- the emitted file passes against the original target and fails against each
  healed mutant (real pytest subprocess runs in tmp copies);
- an equivalent mutant and unsupported signatures land in ``unhealable``;
- ``write_healed_test_file`` output is a single clean pytest file.
"""

from __future__ import annotations

import ast
import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from advanced.core_types import Mutant, Perturbation
from advanced.test_healer import (
    _interleaved_product,
    build_prompt_contract_tests,
    heal_survivors,
    write_healed_test_file,
)

MATHY_SOURCE = '''"""Fixture module with boundary-rich helpers for healer selftests."""

GREETING = "hello"


def bulk_discount(amount: float) -> float:
    """Apply a 10% discount for amounts of at least 100."""
    if amount >= 100.0:
        return amount * 0.9
    return amount


def add_offset(x: int) -> int:
    """Identity with a redundant offset (equivalent-mutant bait)."""
    return x + 0


def strict_pos(x: int) -> int:
    """Return x, rejecting non-positive values."""
    if x <= 0:
        raise ValueError("x must be positive")
    return x


def describe(items: list) -> str:
    """Non-scalar parameter - unsupported by the healer by design."""
    return str(len(items))
'''

# Blind-but-green suite: passes on the original AND on every mutant below,
# which is exactly what makes those mutants "survivors".
TRIVIAL_TESTS_SOURCE = '''"""Deliberately blind smoke tests (mutation survivors by design)."""

import pytest

from app.mathy import add_offset, bulk_discount


def test_discount_away_from_threshold() -> None:
    assert bulk_discount(10.0) == 10.0
    assert bulk_discount(200.0) == pytest.approx(180.0)


def test_add_offset_smoke() -> None:
    assert add_offset(3) == 3
'''

# Minimal deterministic stand-in for the sample app's triage pipeline,
# implementing the frozen §5.2 keyword rules and the §5.3 merged contract, so
# the emitted prompt-contract tests can be executed for real in this selftest.
PIPELINE_STUB_SOURCE = '''"""Deterministic triage stub honoring the frozen contract shape."""


def triage_ticket(ticket_text: str, *, strict: bool = False) -> dict:
    """Rule-based stand-in for the real pipeline (categories per section 5.2)."""
    text = ticket_text.lower()
    if any(k in text for k in ("refund", "charge", "billing")):
        category, priority = "billing", 3
    elif any(k in text for k in ("crash", "error", "bug", "broken")):
        category, priority = "bug", 4
    elif any(k in text for k in ("password", "login", "2fa")):
        category, priority = "account", 3
    elif any(k in text for k in ("slow", "latency", "timeout")):
        category, priority = "performance", 2
    else:
        category, priority = "general", 1
    confidence = min(0.95, round(0.6 + 0.05 * len(category), 2))
    return {
        "category": category,
        "priority": priority,
        "confidence": confidence,
        "summary": " ".join(ticket_text.split()[:8]) or "(no ticket text)",
        "priority_score": round(priority * confidence, 2),
        "escalate": priority >= 4,
    }
'''


def _build_target(root: Path) -> Path:
    target = root / "mini_target"
    (target / "app").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (target / "app" / "__init__.py").write_text("", encoding="utf-8")
    (target / "app" / "mathy.py").write_text(MATHY_SOURCE, encoding="utf-8")
    (target / "app" / "llm_pipeline.py").write_text(PIPELINE_STUB_SOURCE, encoding="utf-8")
    (target / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (target / "tests" / "test_mathy.py").write_text(TRIVIAL_TESTS_SOURCE, encoding="utf-8")
    return target


def _mutant_from_tree(
    tree: ast.Module,
    node: ast.AST,
    original_snippet: str,
    mutated_snippet: str,
    *,
    mutant_id: str,
    function_name: str,
    operator_name: str,
) -> Mutant:
    mutated_source = ast.unparse(ast.fix_missing_locations(tree))
    compile(mutated_source, "app/mathy.py", "exec")  # contract: mutants compile
    return Mutant(
        mutant_id=mutant_id,
        file_path="app/mathy.py",
        operator_name=operator_name,
        line_no=getattr(node, "lineno", 1),
        col_offset=getattr(node, "col_offset", 0),
        function_name=function_name,
        original_snippet=original_snippet,
        mutated_snippet=mutated_snippet,
        mutated_source=mutated_source,
        description=f"{original_snippet} -> {mutated_snippet}",
    )


def _compare_mutant(
    mutant_id: str, func: str, from_op: type[ast.cmpop], to_op: type[ast.cmpop]
) -> Mutant:
    """Real unparsed-source mutant: flip the first ``from_op`` compare in ``func``."""
    tree = ast.parse(MATHY_SOURCE)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func)
    compare = next(
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Compare) and isinstance(n.ops[0], from_op)
    )
    original_snippet = ast.unparse(compare)
    compare.ops[0] = to_op()
    return _mutant_from_tree(
        tree,
        compare,
        original_snippet,
        ast.unparse(compare),
        mutant_id=mutant_id,
        function_name=func,
        operator_name="ComparisonOperatorSwap",
    )


def _equivalent_mutant(mutant_id: str) -> Mutant:
    """``x + 0`` → ``x - 0`` in add_offset: compiles, differs, never observable."""
    tree = ast.parse(MATHY_SOURCE)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "add_offset")
    binop = next(
        n for n in ast.walk(fn) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add)
    )
    original_snippet = ast.unparse(binop)
    binop.op = ast.Sub()
    return _mutant_from_tree(
        tree,
        binop,
        original_snippet,
        ast.unparse(binop),
        mutant_id=mutant_id,
        function_name="add_offset",
        operator_name="ArithmeticOperatorSwap",
    )


def _unsupported_signature_mutant(mutant_id: str) -> Mutant:
    """Mutant targeting ``describe`` (a ``list`` parameter - unsupported)."""
    tree = ast.parse(MATHY_SOURCE)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "describe")
    return _mutant_from_tree(
        tree,
        fn,
        "len(items)",
        "len(items)",
        mutant_id=mutant_id,
        function_name="describe",
        operator_name="ReturnValueMutation",
    )


def _exec_function(source: str, name: str):
    namespace: dict = {}
    exec(compile(source, "<fixture>", "exec"), namespace)
    return namespace[name]


def _run_pytest(project_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rf", "--no-header", "-p", "no:cacheprovider"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


@pytest.fixture(scope="module")
def healing_run(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    """One real healing campaign over six hand-crafted mutants, shared read-only."""
    target = _build_target(tmp_path_factory.mktemp("healer_fixture"))
    m_boundary = _compare_mutant("M001", "bulk_discount", ast.GtE, ast.Gt)
    m_equivalent = _equivalent_mutant("M002")
    m_raises = _compare_mutant("M003", "strict_pos", ast.LtE, ast.Lt)
    m_list_param = _unsupported_signature_mutant("M004")
    m_no_function = dataclasses.replace(m_boundary, mutant_id="M005", function_name="")
    m_ghost = dataclasses.replace(m_boundary, mutant_id="M006", function_name="ghost_fn")
    survivors = [m_boundary, m_equivalent, m_raises, m_list_param, m_no_function, m_ghost]
    healed, unhealable = heal_survivors(target, survivors, seed=1337, max_inputs=500)
    return SimpleNamespace(
        target=target,
        mutants={m.mutant_id: m for m in survivors},
        healed=healed,
        unhealable=unhealable,
    )


def test_healed_and_unhealable_partition(healing_run: SimpleNamespace) -> None:
    # M005 carries no enclosing function: it is healed by sweeping the module's
    # top-level API and observing the mutation through bulk_discount.
    assert [t.mutant_id for t in healing_run.healed] == ["M001", "M003", "M005"]
    assert healing_run.unhealable == ["M002", "M004", "M006"]


def test_module_level_mutant_healed_via_module_api(healing_run: SimpleNamespace) -> None:
    healed = {t.mutant_id: t for t in healing_run.healed}["M005"]
    assert healed.function_name == "bulk_discount"
    assert healed.module == "app.mathy"


def test_value_heal_is_genuinely_discriminating(healing_run: SimpleNamespace) -> None:
    healed = healing_run.healed[0]
    assert healed.test_name == "test_healed_M001_bulk_discount"
    assert healed.module == "app.mathy"

    args = ast.literal_eval(healed.input_repr)
    assert isinstance(args, tuple)
    original = _exec_function(MATHY_SOURCE, "bulk_discount")
    mutated = _exec_function(healing_run.mutants["M001"].mutated_source, "bulk_discount")
    original_result = original(*args)
    mutated_result = mutated(*args)
    assert original_result != mutated_result, "input must genuinely discriminate"
    assert repr(original_result) == healed.expected_repr

    assert "pytest.approx" in healed.test_source
    assert "# Mutant M001 | ComparisonOperatorSwap | app/mathy.py:" in healed.test_source
    # Deterministic search order finds the flipped >= boundary itself first.
    assert args == (100.0,)


def test_raises_heal_is_genuinely_discriminating(healing_run: SimpleNamespace) -> None:
    healed = healing_run.healed[1]
    assert healed.test_name == "test_healed_M003_strict_pos"
    assert healed.expected_repr == "raises ValueError"
    assert "with pytest.raises(ValueError):" in healed.test_source

    args = ast.literal_eval(healed.input_repr)
    original = _exec_function(MATHY_SOURCE, "strict_pos")
    mutated = _exec_function(healing_run.mutants["M003"].mutated_source, "strict_pos")
    with pytest.raises(ValueError):
        original(*args)
    assert mutated(*args) == args[0], "mutant must return where the original raises"


def test_healing_is_deterministic(healing_run: SimpleNamespace) -> None:
    rerun_healed, rerun_unhealable = heal_survivors(
        healing_run.target, [healing_run.mutants["M001"]], seed=1337, max_inputs=500
    )
    assert rerun_unhealable == []
    assert rerun_healed == [healing_run.healed[0]]


def test_prompt_contract_tests_unit(healing_run: SimpleNamespace, tmp_path: Path) -> None:
    survivor = Perturbation(
        perturbation_id="P001",
        template_name="SYSTEM_PROMPT",
        operator_name="RoleStripping",
        description="remove the TriageBot role line",
        perturbed_template="",
        mutated_source="SYSTEM_PROMPT = ''\n",
    )
    assert build_prompt_contract_tests(healing_run.target, []) == []

    tests = build_prompt_contract_tests(healing_run.target, [survivor])
    assert len(tests) == 5
    expected_categories = ["billing", "bug", "account", "performance", "general"]
    for n, (test, category) in enumerate(zip(tests, expected_categories), start=1):
        assert test.test_name == f"test_healed_prompt_contract_{n}"
        assert test.module == "app.llm_pipeline"
        assert test.function_name == "triage_ticket"
        assert "app.llm_pipeline.triage_ticket(" in test.test_source
        assert "strict=True" in test.test_source
        assert f"assert result['category'] == {category!r}" in test.test_source
        assert "'priority_score', 'escalate'" in test.test_source
        assert "P001" in test.test_source  # traceability to the survivors

    with pytest.raises(FileNotFoundError):
        build_prompt_contract_tests(tmp_path / "no_pipeline_here", [survivor])


def test_end_to_end_written_file_kills_mutants(
    healing_run: SimpleNamespace, tmp_path: Path
) -> None:
    survivor = Perturbation(
        perturbation_id="P002",
        template_name="SYSTEM_PROMPT",
        operator_name="JsonOnlyDirectiveRemoval",
        description="remove the JSON-only directive",
        perturbed_template="",
        mutated_source="SYSTEM_PROMPT = ''\n",
    )
    all_tests = healing_run.healed + build_prompt_contract_tests(
        healing_run.target, [survivor]
    )
    path = write_healed_test_file(healing_run.target, all_tests)
    assert path == healing_run.target / "tests" / "test_healed_assertions.py"

    content = path.read_text(encoding="utf-8")
    assert content.startswith('"""Hardened assertion tests auto-generated by SyntraceAI')
    assert "import pytest" in content
    assert "import app.llm_pipeline" in content
    assert "import app.mathy" in content
    compile(content, str(path), "exec")  # single valid standalone file

    # Healed suite is collected cleanly and passes against the ORIGINAL target.
    green = _run_pytest(healing_run.target)
    assert green.returncode == 0, green.stdout + green.stderr
    assert "10 passed" in green.stdout  # 2 trivial + 3 healed + 5 contract tests

    # Against each healed mutant, its own healed test fails and nothing but
    # healed tests fail. (M005 is the same bug as M001 observed module-level,
    # so its test legitimately fails against the M001 mutant too.)
    for mutant_id, function_name in (("M001", "bulk_discount"), ("M003", "strict_pos")):
        mutant_copy = tmp_path / f"copy_{mutant_id}"
        shutil.copytree(
            healing_run.target,
            mutant_copy,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        (mutant_copy / "app" / "mathy.py").write_text(
            healing_run.mutants[mutant_id].mutated_source, encoding="utf-8"
        )
        kill = _run_pytest(mutant_copy)
        assert kill.returncode == 1, kill.stdout + kill.stderr
        failed_lines = [
            line for line in kill.stdout.splitlines() if line.startswith("FAILED")
        ]
        assert failed_lines, kill.stdout
        assert all("test_healed_assertions.py::test_healed_" in line for line in failed_lines)
        assert any(f"test_healed_{mutant_id}_{function_name}" in line for line in failed_lines)


def test_write_healed_file_overwrites_and_handles_empty(
    healing_run: SimpleNamespace, tmp_path: Path
) -> None:
    scratch_target = tmp_path / "scratch_target"
    scratch_target.mkdir()

    first = write_healed_test_file(scratch_target, [healing_run.healed[0]])
    first_content = first.read_text(encoding="utf-8")
    assert "test_healed_M001_bulk_discount" in first_content
    assert "test_healed_M003_strict_pos" not in first_content

    second = write_healed_test_file(scratch_target, healing_run.healed)
    assert second == first
    second_content = second.read_text(encoding="utf-8")
    assert second_content != first_content
    assert second_content.count("Hardened assertion tests auto-generated") == 1
    assert "test_healed_M003_strict_pos" in second_content

    empty = write_healed_test_file(scratch_target, [])
    empty_content = empty.read_text(encoding="utf-8")
    assert "auto-generated" in empty_content
    compile(empty_content, str(empty), "exec")


def test_interleaved_product_spreads_the_cap() -> None:
    pools: list[list[int]] = [list(range(60)), list(range(60))]
    picked = _interleaved_product(pools, 50, seed=1337)
    assert len(picked) == 50
    assert len(set(picked)) == 50  # no duplicates
    first_coords = {a for a, _ in picked}
    second_coords = {b for _, b in picked}
    # A naive product prefix would pin the first coordinate to 0; the
    # interleaved order varies BOTH parameters within the cap.
    assert len(first_coords) >= 5
    assert len(second_coords) >= 5
    # Deterministic for a given seed.
    assert picked == _interleaved_product(pools, 50, seed=1337)
    # Degenerate shapes stay well-defined.
    assert _interleaved_product([], 10, seed=1337) == [()]
    assert _interleaved_product([[1, 2], []], 10, seed=1337) == []
    assert _interleaved_product([[1, 2]], 0, seed=1337) == []

"""Differential auto-healer for surviving mutants (ARCHITECTURE.md §9).

For every surviving code mutant that targets a top-level function with scalar
type hints, the healer searches for a *discriminating input* — an input on
which the pristine target and the mutated target observably disagree — by
running a generated probe script in subprocesses against two temporary copies
of the target (no import-cache games). A pytest test asserting the ORIGINAL
behavior on that input is then synthesized; on re-run it kills the mutant.

Honesty invariants enforced here:

- A test is emitted only for a discriminating input that was *re-verified* by
  a second probe round on that single input; if verification fails, the mutant
  is reported unhealable instead.
- Mutants with no discriminating input (likely equivalent) and mutants with
  unsupported signatures (non-scalar params, ``*args``/``**kwargs``, missing
  hints, nested/missing functions) are returned in ``unhealable_mutant_ids``,
  never papered over with a fabricated test.

Everything is deterministic for a given ``seed``: candidate pools, the
interleaved enumeration order, and therefore the chosen input and the emitted
test source.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ast
import builtins
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import PurePosixPath
from typing import Any, Iterator

from advanced.core_types import DEFAULT_SEED, HealedTest, Mutant, Perturbation

__all__ = ["heal_survivors", "build_prompt_contract_tests", "write_healed_test_file"]

# --------------------------------------------------------------------------- #
# Tunables (frozen by ARCHITECTURE.md §9)                                     #
# --------------------------------------------------------------------------- #

#: Base candidate pool for ``int`` parameters (§9); floats use the same values.
#: The large magnitudes exist to push computed intermediates (rates, taxes,
#: totals) across caps and clamps that small boundary values never reach.
INT_POOL: tuple[int, ...] = (
    -3, -1, 0, 1, 2, 3, 7, 10, 49, 50, 51, 99, 100, 101, 499, 500, 501,
    1000, 2500, 5000, 10000,
)
BOOL_POOL: tuple[bool, ...] = (True, False)
#: Extra values for ``float`` parameter pools only. Non-integral values expose
#: rounding-precision and rate mutants that integral products hide; the orders
#: of magnitude (decimal and binary) reach the unit-scaling thresholds that
#: number-formatting code branches on. They are deliberately kept out of
#: ``int`` pools: an int parameter is as likely to be a precision, a repeat
#: count, or a size as a quantity, and ``f"{x:.{10**33}f}"`` never returns.
FLOAT_EXTRAS: tuple[float, ...] = (
    0.07, 0.1, 0.5, 2.5, 19.99,
    1e6, 1e9, 1e12, 1e15, 1e18, 1e21, 1e24, 1e27, 1e30, 1e33,
    2.0**10, 2.0**20, 2.0**30, 2.0**40,
)
#: A ten-word sentence so word-boundary logic (truncation, summarization)
#: has a long-enough input to discriminate off-by-one word counts.
LONG_SENTENCE: str = "the quick brown fox jumps over the lazy dog today"
#: Adversarial JSON contract payloads: one in-contract, one with an
#: out-of-range confidence, one with a wrong type. Shaped after the demo
#: target's triage schema — useful for any target parsing LLM-style JSON
#: completions with a similar contract, but not schema-agnostic.
CONTRACT_PAYLOADS: tuple[str, ...] = (
    '{"category": "bug", "priority": 3, "confidence": 0.5, "summary": "checkout crash"}',
    '{"category": "bug", "priority": 3, "confidence": 1.5, "summary": "checkout crash"}',
    '{"category": "bug", "priority": "high", "confidence": 0.5, "summary": "checkout crash"}',
)
#: Always appended to the harvested string-constant pool (§9).
FALLBACK_STRINGS: tuple[str, ...] = ("", "zzz", LONG_SENTENCE, *CONTRACT_PAYLOADS)
#: Per-probe subprocess timeout (§9: "cap runtime, ~30s").
PROBE_TIMEOUT_S: float = 30.0
#: pytest.approx default tolerances — a float difference below these would NOT
#: be caught by the emitted ``pytest.approx`` assertion, so it must not count
#: as discriminating.
APPROX_REL_TOL: float = 1e-6
APPROX_ABS_TOL: float = 1e-12

_SCALAR_HINTS: frozenset[str] = frozenset({"int", "float", "bool", "str"})
_PROBE_FILENAME = "_syntrace_probe.py"
_SYNTH_FILENAME = "_syntrace_synth.py"
_INPUTS_FILENAME = "_syntrace_inputs.json"
_HEALED_TEST_BASENAME = "test_healed_assertions.py"

#: Cross-function synthesis caps: how many sibling generator functions to use,
#: how many base strings to feed each one, and bounds on the produced strings.
_SYNTH_MAX_FUNCTIONS = 6
_SYNTH_MAX_BASES = 8
_SYNTH_MAX_RESULTS = 48
_SYNTH_MAX_LEN = 4000

#: Pairwise-concatenation caps for str pools: composed inputs like
#: ``"### TICKET ###" + "zzz"`` reach suffix/terminator parsing paths that no
#: single harvested constant exercises.
_CONCAT_MAX_BASES = 8
_CONCAT_MAX_RESULTS = 64  # all pairs of the capped base set — every base leads once
_CONCAT_MAX_LEN = 200

#: Fixed prompt-contract battery (§9): expected category → ticket text. Each
#: ticket triggers exactly one keyword family of the frozen mock_llm rules
#: (§5.2): billing / bug / account / performance / general.
_CONTRACT_TICKETS: tuple[tuple[str, str], ...] = (
    ("billing", "Please refund the duplicate charge on my billing statement."),
    ("bug", "The app crashed with an error and the export button is broken."),
    ("account", "I cannot login because my password and 2fa reset failed."),
    ("performance", "Dashboard pages are slow and every request hits a timeout."),
    ("general", "How do I download my monthly usage summary as a spreadsheet?"),
)
#: Exact strict-mode result key set (§5.3 keys + the §5.2 merge keys).
_CONTRACT_KEYS: tuple[str, ...] = (
    "category", "priority", "confidence", "summary", "priority_score", "escalate",
)

#: Probe script written into each temporary project copy. ``__MODULE__`` and
#: ``__FUNC__`` are substituted per mutant. It guards every call and prints a
#: single JSON line of results (exceptions encoded ``{"__exc__": "TypeName"}``).
_PROBE_TEMPLATE = '''"""SyntraceAI differential probe (auto-generated) — do not edit."""
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_module = importlib.import_module("__MODULE__")
_target = getattr(_module, "__FUNC__")

_SCALARS = (bool, int, float, str)


def _encode(value):
    if value is None or isinstance(value, _SCALARS):
        return {"type": type(value).__name__, "value": value}
    return {"unsupported": type(value).__name__}


def main():
    candidates = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    results = []
    for args in candidates:
        try:
            results.append(_encode(_target(*args)))
        except BaseException as exc:
            results.append(
                {
                    "__exc__": type(exc).__name__,
                    "__exc_module__": type(exc).__module__,
                    # A same-module exception is only referenceable from the
                    # emitted test as <module>.<Name> if it is a module
                    # attribute (a class nested inside a class is not).
                    "__exc_is_module_attr__":
                        getattr(_module, type(exc).__name__, None) is type(exc),
                }
            )
    print(json.dumps(results))


if __name__ == "__main__":
    main()
'''


#: Synthesis script run once per module against the PRISTINE copy: applies the
#: module's own str -> str functions to base strings, producing realistic
#: composite inputs (e.g. a fully rendered prompt) that raw literal pools
#: cannot reach. Results feed BOTH probe sides identically, so discrimination
#: evidence stays symmetric.
_SYNTH_TEMPLATE = '''"""SyntraceAI cross-function input synthesis (auto-generated) — do not edit."""
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    module = importlib.import_module(spec["module"])
    out = []
    for name in spec["functions"]:
        fn = getattr(module, name, None)
        if not callable(fn):
            continue
        for base in spec["bases"]:
            try:
                result = fn(base)
            except BaseException:
                continue
            if isinstance(result, str) and 0 < len(result) <= spec["max_len"]:
                out.append(result)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
'''


class _Unhealable(Exception):
    """Internal control flow: this mutant cannot be healed (reason in args)."""


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def heal_survivors(
    target_dir: Path,
    survivors: list[Mutant],
    *,
    seed: int = DEFAULT_SEED,
    max_inputs: int = 2000,
) -> tuple[list[HealedTest], list[str]]:
    """Differentially heal surviving code mutants (ARCHITECTURE.md §9).

    For each survivor: build deterministic candidate inputs from the target
    function's type hints and harvested literals, probe a pristine and a
    mutated copy of the target in subprocesses, pick the first verified
    discriminating input, and synthesize a pytest test pinning the ORIGINAL
    behavior on it.

    Returns ``(healed_tests, unhealable_mutant_ids)`` — both in the order the
    survivors were given. A mutant is unhealable when its signature is
    unsupported, its probe fails or times out, or no discriminating input is
    found (likely-equivalent mutant). No test is ever emitted without a
    re-verified discriminating input.
    """
    target_dir = Path(target_dir).resolve()
    healed: list[HealedTest] = []
    unhealable: list[str] = []
    if not survivors:
        return healed, unhealable
    with tempfile.TemporaryDirectory(prefix="syntrace_heal_") as scratch_str:
        scratch = Path(scratch_str)
        pristine = scratch / "pristine"
        _copy_project(target_dir, pristine)
        synth_cache: dict[str, list[str]] = {}
        for mutant in survivors:
            try:
                healed.append(
                    _heal_one(
                        target_dir, pristine, scratch, mutant,
                        seed=seed, max_inputs=max_inputs, synth_cache=synth_cache,
                    )
                )
            except _Unhealable:
                unhealable.append(mutant.mutant_id)
    return healed, unhealable


def build_prompt_contract_tests(
    target_dir: Path, surviving: list[Perturbation]
) -> list[HealedTest]:
    """Emit strict-contract tests that kill surviving prompt perturbations.

    The five tests are independent of *which* perturbation survived: each one
    feeds a fixed ticket (covering the billing / bug / account / performance /
    general keyword families) through ``app.llm_pipeline.triage_ticket(t,
    strict=True)`` and asserts the exact §5.3 contract — key set (including
    the merged ``priority_score`` / ``escalate`` keys), types, and ranges.
    Any perturbation that degrades the LLM output breaks strict parsing and
    fails these tests loudly.

    Returns an empty list when ``surviving`` is empty (nothing to heal).
    Raises ``FileNotFoundError`` if the target has no ``app/llm_pipeline.py``.
    """
    if not surviving:
        return []
    target_dir = Path(target_dir)
    pipeline = target_dir / "app" / "llm_pipeline.py"
    if not pipeline.is_file():
        raise FileNotFoundError(
            f"target has no app/llm_pipeline.py to harden: {target_dir}"
        )
    survivor_ids = ", ".join(p.perturbation_id for p in surviving)
    key_set_literal = "{" + ", ".join(repr(k) for k in _CONTRACT_KEYS) + "}"
    healed: list[HealedTest] = []
    for n, (category, ticket) in enumerate(_CONTRACT_TICKETS, start=1):
        test_name = f"test_healed_prompt_contract_{n}"
        lines = [
            f"# Prompt-contract hardening test {n}/{len(_CONTRACT_TICKETS)} ({category} ticket).",
            f"# Targets surviving prompt perturbations: {survivor_ids}.",
            "# Asserts the exact strict-mode triage contract (ARCHITECTURE.md section 5.3).",
            f"def {test_name}() -> None:",
            f"    result = app.llm_pipeline.triage_ticket({ticket!r}, strict=True)",
            f"    assert set(result) == {key_set_literal}",
            f"    assert result['category'] == {category!r}",
            "    assert isinstance(result['priority'], int)",
            "    assert not isinstance(result['priority'], bool)",
            "    assert 1 <= result['priority'] <= 5",
            "    assert isinstance(result['confidence'], float)",
            "    assert 0.0 <= result['confidence'] <= 1.0",
            "    assert isinstance(result['summary'], str)",
            "    assert result['summary'] != ''",
            "    assert isinstance(result['priority_score'], (int, float))",
            "    assert not isinstance(result['priority_score'], bool)",
            "    assert isinstance(result['escalate'], bool)",
        ]
        healed.append(
            HealedTest(
                mutant_id=f"PC{n:02d}",
                function_name="triage_ticket",
                module="app.llm_pipeline",
                input_repr=f"({ticket!r}, strict=True)",
                expected_repr="strict triage contract: exact key set, types, ranges",
                test_name=test_name,
                test_source="\n".join(lines) + "\n",
            )
        )
    return healed


def write_healed_test_file(
    target_dir: Path, healed: list[HealedTest], *, tests_dir: str = "tests"
) -> Path:
    """Write all healed tests to ``<target>/<tests_dir>/test_healed_assertions.py``.

    Produces a single standalone pytest file: generated-by banner docstring,
    all imports at the top (``pytest`` only when used, plus every referenced
    target module), then each test's source. Overwrites any previous version.
    Duplicate test names are dropped deterministically (first one wins) so the
    emitted file always collects cleanly. Returns the written path.
    """
    target_dir = Path(target_dir)
    tests_path = target_dir / tests_dir
    tests_path.mkdir(parents=True, exist_ok=True)
    path = tests_path / _HEALED_TEST_BASENAME

    unique: list[HealedTest] = []
    seen_names: set[str] = set()
    for test in healed:
        if test.test_name not in seen_names:
            seen_names.add(test.test_name)
            unique.append(test)

    modules = sorted({t.module for t in unique if t.module})
    needs_pytest = any("pytest." in t.test_source for t in unique)
    lines: list[str] = [
        '"""Hardened assertion tests auto-generated by SyntraceAI test_healer.',
        "",
        "Each test pins the ORIGINAL target behavior on an input verified (by",
        "differential subprocess probing) to discriminate a surviving mutant, or",
        "asserts the strict triage contract that kills surviving prompt",
        "perturbations. Regenerated (overwritten) on every healing run - do not edit.",
        '"""',
    ]
    if needs_pytest:
        lines += ["", "import pytest"]
    if modules:
        lines.append("")
        lines += [f"import {m}" for m in modules]
    for test in unique:
        lines += ["", "", test.test_source.rstrip("\n")]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Healing pipeline internals                                                  #
# --------------------------------------------------------------------------- #


def _heal_one(
    target_dir: Path,
    pristine: Path,
    scratch: Path,
    mutant: Mutant,
    *,
    seed: int,
    max_inputs: int,
    synth_cache: dict[str, list[str]] | None = None,
) -> HealedTest:
    """Heal a single mutant or raise ``_Unhealable`` with the reason.

    A mutant inside a top-level function is probed through that function. A
    module-level mutant (a constant table, a flag) has no enclosing function,
    so it is probed through every top-level function of its module in
    definition order — a constant is observable through whichever API reads
    it — and the first verified discriminator wins.
    """
    module_name = _module_name_for(mutant.file_path)
    source_path = target_dir / mutant.file_path
    if not source_path.is_file():
        raise _Unhealable(f"source file missing: {mutant.file_path}")
    try:
        orig_tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - target must parse to survive
        raise _Unhealable(f"original source does not parse: {exc}") from exc

    if mutant.function_name:
        func_name = mutant.function_name
        if not func_name.isidentifier():
            raise _Unhealable("mutant does not target a named top-level function")
        fn_orig = _find_top_level_function(orig_tree, func_name)
        if fn_orig is None:
            raise _Unhealable(f"{func_name!r} is not a top-level function of {module_name}")
        observers = [fn_orig]
    else:
        observers = [node for node in orig_tree.body if isinstance(node, ast.FunctionDef)]
        if not observers:
            raise _Unhealable("module-level mutant with no top-level function to observe it")

    if synth_cache is None:
        synth_cache = {}
    mutated_dir = scratch / f"mutated_{mutant.mutant_id}"
    reasons: list[str] = []
    try:
        _copy_project(target_dir, mutated_dir)
        (mutated_dir / mutant.file_path).write_text(
            mutant.mutated_source, encoding="utf-8"
        )
        for fn_orig in observers:
            try:
                return _probe_function(
                    pristine, mutated_dir, mutant, module_name, fn_orig, orig_tree,
                    seed=seed, max_inputs=max_inputs, synth_cache=synth_cache,
                )
            except _Unhealable as exc:
                reasons.append(f"{fn_orig.name}: {exc}")
    finally:
        shutil.rmtree(mutated_dir, ignore_errors=True)
    raise _Unhealable("; ".join(reasons))


def _probe_function(
    pristine: Path,
    mutated_dir: Path,
    mutant: Mutant,
    module_name: str,
    fn_orig: ast.FunctionDef,
    orig_tree: ast.Module,
    *,
    seed: int,
    max_inputs: int,
    synth_cache: dict[str, list[str]],
) -> HealedTest:
    """Differentially probe one function; return a healed test or raise."""
    func_name = fn_orig.name
    synth_key = f"{module_name}::{func_name}"  # exclusion differs per probed function
    if synth_key not in synth_cache:
        synth_cache[synth_key] = _synthesized_strings(
            pristine, module_name, orig_tree, exclude_function=func_name
        )
    pools = _candidate_pools(
        fn_orig, mutant, orig_tree, extra_strings=synth_cache[synth_key]
    )
    anchors = _anchor_values(fn_orig, pools)
    candidates = _interleaved_product(pools, max_inputs, seed, anchors=anchors)
    if not candidates:
        raise _Unhealable("no candidate inputs could be generated")

    probe_source = _PROBE_TEMPLATE.replace("__MODULE__", module_name).replace(
        "__FUNC__", func_name
    )
    for project in (pristine, mutated_dir):
        (project / _PROBE_FILENAME).write_text(probe_source, encoding="utf-8")

    orig_results = _run_probe(pristine, candidates)
    mut_results = _run_probe(mutated_dir, candidates)
    if orig_results is None or mut_results is None:
        raise _Unhealable("probe subprocess failed or timed out")

    mode, index = _first_discriminating(orig_results, mut_results, module_name)
    if mode is None:
        raise _Unhealable("no discriminating input found (likely equivalent mutant)")

    # Honesty gate: re-verify the single chosen input in both copies.
    args = candidates[index]
    v_orig = _run_probe(pristine, [args])
    v_mut = _run_probe(mutated_dir, [args])
    if v_orig is None or v_mut is None:
        raise _Unhealable("verification probe failed or timed out")
    if v_orig[0] != orig_results[index]:
        raise _Unhealable("original behavior unstable across runs")
    if mode == "value":
        verified = _is_value(v_orig[0]) and _value_assertion_kills(v_orig[0], v_mut[0])
    else:
        verified = _raises_assertion_kills(v_orig[0], v_mut[0], module_name)
    if not verified:
        raise _Unhealable("discriminating input failed re-verification")
    return _build_healed_test(mutant, module_name, func_name, args, mode, v_orig[0])


def _anchor_values(fn_orig: ast.FunctionDef, pools: list[list[Any]]) -> list[Any]:
    """One realistic anchor value per parameter for single-parameter sweeps.

    A parameter's own default (when it is a scalar or ``None`` literal) is
    the most realistic anchor — ``metric(value, unit="", precision=3)`` is
    how callers actually invoke it; otherwise the first pool value is used.
    """
    arguments = fn_orig.args
    params = list(arguments.posonlyargs) + list(arguments.args)
    defaults: list[ast.expr | None] = [None] * (len(params) - len(arguments.defaults))
    defaults += list(arguments.defaults)
    anchors: list[Any] = []
    for pool, default in zip(pools, defaults):
        value: Any = pool[0]
        if isinstance(default, ast.Constant) and (
            default.value is None or type(default.value) in (bool, int, float, str)
        ):
            value = default.value
        elif (
            isinstance(default, ast.UnaryOp)
            and isinstance(default.op, ast.USub)
            and isinstance(default.operand, ast.Constant)
            and type(default.operand.value) in (int, float)
        ):
            value = -default.operand.value
        anchors.append(value)
    return anchors


def _build_healed_test(
    mutant: Mutant,
    module_name: str,
    func_name: str,
    args: tuple[Any, ...],
    mode: str,
    orig_encoded: dict[str, Any],
) -> HealedTest:
    """Synthesize the pytest test source pinning the original behavior."""
    test_name = f"test_healed_{_sanitize(mutant.mutant_id)}_{func_name}"
    call = f"{module_name}.{func_name}({', '.join(repr(a) for a in args)})"
    header = [f"# Mutant {mutant.mutant_id} | {mutant.operator_name} | {mutant.location}"]
    orig_line = _one_line(mutant.original_snippet)
    mut_line = _one_line(mutant.mutated_snippet)
    if orig_line or mut_line:
        header.append(f"# original: {orig_line}  |  mutated: {mut_line}")
    header.append("# Discriminating input found and re-verified by differential probing.")
    if mode == "raises":
        exc_name = orig_encoded["__exc__"]
        if orig_encoded.get("__exc_module__") == module_name:
            exc_ref = f"{module_name}.{exc_name}"  # exception defined in the target module
        else:
            exc_ref = exc_name  # builtin — resolves bare inside the test file
        body = [
            f"def {test_name}() -> None:",
            f"    with pytest.raises({exc_ref}):",
            f"        {call}",
        ]
        expected_repr = f"raises {exc_ref}"
    else:
        value = orig_encoded["value"]
        type_name = orig_encoded["type"]
        if type_name == "float":
            assertion = f"assert {call} == pytest.approx({value!r})"
        elif type_name in ("bool", "NoneType"):
            assertion = f"assert {call} is {value!r}"
        else:
            assertion = f"assert {call} == {value!r}"
        body = [f"def {test_name}() -> None:", f"    {assertion}"]
        expected_repr = repr(value)
    return HealedTest(
        mutant_id=mutant.mutant_id,
        function_name=func_name,
        module=module_name,
        input_repr=repr(tuple(args)),
        expected_repr=expected_repr,
        test_name=test_name,
        test_source="\n".join(header + body) + "\n",
    )


# --------------------------------------------------------------------------- #
# Candidate input generation                                                  #
# --------------------------------------------------------------------------- #


def _candidate_pools(
    fn_orig: ast.FunctionDef,
    mutant: Mutant,
    module_tree: ast.Module,
    *,
    extra_strings: list[str] | None = None,
) -> list[list[Any]]:
    """Build one deterministic candidate pool per parameter, or raise.

    Pools follow §9: the fixed int pool (floats reuse it), harvested numeric
    literals from the function's AST (original and mutated versions, plus ±1
    neighbors), and string constants harvested from the module AST plus
    ``FALLBACK_STRINGS`` plus cross-function synthesized strings
    (``extra_strings``). Unsupported signatures raise ``_Unhealable``.
    """
    arguments = fn_orig.args
    if arguments.vararg is not None or arguments.kwarg is not None:
        raise _Unhealable("*args/**kwargs signatures are unsupported")
    if any(default is None for default in arguments.kw_defaults):
        raise _Unhealable("required keyword-only parameters are unsupported")
    params = list(arguments.posonlyargs) + list(arguments.args)

    int_extras, float_extras = _numeric_literals(fn_orig, mutant)
    str_pool = _string_pool(module_tree)
    for composed in _concat_pairs(str_pool):
        if composed not in str_pool:
            str_pool.append(composed)
    for synthesized in extra_strings or []:
        if synthesized not in str_pool:
            str_pool.append(synthesized)

    type_pools: dict[str, list[Any]] = {
        "int": _merge_numeric(list(INT_POOL), int_extras),
        "float": _merge_numeric(
            [float(v) for v in INT_POOL] + list(FLOAT_EXTRAS), float_extras
        ),
        "bool": list(BOOL_POOL),
        "str": list(str_pool),
        "None": [None],
    }
    pools: list[list[Any]] = []
    for param in params:
        names = _resolve_hint(param.annotation, module_tree)
        if not names or names == ["None"]:
            raise _Unhealable(
                f"parameter {param.arg!r} lacks a supported scalar type hint"
            )
        pool: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for name in names:  # union members in declaration order
            for value in type_pools[name]:
                key = (type(value).__name__, repr(value))
                if key not in seen:
                    seen.add(key)
                    pool.append(value)
        pools.append(pool)
    return pools


def _numeric_literals(fn_orig: ast.FunctionDef, mutant: Mutant) -> tuple[list[int], list[float]]:
    """Harvest numeric literals (with ±1 neighbors) from the function's AST.

    Both the original and the mutated version of the function are harvested so
    the boundary is covered from either side of the mutation. Returns sorted
    ``(int_extras, float_extras)``; float extras include the int harvest, and
    int extras include integral float literals.
    """
    functions = [fn_orig]
    try:
        mut_tree = ast.parse(mutant.mutated_source)
    except SyntaxError:
        mut_tree = None
    if mut_tree is not None:
        fn_mut = _find_top_level_function(mut_tree, fn_orig.name)
        if fn_mut is not None:
            functions.append(fn_mut)

    ints: set[int] = set()
    floats: set[float] = set()

    def _add(value: Any) -> None:
        if type(value) is int:
            ints.update((value - 1, value, value + 1))
        elif type(value) is float and math.isfinite(value):
            floats.update((value - 1.0, value, value + 1.0))

    for fn in functions:
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.UnaryOp)
                and isinstance(node.op, ast.USub)
                and isinstance(node.operand, ast.Constant)
                and type(node.operand.value) in (int, float)
            ):
                _add(-node.operand.value)
            elif isinstance(node, ast.Constant):
                _add(node.value)

    int_extras = set(ints)
    for f in floats:
        if f.is_integer() and abs(f) <= 2**53:
            int_extras.add(int(f))
    float_extras = set(floats)
    for i in ints:
        try:
            float_extras.add(float(i))
        except OverflowError:
            continue
    return sorted(int_extras), sorted(float_extras)


def _string_pool(module_tree: ast.Module) -> list[str]:
    """String constants from the module AST (docstrings excluded) + fallbacks."""
    docstring_ids: set[int] = set()
    for node in ast.walk(module_tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_ids.add(id(body[0].value))
    pool: list[str] = []
    for node in ast.walk(module_tree):
        if (
            isinstance(node, ast.Constant)
            and type(node.value) is str
            and id(node) not in docstring_ids
            and node.value not in pool
        ):
            pool.append(node.value)
    for extra in FALLBACK_STRINGS:
        if extra not in pool:
            pool.append(extra)
    return pool


def _synth_function_names(
    module_tree: ast.Module, exclude_function: str
) -> list[str]:
    """Top-level ``str -> str`` single-parameter functions usable as generators.

    A function qualifies when it takes exactly one required parameter hinted
    ``str`` (optional/keyword parameters with defaults are fine), returns
    ``str`` per its annotation, and is not the function under probe (feeding a
    function its own output proves nothing about a mutation inside it).
    Sorted by name and capped for determinism.
    """
    names: list[str] = []
    for node in module_tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name == exclude_function:
            continue
        arguments = node.args
        if arguments.vararg is not None or arguments.kwarg is not None:
            continue
        positional = list(arguments.posonlyargs) + list(arguments.args)
        required = positional[: len(positional) - len(arguments.defaults)]
        if len(required) != 1 or _hint_name(required[0].annotation) != "str":
            continue
        if any(default is None for default in arguments.kw_defaults):
            continue
        if _hint_name(node.returns) != "str":
            continue
        names.append(node.name)
    return sorted(names)[:_SYNTH_MAX_FUNCTIONS]


def _synthesized_strings(
    pristine: Path,
    module_name: str,
    module_tree: ast.Module,
    *,
    exclude_function: str,
) -> list[str]:
    """Compose candidate strings via the module's own string-producing API.

    Runs a synthesis subprocess in the PRISTINE copy that applies each
    qualifying sibling ``str -> str`` function to a deterministic set of base
    strings (harvested module constants plus ``LONG_SENTENCE``). The composed
    outputs — rendered prompts, formatted payloads — reach code paths that
    literal pools cannot. Failures degrade to an empty list, never to an
    error: synthesis is an input-pool enrichment, not required evidence.
    """
    functions = _synth_function_names(module_tree, exclude_function)
    if not functions:
        return []
    bases = [s for s in _string_pool(module_tree) if s not in FALLBACK_STRINGS]
    bases = bases[:_SYNTH_MAX_BASES] + [LONG_SENTENCE]
    spec = {
        "module": module_name,
        "functions": functions,
        "bases": bases,
        "max_len": _SYNTH_MAX_LEN,
    }
    spec_path = pristine / _INPUTS_FILENAME
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    (pristine / _SYNTH_FILENAME).write_text(_SYNTH_TEMPLATE, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, _SYNTH_FILENAME, _INPUTS_FILENAME],
            cwd=pristine,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []
    try:
        raw = json.loads(proc.stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item not in out:
            out.append(item)
        if len(out) >= _SYNTH_MAX_RESULTS:
            break
    return out


def _concat_pairs(str_pool: list[str]) -> list[str]:
    """Deterministic pairwise concatenations of the first pool strings.

    Harvested module constants order first in the pool, so they dominate the
    capped base set; results are length-capped and deduplicated in generation
    order.
    """
    bases = [s for s in str_pool if s][:_CONCAT_MAX_BASES]
    out: list[str] = []
    for left in bases:
        for right in bases:
            composed = left + right
            if len(composed) <= _CONCAT_MAX_LEN and composed not in out:
                out.append(composed)
            if len(out) >= _CONCAT_MAX_RESULTS:
                return out
    return out


def _merge_numeric(base: list[Any], extras: list[Any]) -> list[Any]:
    """Append extras not already present (by value) to the base pool."""
    pool = list(base)
    for value in extras:
        if not any(value == existing for existing in pool):
            pool.append(value)
    return pool


def _candidate_key(args: tuple[Any, ...]) -> tuple[tuple[str, str], ...]:
    """Identity key distinguishing ``1``, ``1.0`` and ``True`` (all ``==``)."""
    return tuple((type(a).__name__, repr(a)) for a in args)


def _interleaved_product(
    pools: list[list[Any]],
    max_inputs: int,
    seed: int,
    anchors: list[Any] | None = None,
) -> list[tuple[Any, ...]]:
    """Candidate inputs in a deterministic, boundary-first order, capped.

    Two phases: (1) **anchored sweeps** — the all-anchors tuple, then every
    parameter swept through its whole pool while the others hold their
    anchors (defaults), so each parameter's far boundaries are reached even
    when the product is enormous; (2) the Cartesian product enumerated by
    increasing total pool-index sum (diagonal order) with a seeded shuffle
    inside each level, so combinations vary *every* parameter within the
    cap. Duplicates are dropped by exact type-and-value identity.
    """
    if max_inputs <= 0 or any(len(pool) == 0 for pool in pools):
        return []
    if not pools:
        return [()]
    out: list[tuple[Any, ...]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()

    def add(args: tuple[Any, ...]) -> bool:
        key = _candidate_key(args)
        if key in seen:
            return len(out) >= max_inputs
        seen.add(key)
        out.append(args)
        return len(out) >= max_inputs

    if anchors is not None and len(anchors) == len(pools):
        base = tuple(anchors)
        if add(base):
            return out
        for position, pool in enumerate(pools):
            for value in pool:
                swept = base[:position] + (value,) + base[position + 1:]
                if add(swept):
                    return out

    rng = random.Random(seed)
    sizes = [len(pool) for pool in pools]
    for level in range(sum(size - 1 for size in sizes) + 1):
        level_indices = list(_index_tuples_with_sum(sizes, level))
        rng.shuffle(level_indices)
        for indices in level_indices:
            if add(tuple(pool[i] for pool, i in zip(pools, indices))):
                return out
    return out


def _index_tuples_with_sum(sizes: list[int], total: int) -> Iterator[tuple[int, ...]]:
    """Yield index tuples (lexicographic) with ``sum(indices) == total``."""
    if not sizes:
        if total == 0:
            yield ()
        return
    first_size, rest = sizes[0], sizes[1:]
    rest_max = sum(size - 1 for size in rest)
    for i in range(max(0, total - rest_max), min(first_size - 1, total) + 1):
        for tail in _index_tuples_with_sum(rest, total - i):
            yield (i, *tail)


# --------------------------------------------------------------------------- #
# Subprocess probe protocol                                                   #
# --------------------------------------------------------------------------- #


def _run_probe(
    project_dir: Path, candidates: list[tuple[Any, ...]]
) -> list[dict[str, Any]] | None:
    """Run the generated probe script in ``project_dir`` over ``candidates``.

    Returns the decoded per-candidate result list, or ``None`` on timeout,
    non-zero exit, or unparsable output (the caller treats that as
    unhealable — never as evidence).
    """
    inputs_path = project_dir / _INPUTS_FILENAME
    inputs_path.write_text(
        json.dumps([list(args) for args in candidates]), encoding="utf-8"
    )
    try:
        proc = subprocess.run(
            [sys.executable, _PROBE_FILENAME, _INPUTS_FILENAME],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if (
            isinstance(data, list)
            and len(data) == len(candidates)
            and all(isinstance(item, dict) for item in data)
        ):
            return data
        return None
    return None


# --------------------------------------------------------------------------- #
# Discrimination predicates                                                   #
# --------------------------------------------------------------------------- #


def _first_discriminating(
    orig_results: list[dict[str, Any]],
    mut_results: list[dict[str, Any]],
    module_name: str | None = None,
) -> tuple[str | None, int]:
    """Find the first discriminating candidate index.

    Preference per §9: an input where the ORIGINAL does not raise wins
    (``"value"`` mode). Only if none exists is an input accepted where the
    original raises a referenceable exception (builtin, or defined in the
    probed module) and the mutant does not (``"raises"`` mode). Returns
    ``(mode, index)`` or ``(None, -1)``.
    """
    for i, (orig, mut) in enumerate(zip(orig_results, mut_results)):
        if _is_value(orig) and _value_assertion_kills(orig, mut):
            return "value", i
    for i, (orig, mut) in enumerate(zip(orig_results, mut_results)):
        if _raises_assertion_kills(orig, mut, module_name):
            return "raises", i
    return None, -1


def _is_value(result: dict[str, Any]) -> bool:
    return "type" in result


def _value_assertion_kills(orig: dict[str, Any], mut: dict[str, Any]) -> bool:
    """True iff the value assertion emitted for ``orig`` fails against ``mut``.

    This mirrors the exact assertion that would be generated (``is`` for
    bool/None, ``==`` for int/str, ``pytest.approx`` tolerances for float), so
    "discriminating" here means precisely "the emitted test kills the mutant".
    Conservative on anything uncertain (non-finite floats, exotic result
    types): returns False rather than risking a test we cannot stand behind.
    """
    orig_type, orig_value = orig["type"], orig["value"]
    if orig_type not in ("int", "float", "bool", "str", "NoneType"):
        return False
    if orig_type == "float" and not (
        isinstance(orig_value, (int, float)) and math.isfinite(orig_value)
    ):
        return False
    if "__exc__" in mut:
        return True  # the assertion's call raises on the mutant — loud failure
    if "unsupported" in mut:
        return True  # non-scalar mutant result can never satisfy a scalar assertion
    mut_type, mut_value = mut["type"], mut["value"]
    if orig_type == "NoneType":
        return mut_type != "NoneType"
    if orig_type == "bool":
        return not (mut_type == "bool" and mut_value == orig_value)
    if orig_type == "str":
        return not (mut_type == "str" and mut_value == orig_value)
    if orig_type == "float":
        if mut_type not in ("int", "float", "bool"):
            return True
        if isinstance(mut_value, float) and math.isnan(mut_value):
            return True
        try:
            diff = abs(float(mut_value) - float(orig_value))
        except OverflowError:
            return False
        return diff > max(APPROX_REL_TOL * abs(float(orig_value)), APPROX_ABS_TOL)
    # orig_type == "int": plain == comparison; Python cross-type numeric
    # equality is exact, so an equal-valued float/bool would NOT be killed.
    if mut_type in ("int", "float", "bool"):
        return mut_value != orig_value
    return True


def _raises_assertion_kills(
    orig: dict[str, Any], mut: dict[str, Any], module_name: str | None = None
) -> bool:
    """True iff ``pytest.raises(<orig exc>)`` would fail against the mutant.

    The original exception must be referenceable from the generated test file:
    either a *builtin* type (bare name) or a type defined in the probed target
    module itself (referenced as ``<module>.<ExcName>``, which the test file
    already imports). The mutant must not raise at all — differing exception
    types are deliberately not treated as evidence.
    """
    exc_name = orig.get("__exc__")
    if not exc_name:
        return False
    referenceable = _is_builtin_exception(exc_name) or (
        module_name is not None
        and orig.get("__exc_module__") == module_name
        and orig.get("__exc_is_module_attr__") is True
        and exc_name.isidentifier()
    )
    if not referenceable:
        return False
    return "__exc__" not in mut


def _is_builtin_exception(name: str) -> bool:
    candidate = getattr(builtins, name, None)
    return isinstance(candidate, type) and issubclass(candidate, BaseException)


# --------------------------------------------------------------------------- #
# Small helpers                                                               #
# --------------------------------------------------------------------------- #


def _copy_project(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git", ".venv"),
    )


def _find_top_level_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _hint_name(annotation: ast.expr | None) -> str | None:
    """Resolve a parameter annotation to a plain type name, if possible."""
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.strip()
    return None


_MAX_ALIAS_DEPTH = 4


def _resolve_hint(
    annotation: ast.expr | None, module_tree: ast.Module, _depth: int = 0
) -> list[str] | None:
    """Resolve an annotation to the ordered scalar type names it accepts.

    Supports plain names (``int``), string annotations (``"float | None"``),
    PEP 604 unions (``int | str``), ``None``/``Optional``-style members, and
    module-level aliases such as ``NumberOrString: TypeAlias = float | str``
    (looked up anywhere in the module AST, including ``TYPE_CHECKING``
    blocks, and resolved recursively). Returns ``None`` for anything else
    (generics, containers, unknown names) so the caller can refuse honestly.
    """
    if annotation is None or _depth > _MAX_ALIAS_DEPTH:
        return None
    if isinstance(annotation, ast.Constant):
        if annotation.value is None:
            return ["None"]
        if isinstance(annotation.value, str):
            try:
                parsed = ast.parse(annotation.value.strip(), mode="eval").body
            except SyntaxError:
                return None
            return _resolve_hint(parsed, module_tree, _depth + 1)
        return None
    if isinstance(annotation, ast.Name):
        if annotation.id in _SCALAR_HINTS:
            return [annotation.id]
        if annotation.id == "None":
            return ["None"]
        alias_value = _module_alias_value(module_tree, annotation.id)
        if alias_value is None:
            return None
        return _resolve_hint(alias_value, module_tree, _depth + 1)
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left = _resolve_hint(annotation.left, module_tree, _depth + 1)
        right = _resolve_hint(annotation.right, module_tree, _depth + 1)
        if left is None or right is None:
            return None
        merged: list[str] = []
        for name in left + right:
            if name not in merged:
                merged.append(name)
        return merged
    return None


def _module_alias_value(module_tree: ast.Module, name: str) -> ast.expr | None:
    """Value expression of a module-level ``name = ...`` / ``name: T = ...``."""
    for node in ast.walk(module_tree):
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
    return None


def _module_name_for(rel_path: str) -> str:
    """``app/pricing.py`` → ``app.pricing`` (or raise ``_Unhealable``)."""
    path = PurePosixPath(rel_path.replace("\\", "/"))
    if path.suffix != ".py":
        raise _Unhealable(f"not a python module: {rel_path}")
    parts = path.with_suffix("").parts
    if not parts or not all(part.isidentifier() for part in parts):
        raise _Unhealable(f"cannot derive an importable module name from {rel_path}")
    return ".".join(parts)


def _sanitize(text: str) -> str:
    """Reduce arbitrary text to a safe identifier fragment for test names."""
    cleaned = re.sub(r"\W", "_", text)
    return cleaned or "unknown"


def _one_line(text: str, limit: int = 70) -> str:
    """Collapse a snippet to a single bounded line for header comments."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."

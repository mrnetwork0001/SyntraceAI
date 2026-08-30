"""AST mutation engine for SyntraceAI (ARCHITECTURE.md §6).

Seven single-site mutation operators are applied one mutation per mutant:
each candidate site gets its own fresh ``ast.parse`` of the original source,
the site is mutated in that private tree, and ``ast.unparse`` produces the
full mutated file source. A shared tree is never mutated. Every emitted
mutant is gated through ``compile(mutated_source, path, "exec")`` and
discarded if it does not compile cleanly; renders that are byte-identical
to the unparsed original (no-op mutations) are discarded too.

Determinism: sites are discovered in pre-order depth-first traversal, the
final list is stably sorted by ``(file_path, line_no, col_offset,
operator_name)``, and mutants whose mutated source duplicates an earlier
one for the same file are dropped (e.g. ``ConstantMutation`` n+1 colliding
with ``BoundaryValueMutation`` +1 on the same comparison constant), so the
bank never contains two IDs for the same bug. ``select_bank`` is a seeded,
stratified round-robin across ``(file, operator)`` groups; ``random.Random``
with an int seed is platform-stable, so the same inputs and seed yield a
byte-identical bank on any machine.
"""

from __future__ import annotations

import ast
import random
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, ClassVar, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from advanced.core_types import CODE_BANK_SIZE, DEFAULT_SEED, Mutant
from advanced.target_config import is_excluded, load_target_config

__all__ = [
    "ArithmeticOperatorSwap",
    "ComparisonOperatorSwap",
    "BooleanOperatorSwap",
    "ConditionNegation",
    "ConstantMutation",
    "BoundaryValueMutation",
    "ReturnValueMutation",
    "ALL_OPERATORS",
    "enumerate_mutants",
    "select_bank",
]


@dataclass(frozen=True)
class _AppliedMutation:
    """Result of applying one mutation variant to one AST site."""

    line_no: int
    col_offset: int
    original_snippet: str
    mutated_snippet: str
    description: str


# A variant is a zero-arg closure that mutates its captured node in place
# (in the private per-mutant tree) and reports what it changed. During site
# counting the closures are built but never invoked.
_Variant = Callable[[], _AppliedMutation]

_ARITH_SWAPS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Div,
    ast.Mod: ast.Mult,
}
_ARITH_SYMBOLS: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
}
_CMP_SWAPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
}
_CMP_SYMBOLS: dict[type[ast.cmpop], str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_DOC_HOLDERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class MutationOperator:
    """Base class for single-site mutation operators.

    Subclasses set the class attribute ``name`` and implement
    :meth:`variants`, which inspects one AST node and returns one lazy
    variant per distinct mutation this operator can perform at that node.
    Invoking a variant mutates the node in place and returns the applied
    mutation's metadata.
    """

    name: ClassVar[str] = ""

    def variants(self, node: ast.AST, docstring_ids: frozenset[int]) -> list[_Variant]:
        raise NotImplementedError


class ArithmeticOperatorSwap(MutationOperator):
    """Swap binary arithmetic operators: + <-> -, * <-> /, // -> /, % -> *."""

    name: ClassVar[str] = "ArithmeticOperatorSwap"

    def variants(self, node: ast.AST, docstring_ids: frozenset[int]) -> list[_Variant]:
        if not isinstance(node, ast.BinOp) or type(node.op) not in _ARITH_SWAPS:
            return []

        def apply(binop: ast.BinOp = node) -> _AppliedMutation:
            old_sym = _ARITH_SYMBOLS[type(binop.op)]
            new_type = _ARITH_SWAPS[type(binop.op)]
            before = ast.unparse(binop)
            binop.op = new_type()
            after = ast.unparse(binop)
            return _AppliedMutation(
                line_no=binop.lineno,
                col_offset=binop.col_offset,
                original_snippet=before,
                mutated_snippet=after,
                description=f"arithmetic swap: '{old_sym}' -> '{_ARITH_SYMBOLS[new_type]}'",
            )

        return [apply]


class ComparisonOperatorSwap(MutationOperator):
    """Swap comparison operators: == <-> !=, < <-> <=, > <-> >=."""

    name: ClassVar[str] = "ComparisonOperatorSwap"

    def variants(self, node: ast.AST, docstring_ids: frozenset[int]) -> list[_Variant]:
        if not isinstance(node, ast.Compare):
            return []
        out: list[_Variant] = []
        for position, op in enumerate(node.ops):
            if type(op) not in _CMP_SWAPS:
                continue

            def apply(cmp: ast.Compare = node, pos: int = position) -> _AppliedMutation:
                old_type = type(cmp.ops[pos])
                new_type = _CMP_SWAPS[old_type]
                before = ast.unparse(cmp)
                cmp.ops[pos] = new_type()
                after = ast.unparse(cmp)
                return _AppliedMutation(
                    line_no=cmp.lineno,
                    col_offset=cmp.col_offset,
                    original_snippet=before,
                    mutated_snippet=after,
                    description=(
                        f"comparison swap: '{_CMP_SYMBOLS[old_type]}' -> "
                        f"'{_CMP_SYMBOLS[new_type]}'"
                    ),
                )

            out.append(apply)
        return out


class BooleanOperatorSwap(MutationOperator):
    """Swap boolean operators: and <-> or."""

    name: ClassVar[str] = "BooleanOperatorSwap"

    def variants(self, node: ast.AST, docstring_ids: frozenset[int]) -> list[_Variant]:
        if not isinstance(node, ast.BoolOp):
            return []

        def apply(boolop: ast.BoolOp = node) -> _AppliedMutation:
            is_and = isinstance(boolop.op, ast.And)
            before = ast.unparse(boolop)
            boolop.op = ast.Or() if is_and else ast.And()
            after = ast.unparse(boolop)
            old_sym, new_sym = ("and", "or") if is_and else ("or", "and")
            return _AppliedMutation(
                line_no=boolop.lineno,
                col_offset=boolop.col_offset,
                original_snippet=before,
                mutated_snippet=after,
                description=f"boolean swap: '{old_sym}' -> '{new_sym}'",
            )

        return [apply]


class ConditionNegation(MutationOperator):
    """Negate an if-statement condition: ``if c:`` -> ``if not c:``."""

    name: ClassVar[str] = "ConditionNegation"

    def variants(self, node: ast.AST, docstring_ids: frozenset[int]) -> list[_Variant]:
        if not isinstance(node, ast.If) or _is_type_checking_guard(node.test):
            return []

        def apply(stmt: ast.If = node) -> _AppliedMutation:
            before = f"if {ast.unparse(stmt.test)}:"
            stmt.test = ast.UnaryOp(op=ast.Not(), operand=stmt.test)
            after = f"if {ast.unparse(stmt.test)}:"
            return _AppliedMutation(
                line_no=stmt.lineno,
                col_offset=stmt.col_offset,
                original_snippet=before,
                mutated_snippet=after,
                description="negate if-condition",
            )

        return [apply]


class ConstantMutation(MutationOperator):
    """Mutate literal constants: int/float n -> n+1 (so 0 -> 1), True <-> False.

    Docstring constants (the first statement string of a module, class, or
    function body) are never touched. String constants are never mutated,
    so prompt-like text is safe even outside the excluded-file list.
    """

    name: ClassVar[str] = "ConstantMutation"

    def variants(self, node: ast.AST, docstring_ids: frozenset[int]) -> list[_Variant]:
        if not isinstance(node, ast.Constant) or id(node) in docstring_ids:
            return []
        value = node.value
        if not isinstance(value, bool) and type(value) not in (int, float):
            return []

        def apply(const: ast.Constant = node) -> _AppliedMutation:
            old = const.value
            new = (not old) if isinstance(old, bool) else old + 1
            const.value = new
            return _AppliedMutation(
                line_no=const.lineno,
                col_offset=const.col_offset,
                original_snippet=repr(old),
                mutated_snippet=repr(new),
                description=f"constant {old!r} -> {new!r}",
            )

        return [apply]


class BoundaryValueMutation(MutationOperator):
    """Shift a numeric constant operand of a comparison by +1 and by -1."""

    name: ClassVar[str] = "BoundaryValueMutation"

    def variants(self, node: ast.AST, docstring_ids: frozenset[int]) -> list[_Variant]:
        if not isinstance(node, ast.Compare):
            return []
        out: list[_Variant] = []
        for operand in (node.left, *node.comparators):
            if not isinstance(operand, ast.Constant) or type(operand.value) not in (int, float):
                continue
            for delta in (1, -1):

                def apply(
                    cmp: ast.Compare = node,
                    const: ast.Constant = operand,
                    shift: int = delta,
                ) -> _AppliedMutation:
                    old = const.value
                    before = ast.unparse(cmp)
                    const.value = old + shift
                    after = ast.unparse(cmp)
                    return _AppliedMutation(
                        line_no=const.lineno,
                        col_offset=const.col_offset,
                        original_snippet=before,
                        mutated_snippet=after,
                        description=f"boundary shift: {old!r} -> {const.value!r} in comparison",
                    )

                out.append(apply)
        return out


class ReturnValueMutation(MutationOperator):
    """Replace a return value with None: ``return expr`` -> ``return None``.

    Bare ``return`` and ``return None`` are skipped (already None).
    """

    name: ClassVar[str] = "ReturnValueMutation"

    def variants(self, node: ast.AST, docstring_ids: frozenset[int]) -> list[_Variant]:
        if not isinstance(node, ast.Return) or node.value is None:
            return []
        if isinstance(node.value, ast.Constant) and node.value.value is None:
            return []

        def apply(ret: ast.Return = node) -> _AppliedMutation:
            before = ast.unparse(ret)
            ret.value = ast.Constant(value=None)
            return _AppliedMutation(
                line_no=ret.lineno,
                col_offset=ret.col_offset,
                original_snippet=before,
                mutated_snippet="return None",
                description="return value replaced with None",
            )

        return [apply]


ALL_OPERATORS: tuple[type[MutationOperator], ...] = (
    ArithmeticOperatorSwap,
    ComparisonOperatorSwap,
    BooleanOperatorSwap,
    ConditionNegation,
    ConstantMutation,
    BoundaryValueMutation,
    ReturnValueMutation,
)


def _type_checking_names(tree: ast.Module) -> frozenset[str]:
    """Names bound to ``TYPE_CHECKING`` in *tree*, including import aliases."""
    names = {"TYPE_CHECKING"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("typing", "typing_extensions"):
            for alias in node.names:
                if alias.name == "TYPE_CHECKING" and alias.asname:
                    names.add(alias.asname)
    return frozenset(names)


def _is_type_checking_guard(
    test: ast.expr, names: frozenset[str] = frozenset({"TYPE_CHECKING"})
) -> bool:
    """``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:`` / aliased forms.

    These guards are False at runtime by definition: everything inside them is
    type-only code that the test suite never executes, so negating the guard
    or mutating its body would seed the bank with unkillable mutants.
    """
    if isinstance(test, ast.Name):
        return test.id in names
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _equivalent_clamp_swap_sites(tree: ast.Module) -> frozenset[tuple[int, int]]:
    """Coordinates of Compare nodes where an ordering-equality swap is a no-op.

    Pattern: ``if x >cmp C: x = C`` with no else branch, where ``cmp`` is one of
    ``> >= < <=``, ``x`` is a plain name, and ``C`` is the same non-zero constant
    on both lines. Swapping ``>`` <-> ``>=`` (or ``<`` <-> ``<=``) only changes
    behavior at ``x == C`` - where the body assigns the very same value - so
    the mutant is equivalent under the documented assumptions: ``x`` holds a
    standard numeric of the same type as ``C`` (an int-valued float crossing an
    int constant, or vice versa, would make the boundary assignment a type
    change), no custom rich-comparison objects, and no signed-zero observation
    (zero constants are excluded outright). Those assumptions hold for ordinary
    clamp code; sites that cannot satisfy them simply stay in the bank and are
    judged empirically like any other mutant. Excluding the provable cases
    keeps unkillable bugs out of the bank, which would otherwise misstate the
    mutation score.
    """
    sites: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and not node.orelse and len(node.body) == 1):
            continue
        test, body = node.test, node.body[0]
        if not (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and type(test.ops[0]) in (ast.Gt, ast.GtE, ast.Lt, ast.LtE)
            and isinstance(test.left, ast.Name)
            and isinstance(test.comparators[0], ast.Constant)
        ):
            continue
        bound = test.comparators[0].value
        if not isinstance(bound, (int, float)) or isinstance(bound, bool) or bound == 0:
            continue  # zero constants excluded: signed-zero makes the swap observable
        if (
            isinstance(body, ast.Assign)
            and len(body.targets) == 1
            and isinstance(body.targets[0], ast.Name)
            and body.targets[0].id == test.left.id
            and isinstance(body.value, ast.Constant)
            and type(body.value.value) is type(bound)
            and body.value.value == bound
        ):
            sites.add((test.lineno, test.col_offset))
    return frozenset(sites)


def _docstring_node_ids(tree: ast.Module) -> frozenset[int]:
    """``id()``s of Constant nodes that operators must never mutate.

    Docstrings (the first statement string of a module, class, or function)
    and the value of the ``TYPE_CHECKING = False`` idiom - flipping that flag
    only executes type-only imports at runtime, an unkillable no-op.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DOC_HOLDERS) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if (
            targets
            and all(isinstance(t, ast.Name) and t.id == "TYPE_CHECKING" for t in targets)
            and isinstance(node.value, ast.Constant)
        ):
            ids.add(id(node.value))
    return frozenset(ids)


def _iter_sites(
    tree: ast.Module,
    operator: MutationOperator,
    docstring_ids: frozenset[int],
) -> Iterator[tuple[str, _Variant]]:
    """Yield ``(function_qualname, variant)`` in deterministic pre-order DFS.

    The qualname joins enclosing class/function names with ``"."`` (e.g.
    ``"Grader.passing"``); sites not inside any function report ``""``.
    """
    scope: list[tuple[str, bool]] = []  # (name, is_function)
    tc_names = _type_checking_names(tree)

    def qualname() -> str:
        if any(is_fn for _, is_fn in scope):
            return ".".join(name for name, _ in scope)
        return ""

    def visit(node: ast.AST) -> Iterator[tuple[str, _Variant]]:
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test, tc_names):
            # Type-only block: no site in the guard or its body is ever executed.
            for child in node.orelse:
                yield from visit(child)
            return
        for variant in operator.variants(node, docstring_ids):
            yield qualname(), variant
        opens_scope = isinstance(node, _SCOPE_NODES)
        if opens_scope:
            scope.append((node.name, not isinstance(node, ast.ClassDef)))
        for child in ast.iter_child_nodes(node):
            yield from visit(child)
        if opens_scope:
            scope.pop()

    yield from visit(tree)


def _mutants_for_file(source: str, rel_path: str) -> list[Mutant]:
    """Enumerate every valid single-site mutant of one source file."""
    baseline_render = ast.unparse(ast.parse(source, filename=rel_path))
    clamp_sites = _equivalent_clamp_swap_sites(ast.parse(source, filename=rel_path))
    mutants: list[Mutant] = []
    for operator_type in ALL_OPERATORS:
        operator = operator_type()
        probe_tree = ast.parse(source, filename=rel_path)
        site_count = sum(
            1 for _ in _iter_sites(probe_tree, operator, _docstring_node_ids(probe_tree))
        )
        for index in range(site_count):
            tree = ast.parse(source, filename=rel_path)  # fresh, private tree
            sites = list(_iter_sites(tree, operator, _docstring_node_ids(tree)))
            function_name, variant = sites[index]
            applied = variant()
            try:
                mutated_source = ast.unparse(tree)
                compile(mutated_source, rel_path, "exec")
            except (SyntaxError, ValueError, RecursionError):
                continue  # AST validation sandboxing: discard non-compiling mutants
            if mutated_source == baseline_render:
                continue  # mutation was a semantic no-op
            if (
                operator.name == ComparisonOperatorSwap.name
                and (applied.line_no, applied.col_offset) in clamp_sites
            ):
                continue  # proven-equivalent clamp-boundary swap (see helper above)
            mutants.append(
                Mutant(
                    mutant_id="",
                    file_path=rel_path,
                    operator_name=operator.name,
                    line_no=applied.line_no,
                    col_offset=applied.col_offset,
                    function_name=function_name,
                    original_snippet=applied.original_snippet,
                    mutated_snippet=applied.mutated_snippet,
                    mutated_source=mutated_source,
                    description=applied.description,
                )
            )
    return mutants


def enumerate_mutants(
    target_dir: Path,
    *,
    exclude: tuple[str, ...] | None = None,
) -> list[Mutant]:
    """Enumerate all valid single-site mutants of the target's source package.

    The package to scan comes from the target adapter config
    (``syntrace_target.json``; default ``app/``). Skips excluded relative
    paths (default: the configured prompt-templates module, which belongs to
    the prompt perturbator), ``__init__.py``, and anything under the tests or
    ``__pycache__`` directories. The result is stably sorted by
    ``(file_path, line_no, col_offset, operator_name)`` and deduplicated on
    identical mutated file sources; ``mutant_id`` is left empty ("") - IDs
    are assigned by :func:`select_bank` after selection.

    Raises ``FileNotFoundError`` if the source package does not exist and
    propagates ``SyntaxError`` if a target source file does not parse - a
    broken target must fail loudly, not shrink the bank silently.
    """
    target_dir = Path(target_dir).resolve()
    config = load_target_config(target_dir)
    package_dir = target_dir / config.source_package
    if not package_dir.is_dir():
        raise FileNotFoundError(
            f"target has no {config.source_package}/ package: {package_dir}"
        )
    if exclude is None:
        exclude = config.mutation_excludes
    excluded = tuple(entry.replace("\\", "/") for entry in exclude)
    tests_parts = tuple(Path(config.tests_dir).parts)

    mutants: list[Mutant] = []
    paths = sorted(package_dir.rglob("*.py"), key=lambda p: p.relative_to(target_dir).as_posix())
    for path in paths:
        relative = path.relative_to(target_dir)
        rel_path = relative.as_posix()
        if (
            path.name == "__init__.py"
            or "tests" in relative.parts
            or relative.parts[: len(tests_parts)] == tests_parts
            or "__pycache__" in relative.parts
            or is_excluded(rel_path, excluded)
        ):
            continue
        source = path.read_text(encoding="utf-8")
        mutants.extend(_mutants_for_file(source, rel_path))

    mutants.sort(key=lambda m: (m.file_path, m.line_no, m.col_offset, m.operator_name))
    seen: set[tuple[str, str]] = set()
    unique: list[Mutant] = []
    for mutant in mutants:
        key = (mutant.file_path, mutant.mutated_source)
        if key in seen:
            continue  # same bug produced by two operators - keep the first
        seen.add(key)
        unique.append(mutant)
    return unique


def select_bank(
    mutants: list[Mutant],
    size: int = CODE_BANK_SIZE,
    seed: int = DEFAULT_SEED,
) -> list[Mutant]:
    """Select a stratified bank of *size* mutants and assign IDs ``M001``…

    Mutants are grouped by ``(file_path, operator_name)``; each group is
    shuffled with a single ``random.Random(seed)`` (groups processed in
    sorted-key order), then groups are drained round-robin - one mutant per
    group per round, in sorted-key order, skipping exhausted groups - so the
    bank spans all files and operator types. IDs are assigned after
    selection, in selection order. If fewer than *size* mutants exist, all
    of them are returned. The input list and its elements are not modified.
    """
    if size <= 0 or not mutants:
        return []

    groups: dict[tuple[str, str], list[Mutant]] = {}
    for mutant in mutants:
        groups.setdefault((mutant.file_path, mutant.operator_name), []).append(mutant)

    rng = random.Random(seed)
    ordered_keys = sorted(groups)
    for key in ordered_keys:
        rng.shuffle(groups[key])

    selected: list[Mutant] = []
    round_index = 0
    while len(selected) < size:
        took_any = False
        for key in ordered_keys:
            bucket = groups[key]
            if round_index < len(bucket):
                selected.append(bucket[round_index])
                took_any = True
                if len(selected) == size:
                    break
        if not took_any:
            break  # every group exhausted
        round_index += 1

    return [
        replace(mutant, mutant_id=f"M{position:03d}")
        for position, mutant in enumerate(selected, start=1)
    ]

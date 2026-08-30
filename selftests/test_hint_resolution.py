"""Selftests for the healer's type-hint resolution (unions, aliases, None)."""

from __future__ import annotations

import ast

import pytest

from advanced.test_healer import _resolve_hint

MODULE = '''
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeAlias
    NumberOrString: TypeAlias = float | str

Nested = NumberOrString | None
Loop = Loop | int

def f(a: int, b: NumberOrString, c: int | None, d: "float | str", e: list[int], g: Nested): ...
'''


@pytest.fixture(scope="module")
def module_tree() -> ast.Module:
    return ast.parse(MODULE)


def _annotation(module_tree: ast.Module, param: str) -> ast.expr:
    fn = next(n for n in module_tree.body if isinstance(n, ast.FunctionDef))
    return next(a.annotation for a in fn.args.args if a.arg == param)


@pytest.mark.parametrize(
    ("param", "expected"),
    [
        ("a", ["int"]),
        ("b", ["float", "str"]),           # alias inside a TYPE_CHECKING block
        ("c", ["int", "None"]),            # PEP 604 optional
        ("d", ["float", "str"]),           # string annotation
        ("e", None),                       # generic container: refused honestly
        ("g", ["float", "str", "None"]),   # alias of an alias, plus None
    ],
)
def test_resolution(module_tree: ast.Module, param: str, expected: list[str] | None) -> None:
    assert _resolve_hint(_annotation(module_tree, param), module_tree) == expected


def test_self_referential_alias_terminates(module_tree: ast.Module) -> None:
    assert _resolve_hint(ast.Name(id="Loop"), module_tree) is None


def test_unknown_name_refused(module_tree: ast.Module) -> None:
    assert _resolve_hint(ast.Name(id="Mystery"), module_tree) is None

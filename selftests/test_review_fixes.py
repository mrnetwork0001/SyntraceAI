"""Selftests for the adversarial code-review fixes (round 2).

Each test pins a failure mode that an executed repro demonstrated: adapter
paths escaping the target, exclude typos silently excluding nothing,
TYPE_CHECKING bodies and aliases yielding unkillable mutants, non-finite
anchors, out-of-tree sibling paths, and hash-order-dependent healing.
"""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

from advanced.ast_mutator import enumerate_mutants
from advanced.run_mutation import sibling_output_paths
from advanced.target_config import TargetConfigError, is_excluded, load_target_config
from advanced.test_healer import _anchor_values, heal_survivors


def _target(tmp_path: Path, config: dict, files: dict[str, str]) -> Path:
    target = tmp_path / "target"
    for rel, source in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    (target / "syntrace_target.json").write_text(json.dumps(config))
    return target


PKG = {"pkg/__init__.py": "", "pkg/a.py": "def f(x: int) -> int:\n    return x + 1\n",
       "pkg/sub/__init__.py": "", "pkg/sub/b.py": "def g(x: int) -> int:\n    return x - 1\n",
       "tests/__init__.py": ""}


@pytest.mark.parametrize("key", ["source_package", "tests_dir", "prompt_templates"])
def test_parent_traversal_rejected(tmp_path: Path, key: str) -> None:
    (tmp_path / "outside").mkdir()
    config = {"source_package": "pkg", "prompt_templates": None, key: "../outside"}
    target = _target(tmp_path, config, PKG)
    with pytest.raises(TargetConfigError, match="inside the target"):
        load_target_config(target)


def test_exclude_traversal_and_typos_rejected(tmp_path: Path) -> None:
    for bad in (["../x.py"], ["pkg/aa.py"], ["pkg/nope"]):
        target = _target(tmp_path / bad[0].replace("/", "_"), {"source_package": "pkg", "prompt_templates": None, "exclude": bad}, PKG)
        with pytest.raises(TargetConfigError):
            load_target_config(target)


def test_exclude_directory_and_dot_prefix(tmp_path: Path) -> None:
    target = _target(tmp_path, {"source_package": "pkg", "prompt_templates": None, "exclude": ["./pkg/sub"]}, PKG)
    config = load_target_config(target)
    assert config.exclude == ("pkg/sub",)
    assert is_excluded("pkg/sub/b.py", config.mutation_excludes)
    assert not is_excluded("pkg/a.py", config.mutation_excludes)
    assert config.coverage_omit(target) == ("pkg/sub/*",)
    assert {m.file_path for m in enumerate_mutants(target)} == {"pkg/a.py"}


TC_MODULE = '''
from typing import TYPE_CHECKING as TC
TYPE_CHECKING = False
if TYPE_CHECKING:
    LIMIT = 7
    def never(x: int) -> int:
        return x + 1
if TC:
    from typing import Any

def f(x: int) -> int:
    if TC:
        return x + 100
    return x * 2
'''


def test_type_checking_bodies_and_aliases_never_mutated(tmp_path: Path) -> None:
    target = _target(tmp_path, {"source_package": "pkg", "prompt_templates": None},
                     {"pkg/__init__.py": "", "pkg/tc.py": TC_MODULE, "tests/__init__.py": ""})
    mutants = enumerate_mutants(target)
    assert mutants, "the live branch must still yield mutants"
    live_lines = {m.line_no for m in mutants}
    guarded_lines = {i for i, line in enumerate(TC_MODULE.splitlines(), start=1)
                     if "LIMIT" in line or "never" in line or "x + 1" in line
                     or "x + 100" in line or "import Any" in line}
    assert not (live_lines & guarded_lines), sorted(live_lines & guarded_lines)
    assert all(m.function_name == "f" for m in mutants)
    assert not any(m.operator_name == "ConditionNegation" for m in mutants)


def test_non_finite_default_is_not_an_anchor() -> None:
    fn = ast.parse("def scale(v: float = 1e999, n: int = 1): ...").body[0]
    assert isinstance(fn, ast.FunctionDef)
    anchors = _anchor_values(fn, [[0.0, 2.0], [0, 1]])
    assert anchors == [0.0, 1]


def test_sibling_paths_out_of_tree_and_dot_prefixed(tmp_path: Path) -> None:
    html, traj = sibling_output_paths(str(tmp_path / "out.json"), None, None)
    assert Path(traj).parent == tmp_path and Path(html).parent == tmp_path
    _, traj2 = sibling_output_paths("./reports/mutation_report.json", None, None)
    assert traj2 == "trajectories/agent_trace_02.json"


HASH_MODULE = '''
def tags(n: int) -> str:
    s = {"alpha", "beta"}
    return ",".join(s) + str(n > 1)
'''


def test_hash_order_dependent_output_is_refused(tmp_path: Path) -> None:
    target = _target(tmp_path, {"source_package": "pkg", "prompt_templates": None},
                     {"pkg/__init__.py": "", "pkg/h.py": HASH_MODULE, "tests/__init__.py": "",
                      "tests/test_h.py": "from pkg.h import tags\n\ndef test_len():\n    assert len(tags(0)) > 5\n"})
    mutant = next(m for m in enumerate_mutants(target)
                  if m.operator_name == "ComparisonOperatorSwap" and ">=" in m.mutated_snippet)
    mutant = replace(mutant, mutant_id="M001")
    # Five attempts: a single-subprocess gate would let ~half of them through.
    for _ in range(5):
        healed, unhealable = heal_survivors(target, [mutant])
        assert healed == [] and unhealable == ["M001"]

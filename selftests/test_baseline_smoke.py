"""Smoke selftests for baseline/run_baseline.py (ARCHITECTURE.md §11).

The baseline script imports sibling modules (advanced.ast_mutator,
advanced.prompt_perturbator, advanced.sandbox_runner) that are owned by
other agents and may land later. These tests verify everything that can be
verified standalone - the script compiles and carries the §3 bootstrap
header - and run the live ``--help`` check only once the siblings are
importable (the integrator exercises the full flow).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "baseline" / "run_baseline.py"

_SIBLING_MODULES = (
    "advanced.ast_mutator",
    "advanced.prompt_perturbator",
    "advanced.sandbox_runner",
)


def _siblings_importable() -> bool:
    """True once every sibling module run_baseline.py imports exists."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        return all(importlib.util.find_spec(name) is not None for name in _SIBLING_MODULES)
    except ModuleNotFoundError:
        return False


def test_script_compiles() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    compile(source, str(SCRIPT), "exec")


def test_bootstrap_header_present() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in source
    assert "if str(REPO_ROOT) not in sys.path:" in source
    assert "sys.path.insert(0, str(REPO_ROOT))" in source
    # The bootstrap must run before any advanced.* import is attempted.
    assert source.index("sys.path.insert(0, str(REPO_ROOT))") < source.index("from advanced.")


@pytest.mark.skipif(
    not _siblings_importable(),
    reason=(
        "sibling advanced modules (ast_mutator/prompt_perturbator/sandbox_runner) "
        "not written yet; the integrator runs the full flow"
    ),
)
def test_help_exits_zero_and_lists_contract_flags() -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    for flag in ("--target", "--jobs", "--seed", "--json"):
        assert flag in run.stdout

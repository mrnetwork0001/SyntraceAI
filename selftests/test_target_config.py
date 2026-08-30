"""Selftests for the target adapter config (advanced/target_config.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from advanced.target_config import TargetConfig, TargetConfigError, load_target_config


def _make_target(tmp_path: Path, config: dict | None, package: str = "app") -> Path:
    target = tmp_path / "target"
    (target / package).mkdir(parents=True)
    (target / package / "__init__.py").write_text("")
    (target / "tests").mkdir()
    if config is not None:
        (target / "syntrace_target.json").write_text(json.dumps(config))
    return target


def test_missing_config_with_prompt_module_yields_demo_defaults(tmp_path: Path) -> None:
    target = _make_target(tmp_path, None)
    (target / "app" / "prompt_templates.py").write_text("SYSTEM_PROMPT = ''\n")
    config = load_target_config(target)
    assert config == TargetConfig()
    assert config.has_prompts
    assert config.mutation_excludes == ("app/prompt_templates.py",)


def test_plain_project_without_prompts_runs_code_only(tmp_path: Path) -> None:
    """A textbook app/ + tests/ project with no prompt module must not crash.

    Regression: the default prompt path was assumed to exist, so the headline
    "point it at your own project" flow died with a PromptContractError
    traceback for exactly the layout the UI says needs no config file.
    """
    from advanced.prompt_perturbator import enumerate_perturbations

    target = _make_target(tmp_path, None)
    config = load_target_config(target)
    assert not config.has_prompts
    assert config.mutation_excludes == ()
    assert enumerate_perturbations(target) == []


def test_explicit_prompt_path_typo_still_fails_loudly(tmp_path: Path) -> None:
    target = _make_target(tmp_path, {"prompt_templates": "app/typo.py"})
    with pytest.raises(TargetConfigError, match="prompt_templates"):
        load_target_config(target)


def test_code_only_target(tmp_path: Path) -> None:
    target = _make_target(
        tmp_path,
        {
            "source_package": "mylib",
            "tests_dir": "tests",
            "prompt_templates": None,
            "exclude": ["mylib/skip_me.py"],
        },
        package="mylib",
    )
    (target / "mylib" / "skip_me.py").write_text("X = 1\n")  # excludes must exist
    config = load_target_config(target)
    assert config.source_package == "mylib"
    assert not config.has_prompts
    assert config.mutation_excludes == ("mylib/skip_me.py",)


def test_partial_config_fills_defaults(tmp_path: Path) -> None:
    target = _make_target(tmp_path, {"prompt_templates": None})
    config = load_target_config(target)
    assert config.source_package == "app"
    assert config.tests_dir == "tests"
    assert config.prompt_templates is None


def test_prompt_module_must_exist(tmp_path: Path) -> None:
    target = _make_target(tmp_path, {"prompt_templates": "app/nope.py"})
    with pytest.raises(TargetConfigError, match="prompt_templates"):
        load_target_config(target)


def test_source_package_must_exist(tmp_path: Path) -> None:
    target = _make_target(tmp_path, {"source_package": "ghost", "prompt_templates": None})
    with pytest.raises(TargetConfigError, match="source_package"):
        load_target_config(target)


@pytest.mark.parametrize(
    "bad",
    [
        {"source_package": "/abs", "prompt_templates": None},
        {"exclude": "not-a-list", "prompt_templates": None},
        {"tests_dir": "", "prompt_templates": None},
    ],
)
def test_malformed_values_rejected(tmp_path: Path, bad: dict) -> None:
    target = _make_target(tmp_path, bad)
    with pytest.raises(TargetConfigError):
        load_target_config(target)


def test_invalid_json_rejected(tmp_path: Path) -> None:
    target = _make_target(tmp_path, None)
    (target / "syntrace_target.json").write_text("{not json")
    with pytest.raises(TargetConfigError, match="not valid JSON"):
        load_target_config(target)


def test_mutator_honours_adapter_config(tmp_path: Path) -> None:
    from advanced.ast_mutator import enumerate_mutants

    target = _make_target(
        tmp_path,
        {"source_package": "mylib", "prompt_templates": None, "exclude": ["mylib/skip.py"]},
        package="mylib",
    )
    (target / "mylib" / "core.py").write_text("def f(x: int) -> int:\n    return x + 1\n")
    (target / "mylib" / "skip.py").write_text("def g(x: int) -> int:\n    return x - 1\n")
    mutants = enumerate_mutants(target)
    files = {m.file_path for m in mutants}
    assert "mylib/core.py" in files
    assert "mylib/skip.py" not in files


def test_perturbator_returns_empty_for_code_only_target(tmp_path: Path) -> None:
    from advanced.prompt_perturbator import enumerate_perturbations

    target = _make_target(tmp_path, {"source_package": "mylib", "prompt_templates": None}, "mylib")
    assert enumerate_perturbations(target) == []

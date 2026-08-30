"""Target adapter: describes how a project under test is laid out.

A target directory may carry a ``syntrace_target.json`` file::

    {
      "source_package": "humanize",          # package dir to mutate (import name)
      "tests_dir": "tests",                  # pytest suite dir, relative to target
      "prompt_templates": null,              # prompt module rel path, or null
      "exclude": ["humanize/time.py"]        # optional: rel paths never mutated
    }

Missing file or missing keys fall back to the demo layout (``app/``, ``tests/``,
``app/prompt_templates.py``), so the original sample target needs no config.
Targets without a prompt module run a code-only campaign.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = "syntrace_target.json"


class TargetConfigError(ValueError):
    """``syntrace_target.json`` is malformed or points at a missing path."""


@dataclass(frozen=True)
class TargetConfig:
    source_package: str = "app"
    tests_dir: str = "tests"
    prompt_templates: str | None = "app/prompt_templates.py"
    exclude: tuple[str, ...] = ()

    @property
    def has_prompts(self) -> bool:
        return self.prompt_templates is not None

    @property
    def mutation_excludes(self) -> tuple[str, ...]:
        """Relative paths the mutator must skip: the prompt module + explicit excludes."""
        paths = list(self.exclude)
        if self.prompt_templates and self.prompt_templates not in paths:
            paths.insert(0, self.prompt_templates)
        return tuple(paths)


def load_target_config(target_dir: Path) -> TargetConfig:
    """Load the target's adapter config, falling back to demo-layout defaults."""
    target_dir = Path(target_dir)
    path = target_dir / CONFIG_FILENAME
    if not path.is_file():
        return TargetConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TargetConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TargetConfigError(f"{path} must contain a JSON object")

    defaults = TargetConfig()
    source_package = data.get("source_package", defaults.source_package)
    tests_dir = data.get("tests_dir", defaults.tests_dir)
    prompt_templates = data.get("prompt_templates", defaults.prompt_templates)
    exclude = data.get("exclude", [])
    if not isinstance(exclude, list) or not all(
        isinstance(entry, str) and entry and not Path(entry).is_absolute() for entry in exclude
    ):
        raise TargetConfigError(f"{path}: 'exclude' must be a list of relative paths")

    for name, value in (("source_package", source_package), ("tests_dir", tests_dir)):
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise TargetConfigError(f"{path}: {name!r} must be a non-empty relative path")
    if prompt_templates is not None and (
        not isinstance(prompt_templates, str) or Path(prompt_templates).is_absolute()
    ):
        raise TargetConfigError(f"{path}: 'prompt_templates' must be a relative path or null")
    if not (target_dir / source_package).is_dir():
        raise TargetConfigError(
            f"{path}: source_package {source_package!r} is not a directory under {target_dir}"
        )
    if prompt_templates is not None and not (target_dir / prompt_templates).is_file():
        raise TargetConfigError(
            f"{path}: prompt_templates {prompt_templates!r} does not exist under {target_dir}"
        )
    return TargetConfig(
        source_package=source_package.replace("\\", "/").strip("/"),
        tests_dir=tests_dir.replace("\\", "/").strip("/"),
        prompt_templates=(
            prompt_templates.replace("\\", "/").strip("/") if prompt_templates else None
        ),
        exclude=tuple(entry.replace("\\", "/").strip("/") for entry in exclude),
    )

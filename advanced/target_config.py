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
        """Relative paths the mutator must skip: the prompt module + explicit excludes.

        An entry names either a file or a directory (matched as a prefix).
        """
        paths = list(self.exclude)
        if self.prompt_templates and self.prompt_templates not in paths:
            paths.insert(0, self.prompt_templates)
        return tuple(paths)

    def coverage_omit(self, target_dir: Path) -> tuple[str, ...]:
        """``--omit`` patterns for coverage.py: directories become ``<dir>/*``."""
        patterns: list[str] = []
        for entry in self.exclude:
            patterns.append(f"{entry}/*" if (target_dir / entry).is_dir() else entry)
        return tuple(patterns)


def is_excluded(rel_path: str, excludes: tuple[str, ...]) -> bool:
    """True if *rel_path* equals an exclude entry or lies under an excluded directory."""
    return any(rel_path == entry or rel_path.startswith(entry + "/") for entry in excludes)


_UNSET = object()


def load_target_config(target_dir: Path) -> TargetConfig:
    """Load the target's adapter config, falling back to demo-layout defaults.

    The default prompt module is *auto-detected*: a project laid out as
    ``app/`` + ``tests/`` with no prompt templates simply runs a code-only
    campaign, rather than failing because a file it never claimed to have is
    missing. An explicitly configured ``prompt_templates`` path still must
    exist - a typo there is a real error.
    """
    target_dir = Path(target_dir)
    path = target_dir / CONFIG_FILENAME
    if not path.is_file():
        defaults = TargetConfig()
        prompts = defaults.prompt_templates
        if prompts and not (target_dir / prompts).is_file():
            prompts = None
        return TargetConfig(prompt_templates=prompts)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TargetConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TargetConfigError(f"{path} must contain a JSON object")

    defaults = TargetConfig()
    source_package = data.get("source_package", defaults.source_package)
    tests_dir = data.get("tests_dir", defaults.tests_dir)
    prompt_templates = data.get("prompt_templates", _UNSET)
    if prompt_templates is _UNSET:  # not configured: auto-detect, never crash
        prompt_templates = defaults.prompt_templates
        if prompt_templates and not (target_dir / prompt_templates).is_file():
            prompt_templates = None
    exclude = data.get("exclude", [])
    if not isinstance(exclude, list):
        raise TargetConfigError(f"{path}: 'exclude' must be a list of relative paths")

    def safe_rel(value: object, label: str) -> str:
        """Normalize a config path and refuse anything that could leave the target."""
        if not isinstance(value, str) or not value.strip():
            raise TargetConfigError(f"{path}: {label!r} must be a non-empty relative path")
        normalized = value.replace("\\", "/")
        if Path(normalized).is_absolute():
            raise TargetConfigError(f"{path}: {label!r} must be relative, got {value!r}")
        parts = [p for p in normalized.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            raise TargetConfigError(
                f"{path}: {label!r} must stay inside the target directory, got {value!r}"
            )
        return "/".join(parts)

    source_package = safe_rel(source_package, "source_package")
    tests_dir = safe_rel(tests_dir, "tests_dir")
    if prompt_templates is not None:
        prompt_templates = safe_rel(prompt_templates, "prompt_templates")
    exclude = tuple(safe_rel(entry, "exclude") for entry in exclude)

    if not (target_dir / source_package).is_dir():
        raise TargetConfigError(
            f"{path}: source_package {source_package!r} is not a directory under {target_dir}"
        )
    if prompt_templates is not None and not (target_dir / prompt_templates).is_file():
        raise TargetConfigError(
            f"{path}: prompt_templates {prompt_templates!r} does not exist under {target_dir}"
        )
    for entry in exclude:
        if not (target_dir / entry).exists():
            raise TargetConfigError(
                f"{path}: exclude entry {entry!r} does not exist under {target_dir} "
                "(a typo here would silently exclude nothing)"
            )
    return TargetConfig(
        source_package=source_package,
        tests_dir=tests_dir,
        prompt_templates=prompt_templates,
        exclude=exclude,
    )

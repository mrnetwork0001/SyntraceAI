"""Output-contract validation for TriageBot model completions.

Two parsing modes:

- ``parse_strict``: the completion must be exactly one valid JSON object
  that satisfies the ``TriageResult`` schema, otherwise ``ContractViolation``.
- ``parse_lenient``: best-effort salvage that never raises — it fishes the
  first ``{...}`` blob out of prose, fills missing keys with defaults, and
  coerces types. This is a deliberate real-world antipattern: it silently
  converts contract breaches into plausible-looking results.
"""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

REQUIRED_KEYS: tuple[str, str, str, str] = ("category", "priority", "confidence", "summary")

_JSON_BLOB = re.compile(r"\{.*?\}", re.DOTALL)


class ContractViolation(Exception):
    """The model output violates the triage JSON contract."""


class TriageResult(BaseModel):
    """Strict schema for a triage completion."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["billing", "bug", "account", "performance", "general"]
    priority: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)


def parse_strict(raw: str) -> dict[str, Any]:
    """Parse and validate a completion; raise ``ContractViolation`` on any breach."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractViolation(f"output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractViolation("output JSON is not an object")
    try:
        model = TriageResult.model_validate(data)
    except ValidationError as exc:
        raise ContractViolation(f"output violates the triage schema: {exc}") from exc
    return model.model_dump()


def _coerce_str(value: Any, default: str) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_lenient(raw: str) -> dict[str, Any]:
    """Salvage a triage dict from arbitrary model output; never raises.

    Extra keys the model emitted are kept as-is; the four required keys are
    always present, defaulted (``general`` / 1 / 0.0 / ``""``) and coerced
    to their expected types.
    """
    salvaged: dict[str, Any] = {}
    match = _JSON_BLOB.search(raw)
    if match is not None:
        try:
            candidate = json.loads(match.group(0))
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict):
            salvaged = dict(candidate)

    result: dict[str, Any] = dict(salvaged)
    result["category"] = _coerce_str(salvaged.get("category"), "general")
    result["priority"] = _coerce_int(salvaged.get("priority"), 1)
    result["confidence"] = _coerce_float(salvaged.get("confidence"), 0.0)
    result["summary"] = _coerce_str(salvaged.get("summary"), "")
    return result

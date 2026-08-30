"""TriageBot pipeline: prompt assembly, mock LLM, and end-to-end triage.

``mock_llm`` is a deterministic, rule-based stand-in for a hosted model.
It is a pure function of the prompt string and it *reads its own
instructions*: when contract-critical lines are missing or corrupted in
the prompt, it degrades the way real LLMs do - hallucinated keys, wrong
value types, dropped fields, and chatty prose around the JSON.
"""

import json
import re
from typing import Any

from app import scoring
from app.prompt_templates import FEW_SHOT_BLOCK, SYSTEM_PROMPT, TICKET_TEMPLATE
from app.validators import parse_lenient, parse_strict

# Marker substrings the mock model keys off (must match the prompt templates).
REQUIRED_KEYS_MARKER = "Required JSON keys:"
ROLE_MARKER = "You are TriageBot"
JSON_ONLY_MARKER = "Respond ONLY with a single valid JSON object"
PRIORITY_RULE_MARKER = "Priority must be an integer"
CONFIDENCE_RULE_MARKER = "Confidence must be a number"
OUTPUT_RULES_MARKER = "### OUTPUT RULES ###"
FEW_SHOT_MARKER = "Example"
TICKET_MARKER = "### TICKET ###"
SECTION_PREFIX = "### "

# Keys the model invents when the Required-keys line is missing/corrupted.
HALLUCINATION_KEYS: tuple[str, ...] = ("category", "priority", "summary", "extra_thoughts")

_QUOTED_NAME = re.compile(r'"([^"]+)"')

_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("billing", ("refund", "charge", "billing")),
    ("bug", ("crash", "error", "bug", "broken")),
    ("account", ("password", "login", "2fa")),
    ("performance", ("slow", "latency", "timeout")),
)

_BASE_PRIORITY: dict[str, int] = {
    "billing": 3,
    "bug": 4,
    "account": 3,
    "performance": 2,
    "general": 1,
}

_URGENCY_WORDS: tuple[str, ...] = ("urgent", "asap", "immediately")


def build_prompt(ticket_text: str) -> str:
    """Assemble the full triage prompt for one ticket."""
    return TICKET_TEMPLATE.format(
        system=SYSTEM_PROMPT,
        few_shot=FEW_SHOT_BLOCK,
        ticket=ticket_text,
    )


def _required_keys(prompt: str) -> list[str]:
    """Key names the model believes it must emit.

    Parsed from the ``Required JSON keys:`` line; if that line is missing
    or corrupted, the model hallucinates its own schema.
    """
    for line in prompt.splitlines():
        if REQUIRED_KEYS_MARKER in line:
            names = _QUOTED_NAME.findall(line)
            if names:
                return names
    return list(HALLUCINATION_KEYS)


def _extract_ticket(prompt: str) -> str:
    """Ticket text between ``### TICKET ###`` and the next ``### `` marker.

    Returns ``""`` when the ticket marker is missing entirely.
    """
    start = prompt.find(TICKET_MARKER)
    if start == -1:
        return ""
    rest = prompt[start + len(TICKET_MARKER):]
    next_section = rest.find(SECTION_PREFIX)
    if next_section == -1:
        return rest
    return rest[:next_section]


def _categorize(ticket_lower: str) -> str:
    """First keyword family that matches wins; default is ``general``."""
    for category, keywords in _CATEGORY_KEYWORDS:
        for keyword in keywords:
            if keyword in ticket_lower:
                return category
    return "general"


def _priority_for(category: str, ticket_lower: str) -> int:
    """Base priority per category, +1 for urgency wording, capped at 5."""
    priority = _BASE_PRIORITY[category]
    for word in _URGENCY_WORDS:
        if word in ticket_lower:
            priority += 1
            break
    return min(priority, 5)


def _priority_word(priority: int) -> str:
    """Verbal priority the model emits when the integer type rule is gone."""
    if priority >= 4:
        return "critical"
    if priority == 3:
        return "high"
    if priority == 2:
        return "medium"
    return "low"


def mock_llm(prompt: str) -> str:
    """Deterministic rule-based LLM: prompt string in, raw completion out.

    Degradation rules (each keyed to a contract line in the prompt):
    missing Required-keys line -> hallucinated schema; missing integer
    type rule -> verbal priority; missing confidence-range rule ->
    confidence as an integer percentage; missing role line -> confidence
    dropped plus a hedging note; missing few-shot examples -> summary
    field forgotten; missing OUTPUT RULES section -> trailing commentary
    after the JSON; missing JSON-only directive -> prose-wrapped JSON.
    """
    required = _required_keys(prompt)
    ticket = _extract_ticket(prompt)
    ticket_lower = ticket.lower()

    category = _categorize(ticket_lower)
    priority = _priority_for(category, ticket_lower)
    confidence = min(0.95, round(0.6 + 0.05 * len(category), 2))
    words = ticket.split()
    if words:
        summary = " ".join(words[:8])
    else:
        summary = "(no ticket text)"

    known_values: dict[str, Any] = {
        "category": category,
        "priority": priority,
        "confidence": confidence,
        "summary": summary,
    }
    payload: dict[str, Any] = {}
    for name in required:
        if name in known_values:
            payload[name] = known_values[name]
        else:
            payload[name] = f"hallucinated:{name}"

    if PRIORITY_RULE_MARKER not in prompt and "priority" in payload:
        payload["priority"] = _priority_word(priority)

    if CONFIDENCE_RULE_MARKER not in prompt and "confidence" in payload:
        payload["confidence"] = int(round(confidence * 100))

    if FEW_SHOT_MARKER not in prompt:
        payload.pop("summary", None)

    if ROLE_MARKER not in prompt:
        payload.pop("confidence", None)
        payload["note"] = "As an AI language model, I cannot be fully certain."

    body = json.dumps(payload, separators=(",", ":"))
    if OUTPUT_RULES_MARKER not in prompt:
        body += "\n\nHope this helps! Reply if you need a deeper analysis."
    if JSON_ONLY_MARKER not in prompt:
        return (
            "Sure! Here is the triage you asked for:\n```json\n"
            + body
            + "\n```\nLet me know if you need anything else!"
        )
    return body


def triage_ticket(ticket_text: str, *, strict: bool = False) -> dict[str, Any]:
    """Triage one ticket end to end and attach routing decisions.

    ``strict=True`` validates the model output against the JSON contract
    (raising ``ContractViolation`` on any breach); the default lenient
    path salvages what it can and never raises.
    """
    prompt = build_prompt(ticket_text)
    raw = mock_llm(prompt)
    if strict:
        parsed = parse_strict(raw)
    else:
        parsed = parse_lenient(raw)
    result: dict[str, Any] = dict(parsed)
    score = scoring.priority_score(result["priority"], result["confidence"])
    result["priority_score"] = score
    result["escalate"] = scoring.escalation_required(score, result["category"])
    return result

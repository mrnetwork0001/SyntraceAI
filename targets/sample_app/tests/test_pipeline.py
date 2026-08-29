"""End-to-end tests for the triage pipeline: prompt build, mock LLM, parsing."""

import json

import pytest

from app.llm_pipeline import build_prompt, mock_llm, triage_ticket
from app.validators import REQUIRED_KEYS, ContractViolation, parse_lenient, parse_strict


class TestBuildPrompt:
    """build_prompt assembles the ticket into the template."""

    def test_ticket_text_is_embedded(self) -> None:
        prompt = build_prompt("The app is slow today")
        assert "The app is slow today" in prompt

    def test_prompt_mentions_the_schema(self) -> None:
        prompt = build_prompt("hello")
        assert "category" in prompt
        assert "priority" in prompt


class TestMockLlm:
    """mock_llm produces a deterministic completion for a prompt."""

    def test_returns_parseable_json(self) -> None:
        raw = mock_llm(build_prompt("I need help with my invoice"))
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_billing_ticket_is_categorized(self) -> None:
        raw = mock_llm(build_prompt("Please refund my last charge"))
        data = json.loads(raw)
        assert data["category"] == "billing"

    def test_urgent_bug_gets_high_priority(self) -> None:
        raw = mock_llm(build_prompt("Urgent: the export crashes every time"))
        data = json.loads(raw)
        assert data["priority"] >= 4

    def test_same_prompt_same_output(self) -> None:
        prompt = build_prompt("My password reset link never arrives")
        assert mock_llm(prompt) == mock_llm(prompt)


class TestTriageTicket:
    """triage_ticket runs the whole pipeline and attaches routing info."""

    def test_returns_a_result(self) -> None:
        result = triage_ticket("I was charged twice, please refund me")
        assert result is not None

    def test_result_has_expected_keys(self) -> None:
        result = triage_ticket("The login page crashes with an error")
        for key in REQUIRED_KEYS:
            assert key in result

    def test_routing_fields_are_attached(self) -> None:
        result = triage_ticket("Everything is very slow since the update")
        assert "priority_score" in result
        assert "escalate" in result

    def test_urgent_bug_escalates(self) -> None:
        result = triage_ticket("URGENT: checkout is broken and throwing errors")
        assert result["escalate"] is True

    def test_vague_ticket_falls_back_to_general(self) -> None:
        result = triage_ticket("Just wanted to say thanks!")
        assert result["category"] == "general"


class TestParsers:
    """Direct tests for the strict and lenient output parsers."""

    def test_strict_accepts_a_valid_payload(self) -> None:
        raw = json.dumps(
            {"category": "bug", "priority": 4, "confidence": 0.9, "summary": "Crash on save"}
        )
        data = parse_strict(raw)
        assert data["category"] == "bug"

    def test_strict_rejects_non_json(self) -> None:
        with pytest.raises(ContractViolation):
            parse_strict("sorry, no json today")

    def test_lenient_salvages_json_from_prose(self) -> None:
        raw = 'Here you go: {"category": "billing", "priority": 2, "confidence": 0.8, "summary": "x"} hope that helps!'
        data = parse_lenient(raw)
        assert data["category"] == "billing"

    def test_lenient_never_raises_on_garbage(self) -> None:
        data = parse_lenient("total garbage, not even braces")
        assert data is not None
        assert "category" in data

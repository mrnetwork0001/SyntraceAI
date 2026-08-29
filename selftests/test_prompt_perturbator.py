"""Selftests for advanced.prompt_perturbator (ARCHITECTURE.md section 7).

Builds a contract-conformant ``app/prompt_templates.py`` fixture in a tmp dir
(the section 5.1 marker lines are copied verbatim) and verifies the frozen
12-perturbation bank end to end: IDs and order, compilability of every mutated
source, the semantic effect of each operator, placeholder survival, and the
drift-detection errors.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from advanced.core_types import Perturbation
from advanced.prompt_perturbator import (
    PromptContractError,
    enumerate_perturbations,
    replace_constant,
)

# --- fixture target (section 5.1 marker lines copied verbatim) ---------------

SYSTEM_PROMPT_FIXTURE = (
    "You are TriageBot, a senior support-ticket triage analyst.\n"
    "Respond ONLY with a single valid JSON object and nothing else.\n"
    'Required JSON keys: "category", "priority", "confidence", "summary".\n'
    "Priority must be an integer from 1 (lowest) to 5 (critical).\n"
    "Confidence must be a number between 0.0 and 1.0.\n"
)

FEW_SHOT_FIXTURE = (
    "Ticket: My card was charged twice for one order.\n"
    '{"category": "billing", "priority": 3, "confidence": 0.9, '
    '"summary": "My card was charged twice for"}\n'
    "\n"
    "Ticket: The app crashes when I open settings.\n"
    '{"category": "bug", "priority": 4, "confidence": 0.75, '
    '"summary": "The app crashes when I open settings."}\n'
)

TICKET_TEMPLATE_FIXTURE = (
    "### ROLE ###\n"
    "{system}\n"
    "\n"
    "### EXAMPLES ###\n"
    "{few_shot}\n"
    "\n"
    "### TICKET ###\n"
    "{ticket}\n"
    "\n"
    "### OUTPUT RULES ###\n"
    "Respond ONLY with a single valid JSON object and nothing else.\n"
    "Do not add commentary before or after the JSON object.\n"
)

EXPECTED_BANK = [
    ("P001", "RoleStripping", "SYSTEM_PROMPT"),
    ("P002", "JsonOnlyDirectiveRemoval", "SYSTEM_PROMPT"),
    ("P003", "InstructionNegation", "SYSTEM_PROMPT"),
    ("P004", "SchemaKeyRename", "SYSTEM_PROMPT"),
    ("P005", "SchemaKeyRename", "SYSTEM_PROMPT"),
    ("P006", "SchemaKeyRename", "SYSTEM_PROMPT"),
    ("P007", "TypeRuleRemoval", "SYSTEM_PROMPT"),
    ("P008", "RangeRuleRemoval", "SYSTEM_PROMPT"),
    ("P009", "SectionMarkerCorruption", "TICKET_TEMPLATE"),
    ("P010", "SectionMarkerCorruption", "TICKET_TEMPLATE"),
    ("P011", "WhitespaceNoise", "SYSTEM_PROMPT"),
    ("P012", "FewShotDrop", "FEW_SHOT_BLOCK"),
]

ZWSP = "\u200b"
PLACEHOLDERS = ("{system}", "{few_shot}", "{ticket}")


def _write_target(
    tmp_path: Path,
    *,
    system_prompt: str = SYSTEM_PROMPT_FIXTURE,
    few_shot: str | None = FEW_SHOT_FIXTURE,
    ticket_template: str = TICKET_TEMPLATE_FIXTURE,
) -> Path:
    """Materialize a minimal target with ``app/prompt_templates.py``."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    lines = [
        '"""Fixture prompt templates conforming to ARCHITECTURE.md section 5.1."""',
        "",
        f"SYSTEM_PROMPT = {system_prompt!r}",
        "",
    ]
    if few_shot is not None:
        lines += [f"FEW_SHOT_BLOCK = {few_shot!r}", ""]
    lines += [f"TICKET_TEMPLATE = {ticket_template!r}", ""]
    (app_dir / "prompt_templates.py").write_text("\n".join(lines), encoding="utf-8")
    return tmp_path


def _exec_constants(mutated_source: str) -> dict[str, str]:
    """Execute a mutated prompt_templates.py and return its str constants."""
    namespace: dict[str, object] = {}
    exec(compile(mutated_source, "<mutated prompt_templates.py>", "exec"), namespace)
    return {
        name: value
        for name, value in namespace.items()
        if name.isupper() and isinstance(value, str)
    }


@pytest.fixture()
def target_dir(tmp_path: Path) -> Path:
    return _write_target(tmp_path)


@pytest.fixture()
def bank(target_dir: Path) -> list[Perturbation]:
    return enumerate_perturbations(target_dir)


# --- bank shape --------------------------------------------------------------


def test_exactly_twelve_ids_and_order(bank: list[Perturbation]) -> None:
    assert len(bank) == 12
    assert [
        (p.perturbation_id, p.operator_name, p.template_name) for p in bank
    ] == EXPECTED_BANK


def test_every_mutated_source_compiles_and_carries_the_perturbed_value(
    bank: list[Perturbation],
) -> None:
    for p in bank:
        compile(p.mutated_source, "<mutated prompt_templates.py>", "exec")
        constants = _exec_constants(p.mutated_source)
        assert constants[p.template_name] == p.perturbed_template
        # every mutated module still formats a complete prompt without crashing
        constants["TICKET_TEMPLATE"].format(system="S", few_shot="F", ticket="T")


def test_untouched_constants_are_preserved(bank: list[Perturbation]) -> None:
    originals = {
        "SYSTEM_PROMPT": SYSTEM_PROMPT_FIXTURE,
        "FEW_SHOT_BLOCK": FEW_SHOT_FIXTURE,
        "TICKET_TEMPLATE": TICKET_TEMPLATE_FIXTURE,
    }
    for p in bank:
        constants = _exec_constants(p.mutated_source)
        for name, original in originals.items():
            if name != p.template_name:
                assert constants[name] == original, (p.perturbation_id, name)


def test_determinism(target_dir: Path) -> None:
    assert enumerate_perturbations(target_dir) == enumerate_perturbations(target_dir)


# --- per-operator semantics --------------------------------------------------


def test_p001_role_stripping_removes_the_whole_triagebot_line(
    bank: list[Perturbation],
) -> None:
    system = _exec_constants(bank[0].mutated_source)["SYSTEM_PROMPT"]
    assert "TriageBot" not in system
    assert system.startswith("Respond ONLY")  # line AND its newline are gone
    assert system.count("\n") == SYSTEM_PROMPT_FIXTURE.count("\n") - 1


def test_p002_json_only_directive_removed(bank: list[Perturbation]) -> None:
    system = _exec_constants(bank[1].mutated_source)["SYSTEM_PROMPT"]
    assert "Respond ONLY with a single valid JSON object" not in system
    assert system.count("\n") == SYSTEM_PROMPT_FIXTURE.count("\n") - 1
    assert "You are TriageBot" in system  # neighbors untouched


def test_p003_instruction_negation(bank: list[Perturbation]) -> None:
    system = _exec_constants(bank[2].mutated_source)["SYSTEM_PROMPT"]
    assert "You may include helpful commentary around the JSON object." in system
    assert "Respond ONLY with a single valid JSON object and nothing else." not in system
    assert system.count("\n") == SYSTEM_PROMPT_FIXTURE.count("\n")  # replaced, not removed


@pytest.mark.parametrize(
    ("index", "old_key", "new_key"),
    [(3, "confidence", "certainty"), (4, "summary", "synopsis"), (5, "category", "topic")],
)
def test_schema_key_renames(
    bank: list[Perturbation], index: int, old_key: str, new_key: str
) -> None:
    system = _exec_constants(bank[index].mutated_source)["SYSTEM_PROMPT"]
    assert f'"{new_key}"' in system
    assert f'"{old_key}"' not in system
    # only the Required-keys line changed; the rule lines survive verbatim
    assert "Priority must be an integer from 1 (lowest) to 5 (critical)." in system
    assert "Confidence must be a number between 0.0 and 1.0." in system


def test_p007_type_rule_removed(bank: list[Perturbation]) -> None:
    system = _exec_constants(bank[6].mutated_source)["SYSTEM_PROMPT"]
    assert "Priority must be an integer" not in system
    assert "Confidence must be a number" in system
    # the removed line's newline went with it: neighbors are now adjacent
    assert '"summary".\nConfidence must be a number' in system


def test_p008_range_rule_removed(bank: list[Perturbation]) -> None:
    system = _exec_constants(bank[7].mutated_source)["SYSTEM_PROMPT"]
    assert "Confidence must be a number" not in system
    assert "Priority must be an integer" in system


def test_p009_ticket_marker_corrupted_placeholders_intact(
    bank: list[Perturbation],
) -> None:
    template = _exec_constants(bank[8].mutated_source)["TICKET_TEMPLATE"]
    assert "@@@ TICKET @@@" in template
    assert "### TICKET ###" not in template
    for placeholder in PLACEHOLDERS:
        assert placeholder in template
    template.format(system="S", few_shot="F", ticket="T")


def test_p010_output_rules_section_removed_placeholders_intact(
    bank: list[Perturbation],
) -> None:
    template = _exec_constants(bank[9].mutated_source)["TICKET_TEMPLATE"]
    assert "### OUTPUT RULES ###" not in template
    assert "Do not add commentary" not in template  # section body removed too
    for placeholder in PLACEHOLDERS:
        assert placeholder in template
    assert "### TICKET ###" in template  # earlier sections untouched
    template.format(system="S", few_shot="F", ticket="T")


def test_p011_zero_width_space_inserted(bank: list[Perturbation]) -> None:
    system = _exec_constants(bank[10].mutated_source)["SYSTEM_PROMPT"]
    assert ZWSP in system
    assert f"Required JSON{ZWSP} keys" in system
    assert "Required JSON keys" not in system  # literal phrase is broken


def test_p012_few_shot_dropped(bank: list[Perturbation]) -> None:
    assert bank[11].perturbed_template == ""
    assert _exec_constants(bank[11].mutated_source)["FEW_SHOT_BLOCK"] == ""


# --- drift detection ---------------------------------------------------------


def test_missing_marker_line_raises(tmp_path: Path) -> None:
    drifted = SYSTEM_PROMPT_FIXTURE.replace(
        "You are TriageBot, a senior support-ticket triage analyst.\n", ""
    )
    target = _write_target(tmp_path, system_prompt=drifted)
    with pytest.raises(PromptContractError, match="You are TriageBot"):
        enumerate_perturbations(target)


def test_missing_section_marker_raises(tmp_path: Path) -> None:
    drifted = TICKET_TEMPLATE_FIXTURE.replace("### OUTPUT RULES ###", "OUTPUT RULES")
    target = _write_target(tmp_path, ticket_template=drifted)
    with pytest.raises(PromptContractError, match="OUTPUT RULES"):
        enumerate_perturbations(target)


def test_missing_constant_raises(tmp_path: Path) -> None:
    target = _write_target(tmp_path, few_shot=None)
    with pytest.raises(PromptContractError, match="FEW_SHOT_BLOCK"):
        enumerate_perturbations(target)


def test_missing_templates_module_raises(tmp_path: Path) -> None:
    with pytest.raises(PromptContractError, match="not found"):
        enumerate_perturbations(tmp_path)


# --- replace_constant --------------------------------------------------------


def test_replace_constant_swaps_only_the_named_assignment() -> None:
    source = 'GREETING = "hello"\nCOUNT = 3\nOTHER = "keep me"\n'
    mutated = replace_constant(source, "GREETING", "goodbye")
    compile(mutated, "<mutated>", "exec")
    constants = {
        node.targets[0].id: node.value.value
        for node in ast.parse(mutated).body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
    }
    assert constants["GREETING"] == "goodbye"  # re-parse shows the new value
    assert constants["COUNT"] == 3
    assert constants["OTHER"] == "keep me"


def test_replace_constant_missing_name_raises() -> None:
    with pytest.raises(PromptContractError, match="MISSING"):
        replace_constant('PRESENT = "x"\n', "MISSING", "y")


def test_replace_constant_ambiguous_assignment_raises() -> None:
    with pytest.raises(PromptContractError, match="ambiguous"):
        replace_constant('TWICE = "a"\nTWICE = "b"\n', "TWICE", "c")


def test_replace_constant_ignores_non_single_name_targets() -> None:
    with pytest.raises(PromptContractError, match="no module-level"):
        replace_constant('A, B = "x", "y"\n', "A", "z")

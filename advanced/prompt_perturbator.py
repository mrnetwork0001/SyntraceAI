"""Prompt perturbation engine for SyntraceAI (ARCHITECTURE.md section 7).

Enumerates the frozen bank of 12 prompt perturbations (``P001``-``P012``)
against the target's ``app/prompt_templates.py``.  The real constant values are
read from the target module by parsing its AST -- nothing about the target's
wording is assumed beyond the exact section 5.1 marker substrings this module
keys off.  Each perturbation rewrites exactly one module constant via
:func:`replace_constant`, producing a full mutated module source that is
verified to compile and to round-trip (a re-parse shows the new value).

Invariants:

- Line-removal operators remove the entire marker line including its newline.
- Replacements are exact-substring operations on the section 5.1 marker lines;
  a missing marker raises :class:`PromptContractError` (the target has drifted
  from the frozen contract) instead of silently emitting a no-op perturbation.
- No perturbation ever removes a ``{placeholder}`` that ``TICKET_TEMPLATE``
  needs for ``.format`` -- that would crash the pipeline rather than degrade
  the LLM behavior, which is not the failure mode being injected.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ast

from advanced.core_types import PROMPT_BANK_SIZE, Perturbation

__all__ = ["PromptContractError", "enumerate_perturbations", "replace_constant"]

# --- Section 5.1 contract markers (verbatim -- the perturbator keys off these)

ROLE_LINE = "You are TriageBot, a senior support-ticket triage analyst."
JSON_ONLY_LINE = "Respond ONLY with a single valid JSON object and nothing else."
REQUIRED_KEYS_LINE = 'Required JSON keys: "category", "priority", "confidence", "summary".'
PRIORITY_RULE_LINE = "Priority must be an integer from 1 (lowest) to 5 (critical)."
CONFIDENCE_RULE_LINE = "Confidence must be a number between 0.0 and 1.0."
TICKET_MARKER = "### TICKET ###"
CORRUPTED_TICKET_MARKER = "@@@ TICKET @@@"
OUTPUT_RULES_MARKER = "### OUTPUT RULES ###"
NEGATED_DIRECTIVE = "You may include helpful commentary around the JSON object."
REQUIRED_KEYS_PHRASE = "Required JSON keys"
ZERO_WIDTH_SPACE = "\u200b"
NOISED_KEYS_PHRASE = f"Required JSON{ZERO_WIDTH_SPACE} keys"
REQUIRED_PLACEHOLDERS = ("{system}", "{few_shot}", "{ticket}")

_TEMPLATES_REL_PATH = Path("app") / "prompt_templates.py"


class PromptContractError(ValueError):
    """The target's ``app/prompt_templates.py`` drifted from the frozen contract.

    Raised when a required module constant or a section 5.1 marker line cannot
    be found, or when a constant replacement fails verification.
    """


# --- helpers ----------------------------------------------------------------


def _marker_index(text: str, marker: str, constant_name: str) -> int:
    """Return the index of ``marker`` in ``text`` or raise a contract error."""
    index = text.find(marker)
    if index == -1:
        raise PromptContractError(
            f"marker {marker!r} not found in constant {constant_name!r}: the "
            "target's app/prompt_templates.py has drifted from the "
            "ARCHITECTURE.md section 5.1 contract"
        )
    return index


def _remove_marker_line(text: str, marker: str, constant_name: str) -> str:
    """Remove the entire line containing ``marker``, including its newline."""
    index = _marker_index(text, marker, constant_name)
    line_start = text.rfind("\n", 0, index) + 1
    line_end = text.find("\n", index + len(marker))
    line_end = len(text) if line_end == -1 else line_end + 1
    return text[:line_start] + text[line_end:]


def _replace_marker(text: str, old: str, new: str, constant_name: str) -> str:
    """Replace the first occurrence of ``old`` with ``new``; ``old`` must exist."""
    _marker_index(text, old, constant_name)
    return text.replace(old, new, 1)


def _remove_section(text: str, marker: str, constant_name: str) -> str:
    """Remove a ``### NAME ###`` section: its marker line through the content
    that follows, up to (excluding) the next ``### `` marker line or the end
    of the string."""
    index = _marker_index(text, marker, constant_name)
    section_start = text.rfind("\n", 0, index) + 1
    next_marker = text.find("\n### ", index + len(marker))
    section_end = len(text) if next_marker == -1 else next_marker + 1
    return text[:section_start] + text[section_end:]


def _rename_required_key(system_prompt: str, old_key: str, new_key: str) -> str:
    """Rename one quoted key inside the Required-keys marker line only."""
    renamed_line = REQUIRED_KEYS_LINE.replace(f'"{old_key}"', f'"{new_key}"', 1)
    return _replace_marker(system_prompt, REQUIRED_KEYS_LINE, renamed_line, "SYSTEM_PROMPT")


def _assert_placeholders(ticket_template: str, perturbation_label: str) -> None:
    """Guarantee every ``.format`` placeholder survives a perturbation."""
    missing = [p for p in REQUIRED_PLACEHOLDERS if p not in ticket_template]
    if missing:
        raise PromptContractError(
            f"{perturbation_label} would drop required .format placeholder(s) "
            f"{missing} from TICKET_TEMPLATE; refusing to emit a perturbation "
            "that crashes instead of degrading"
        )


def _module_str_constants(source: str) -> dict[str, str]:
    """Extract module-level ``NAME = "..."`` string constants from source."""
    constants: dict[str, str] = {}
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.targets[0].id] = node.value.value
    return constants


def _require_constant(constants: dict[str, str], name: str) -> str:
    try:
        return constants[name]
    except KeyError:
        raise PromptContractError(
            f"app/prompt_templates.py defines no module-level string constant "
            f"{name!r}: the target has drifted from the ARCHITECTURE.md "
            "section 5.1 contract"
        ) from None


def _find_single_assign(tree: ast.Module, name: str) -> ast.Assign:
    """Locate the unique module-level ``name = <value>`` assignment."""
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    if not matches:
        raise PromptContractError(
            f"no module-level single-target assignment to {name!r} found in "
            "the module source"
        )
    if len(matches) > 1:
        raise PromptContractError(
            f"{len(matches)} module-level assignments to {name!r} found; "
            "constant replacement would be ambiguous"
        )
    return matches[0]


# --- public API -------------------------------------------------------------


def replace_constant(module_source: str, name: str, new_value: str) -> str:
    """Return ``module_source`` with the constant ``name`` rebound to ``new_value``.

    AST-based: parses the module, locates the unique ``Assign`` whose single
    target is the ``Name`` ``name``, swaps its value for
    ``ast.Constant(new_value)``, and unparses.  The result is verified to
    compile and to round-trip (re-parsing shows the new value) before it is
    returned.  Raises :class:`PromptContractError` if the assignment is
    missing, ambiguous, or verification fails.
    """
    tree = ast.parse(module_source)
    _find_single_assign(tree, name).value = ast.Constant(value=new_value)
    mutated_source = ast.unparse(ast.fix_missing_locations(tree)) + "\n"

    compile(mutated_source, "<mutated prompt_templates.py>", "exec")
    verified = _find_single_assign(ast.parse(mutated_source), name)
    if not (isinstance(verified.value, ast.Constant) and verified.value.value == new_value):
        raise PromptContractError(
            f"round-trip verification failed: re-parsed source does not carry "
            f"the new value for {name!r}"
        )
    return mutated_source


def enumerate_perturbations(target_dir: Path) -> list[Perturbation]:
    """Enumerate the frozen bank of 12 prompt perturbations for a target.

    Reads the real constants from ``<target_dir>/app/prompt_templates.py``
    (AST parse) and returns exactly :data:`PROMPT_BANK_SIZE` perturbations
    with IDs ``P001``-``P012`` in the ARCHITECTURE.md section 7 order.  Every
    ``mutated_source`` is a complete replacement module that compiles.
    Deterministic: same input file, byte-identical bank.
    """
    templates_path = Path(target_dir) / _TEMPLATES_REL_PATH
    try:
        source = templates_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PromptContractError(
            f"prompt templates module not found at {templates_path}: the "
            "target does not match the ARCHITECTURE.md section 5 layout"
        ) from None

    constants = _module_str_constants(source)
    system_prompt = _require_constant(constants, "SYSTEM_PROMPT")
    _require_constant(constants, "FEW_SHOT_BLOCK")
    ticket_template = _require_constant(constants, "TICKET_TEMPLATE")

    # (operator_name, template_name, description, perturbed_value) in the
    # frozen section 7 order.  Building a value raises PromptContractError as
    # soon as any section 5.1 marker is missing.
    specs: list[tuple[str, str, str, str]] = [
        (
            "RoleStripping",
            "SYSTEM_PROMPT",
            "Remove the 'You are TriageBot...' role line from SYSTEM_PROMPT.",
            _remove_marker_line(system_prompt, ROLE_LINE, "SYSTEM_PROMPT"),
        ),
        (
            "JsonOnlyDirectiveRemoval",
            "SYSTEM_PROMPT",
            "Remove the 'Respond ONLY with a single valid JSON object' "
            "directive line from SYSTEM_PROMPT.",
            _remove_marker_line(system_prompt, JSON_ONLY_LINE, "SYSTEM_PROMPT"),
        ),
        (
            "InstructionNegation",
            "SYSTEM_PROMPT",
            "Replace the JSON-only directive with explicit permission to add "
            "commentary around the JSON object.",
            _replace_marker(system_prompt, JSON_ONLY_LINE, NEGATED_DIRECTIVE, "SYSTEM_PROMPT"),
        ),
        (
            "SchemaKeyRename",
            "SYSTEM_PROMPT",
            "Rename required key \"confidence\" to \"certainty\" in the "
            "Required-keys line.",
            _rename_required_key(system_prompt, "confidence", "certainty"),
        ),
        (
            "SchemaKeyRename",
            "SYSTEM_PROMPT",
            "Rename required key \"summary\" to \"synopsis\" in the "
            "Required-keys line.",
            _rename_required_key(system_prompt, "summary", "synopsis"),
        ),
        (
            "SchemaKeyRename",
            "SYSTEM_PROMPT",
            "Rename required key \"category\" to \"topic\" in the "
            "Required-keys line.",
            _rename_required_key(system_prompt, "category", "topic"),
        ),
        (
            "TypeRuleRemoval",
            "SYSTEM_PROMPT",
            "Remove the 'Priority must be an integer...' type-rule line from "
            "SYSTEM_PROMPT.",
            _remove_marker_line(system_prompt, PRIORITY_RULE_LINE, "SYSTEM_PROMPT"),
        ),
        (
            "RangeRuleRemoval",
            "SYSTEM_PROMPT",
            "Remove the 'Confidence must be a number...' range-rule line from "
            "SYSTEM_PROMPT.",
            _remove_marker_line(system_prompt, CONFIDENCE_RULE_LINE, "SYSTEM_PROMPT"),
        ),
        (
            "SectionMarkerCorruption",
            "TICKET_TEMPLATE",
            "Corrupt the '### TICKET ###' section marker into "
            "'@@@ TICKET @@@' in TICKET_TEMPLATE.",
            _replace_marker(ticket_template, TICKET_MARKER, CORRUPTED_TICKET_MARKER, "TICKET_TEMPLATE"),
        ),
        (
            "SectionMarkerCorruption",
            "TICKET_TEMPLATE",
            "Remove the '### OUTPUT RULES ###' section from TICKET_TEMPLATE "
            "(all .format placeholders preserved).",
            _remove_section(ticket_template, OUTPUT_RULES_MARKER, "TICKET_TEMPLATE"),
        ),
        (
            "WhitespaceNoise",
            "SYSTEM_PROMPT",
            "Insert a zero-width space (U+200B) between 'JSON' and 'keys' in "
            "the Required-keys line of SYSTEM_PROMPT.",
            _replace_marker(system_prompt, REQUIRED_KEYS_PHRASE, NOISED_KEYS_PHRASE, "SYSTEM_PROMPT"),
        ),
        (
            "FewShotDrop",
            "FEW_SHOT_BLOCK",
            "Drop all few-shot worked examples: FEW_SHOT_BLOCK becomes the "
            "empty string.",
            "",
        ),
    ]
    assert len(specs) == PROMPT_BANK_SIZE  # frozen bank composition (section 3)

    perturbations: list[Perturbation] = []
    for index, (operator_name, template_name, description, perturbed_value) in enumerate(
        specs, start=1
    ):
        perturbation_id = f"P{index:03d}"
        effective_ticket = (
            perturbed_value if template_name == "TICKET_TEMPLATE" else ticket_template
        )
        _assert_placeholders(effective_ticket, f"{perturbation_id} {operator_name}")
        perturbations.append(
            Perturbation(
                perturbation_id=perturbation_id,
                template_name=template_name,
                operator_name=operator_name,
                description=description,
                perturbed_template=perturbed_value,
                mutated_source=replace_constant(source, template_name, perturbed_value),
            )
        )
    return perturbations

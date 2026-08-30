# SyntraceAI — Architecture & Module Contract (FROZEN)

This document is the **binding interface contract** for SyntraceAI. Every module MUST
conform exactly to the APIs, file layout, marker strings, and behavior rules below.
Shared dataclasses/enums live in `advanced/core_types.py` and are the single source of
truth — import them, never redefine them.

## 1. What SyntraceAI is

An autonomous adversarial mutation-testing and hallucination stress-testing engine.
It injects a deterministic bank of **50 bugs** (38 AST code mutations + 12 prompt
perturbations) into a target AI application, runs the target's test suite in an
isolated sandbox per bug, and measures the **detection rate**. Surviving bugs are then
**auto-healed**: SyntraceAI performs differential input search between original and
mutated code to synthesize hardened assertion tests, re-runs the campaign, and reports
the post-heal mutation score.

Story the numbers must tell (honestly, from real runs):
- Baseline (`coverage.py` mindset): high line coverage, low bug detection.
- Advanced (SyntraceAI): same bank, auto-healed suite → detection ≥ 90%.

## 2. Repository layout

```
SyntraceAI/
├── main.py                          # CLI hub: baseline | mutate | full
├── baseline/
│   └── run_baseline.py              # coverage.py audit + bank detection w/ original suite
├── advanced/
│   ├── __init__.py
│   ├── core_types.py                # FROZEN shared types (already written — do not edit)
│   ├── ast_mutator.py               # AST mutation operators + bank selection
│   ├── prompt_perturbator.py        # prompt perturbation operators
│   ├── sandbox_runner.py            # isolated per-mutant pytest execution
│   ├── test_healer.py               # differential auto-healing of surviving mutants
│   ├── report.py                    # terminal (rich) + JSON + self-contained HTML reports
│   ├── trajectory_logger.py         # agent trajectory JSON logging
│   ├── target_config.py             # target adapter (syntrace_target.json)
│   └── run_mutation.py              # campaign orchestrator (written by integrator)
├── .github/workflows/ci.yml         # selftests + --fail-under gates on both targets
├── targets/
│   ├── humanize/                    # third-party validation target (vendored, MIT)
│   │   ├── syntrace_target.json     # adapter: package humanize, no prompts, excludes
│   │   ├── humanize/ · tests/ · LICENSE-humanize
│   └── sample_app/                  # demo target: AI ticket-triage pipeline
│       ├── pytest.ini               # [pytest] testpaths = tests
│       ├── app/
│       │   ├── __init__.py
│       │   ├── prompt_templates.py  # UPPER_SNAKE str constants (spec §5)
│       │   ├── llm_pipeline.py      # build_prompt, mock_llm, triage_ticket
│       │   ├── validators.py        # pydantic TriageResult, parse_strict/parse_lenient
│       │   ├── pricing.py           # boundary-rich billing math (healer-friendly)
│       │   └── scoring.py           # comparison-heavy scoring logic
│       └── tests/
│           ├── __init__.py          # empty
│           ├── test_pricing.py
│           ├── test_scoring.py
│           └── test_pipeline.py
├── dashboard/                       # optional Mission Control UI (FastAPI + SPA)
│   ├── server.py                    # landing at /, dashboard at /app, JSON API
│   ├── landing.html
│   └── index.html
├── selftests/                       # SyntraceAI's OWN unit tests (pytest selftests/)
│   └── conftest.py                  # sys.path bootstrap (already written)
├── trajectories/                    # real agent traces
├── reports/                         # generated reports (committed as evidence)
└── docs/ARCHITECTURE.md             # this file
```

## 3. Global rules

- Python 3.11+ (dev machine runs 3.14). Full type hints. Deterministic everywhere.
- Engine dependencies: ONLY `pytest`, `coverage`, `pydantic>=2`, `rich`. Stdlib
  otherwise. (The optional Mission Control dashboard under `dashboard/` additionally
  uses `fastapi` + `uvicorn`; the engine never imports them.)
- **No `random` without an explicit seed parameter (default `seed=1337`). No wall-clock
  dependence in any computed result.**
- Scripts in `baseline/` and `advanced/` start with this bootstrap so
  `python advanced/run_mutation.py` works from repo root:

```python
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
```

- Absolute imports only: `from advanced.core_types import Mutant`.
- Target adapter: a target directory may carry `syntrace_target.json`
  (`source_package`, `tests_dir`, `prompt_templates` or `null`, `exclude` list). Missing
  file ⇒ the demo layout below. Targets without a prompt module run code-only
  campaigns (38-mutant bank, no perturbations). Coverage is measured with
  `--source=<package>` and `--omit=<excludes>` so line coverage and mutation scope
  describe the same code. See `advanced/target_config.py`.
- Every module gets a selftest in `selftests/test_<module>.py` that runs standalone
  (create tiny fixture projects/sources inline or in tmp dirs — do NOT depend on
  `targets/sample_app` except where the contract says so).
- Bank composition is FROZEN: `CODE_BANK_SIZE = 38`, `PROMPT_BANK_SIZE = 12`, seed 1337.

## 4. Shared types — `advanced/core_types.py` (already written, read it)

Key types: `Outcome` (KILLED/SURVIVED/TIMEOUT/ERROR/NOT_RUN), `Mutant`, `Perturbation`,
`TestRunResult`, `MutantResult`, `HealedTest`, `CampaignResult`, plus `repo_root()`.
`Outcome.detected` property: KILLED, TIMEOUT, ERROR ⇒ detected (loud failure);
SURVIVED ⇒ undetected.

## 5. Target app spec — `targets/sample_app` (owner: sample-app agent)

A deterministic AI support-ticket triage pipeline. **No network, no real LLM** — a
rule-based `mock_llm` that is a pure function of the prompt string, so prompt
perturbations produce realistic degraded LLM behavior at $0 cost.

### 5.1 `app/prompt_templates.py` — EXACT marker lines (perturbator keys off these)

Module-level string constants ONLY (plain `NAME = "..."` assignments, no f-strings):

- `SYSTEM_PROMPT` MUST contain these exact lines (verbatim, each on its own line):
  - `You are TriageBot, a senior support-ticket triage analyst.`
  - `Respond ONLY with a single valid JSON object and nothing else.`
  - `Required JSON keys: "category", "priority", "confidence", "summary".`
  - `Priority must be an integer from 1 (lowest) to 5 (critical).`
  - `Confidence must be a number between 0.0 and 1.0.`
- `FEW_SHOT_BLOCK`: 2 worked examples (ticket → JSON), plain string; MUST contain the
  substring `Example` (rule 12 keys off it).
- `TICKET_TEMPLATE` MUST contain the section markers `### ROLE ###`, `### EXAMPLES ###`,
  `### TICKET ###`, `### OUTPUT RULES ###` and placeholders `{system}`, `{few_shot}`,
  `{ticket}`. The OUTPUT RULES section restates the JSON-only directive.

### 5.2 `app/llm_pipeline.py`

```python
def build_prompt(ticket_text: str) -> str            # TICKET_TEMPLATE.format(...)
def mock_llm(prompt: str) -> str                     # pure, deterministic — rules below
def triage_ticket(ticket_text: str, *, strict: bool = False) -> dict
```

`mock_llm` rules (implement EXACTLY — perturbations depend on them):
1. Parse required key names from the `Required JSON keys:` line (regex quoted names).
   If that line is missing/corrupted → hallucination fallback keys
   `["category", "priority", "summary", "extra_thoughts"]`.
2. Ticket text = content between `### TICKET ###` and the next `### ` marker; if the
   marker is missing → ticket = `""`.
3. category from ticket (lowercased) keyword rules: refund/charge/billing→`billing`;
   crash/error/bug/broken→`bug`; password/login/2fa→`account`; slow/latency/timeout→
   `performance`; else `general`.
4. priority base: billing 3, bug 4, account 3, performance 2, general 1; +1 if
   urgent/asap/immediately in ticket (cap 5).
5. confidence = min(0.95, round(0.6 + 0.05 * len(category), 2)).
6. summary = first 8 whitespace words of ticket joined, or `"(no ticket text)"`.
7. Build output dict over the required keys: known names get matching values;
   unknown/renamed names get `"hallucinated:<name>"` string values.
8. If `Priority must be an integer` line absent → priority emitted as the word
   `"critical"/"high"/"medium"/"low"` (5-4/3/2/1) instead of an int.
9. If `You are TriageBot` line absent → drop `confidence` key, add
   `"note": "As an AI language model, I cannot be fully certain."`.
10. If `Respond ONLY with a single valid JSON object` absent from the prompt → wrap the
    JSON in prose: ``Sure! Here is the triage you asked for:\n```json\n<JSON>\n```\nLet me know if you need anything else!``
    Otherwise output the bare compact JSON only.
11. If the `Confidence must be a number` line absent → confidence emitted as an integer
    percentage (`int(round(confidence * 100))`) instead of a 0–1 float.
12. If the substring `Example` absent from the prompt (few-shot examples dropped) → the
    model forgets the `summary` field entirely.
13. If `### OUTPUT RULES ###` absent → trailing commentary is appended after the JSON
    body (before any rule-10 wrapping): `\n\nHope this helps! Reply if you need a deeper
    analysis.` — every contract line in the prompt has a real behavioral consequence.

`triage_ticket`: build prompt → mock_llm → `parse_strict` if strict else `parse_lenient`
→ merge `{"priority_score": scoring.priority_score(priority, confidence), "escalate":
scoring.escalation_required(...)}` → return dict.

### 5.3 `app/validators.py`

`ContractViolation(Exception)`; `REQUIRED_KEYS = ("category", "priority", "confidence",
"summary")`; pydantic model `TriageResult` (category in the 5 known values, priority int
1–5, confidence float 0–1, summary non-empty str);
`parse_strict(raw: str) -> dict` — `json.loads` directly (no salvage), validate via
model, raise `ContractViolation` on any failure;
`parse_lenient(raw: str) -> dict` — regex-salvage first `{...}` blob, fill missing keys
with defaults (`general`, 1, 0.0, `""`), best-effort type coercion, never raises.
The lenient path is a *deliberate real-world antipattern* the demo exposes.

### 5.4 `app/pricing.py` and `app/scoring.py` — healer-friendly logic

Top-level, pure, deterministic functions with scalar/str args and type hints,
boundary-rich (thresholds like 50/100/500, `>=` vs `>`, clamps, caps):

```python
# pricing.py
def apply_tier_discount(subtotal: float, tier: str) -> float
def compute_tax(amount: float, region: str) -> float
def apply_coupon(amount: float, coupon_pct: float) -> float      # cap at 40%
def final_total(subtotal: float, tier: str, region: str, coupon_pct: float = 0.0) -> float
# scoring.py
def priority_score(priority: int, confidence: float) -> float
def escalation_required(score: float, category: str) -> bool
def sla_hours(priority: int) -> int
def clamp(value: float, low: float, high: float) -> float
```

Use literal numeric constants in comparisons (the healer harvests them for boundary
input generation).

### 5.5 `tests/` — deliberately blind-spotted, but GREEN

The suite MUST pass 100% green and reach roughly **85–92% line coverage**, while being
weak at boundaries and contracts, e.g.: test discount at values away from thresholds
(both `>` and `>=` agree); assert `result is not None` or key presence instead of
values; never call `triage_ticket(strict=True)`; never assert `confidence`/`summary`
values; skip testing `apply_coupon` cap. This is the realistic "90% coverage, false
confidence" suite. After writing, RUN `python -m pytest` and
`python -m coverage run -m pytest && python -m coverage report` inside
`targets/sample_app` and report the real numbers.

## 6. `advanced/ast_mutator.py` (owner: mutator agent)

Mutation operator classes (each with class attr `name`), applied one-site-per-mutant
via `ast.NodeTransformer` + `ast.unparse`:

- `ArithmeticOperatorSwap` (+↔-, *↔/, //→/, %→*)
- `ComparisonOperatorSwap` (==↔!=, <↔<=, >↔>=)
- `BooleanOperatorSwap` (and↔or)
- `ConditionNegation` (`if c:` → `if not c:`)
- `ConstantMutation` (int/float n→n+1, 0→1; True↔False; skip docstrings)
- `BoundaryValueMutation` (numeric constant inside a Compare → ±1)
- `ReturnValueMutation` (`return expr` → `return None`; skip when already None)

API:
```python
def enumerate_mutants(target_dir: Path, *, exclude: tuple[str, ...] = ("app/prompt_templates.py",)) -> list[Mutant]
def select_bank(mutants: list[Mutant], size: int = 38, seed: int = 1337) -> list[Mutant]
```
- Scan `app/*.py` under target_dir (skip excluded, skip `__init__.py`, skip `tests/`).
- Deterministic ordering: (file path, line, col, operator name). IDs `M001…` assigned
  AFTER selection, in selection order.
- Every mutant MUST pass `compile(mutated_source, path, "exec")` — discard ones that
  don't (AST validation sandboxing).
- Equivalent-mutant exclusions: an ordering-equality comparison swap inside a clamp
  pattern (`if x > C: x = C` with no else, plain name, same non-zero constant, standard
  numeric semantics) is a semantic no-op and MUST be excluded from enumeration — an
  unkillable bug in the bank would misstate every downstream score. Likewise the
  `TYPE_CHECKING` idiom: `if TYPE_CHECKING:` guards are never negated and the constant
  in `TYPE_CHECKING = False` is never mutated (import-only blocks at runtime).
- `select_bank`: seeded round-robin stratified across (file, operator) so the bank
  spans all files and operator types; deterministic for a given seed.
- Record `function_name` (enclosing function qualname or `""`).

## 7. `advanced/prompt_perturbator.py` (owner: perturbator agent)

```python
def enumerate_perturbations(target_dir: Path) -> list[Perturbation]   # exactly 12
def replace_constant(module_source: str, name: str, new_value: str) -> str  # AST-based
```

The 12 perturbations (IDs `P001…P012` in this order), each producing a full mutated
`app/prompt_templates.py` source via `replace_constant`:
1. `RoleStripping` — remove the `You are TriageBot…` line from SYSTEM_PROMPT.
2. `JsonOnlyDirectiveRemoval` — remove the `Respond ONLY…` line from SYSTEM_PROMPT.
3. `InstructionNegation` — replace `Respond ONLY with a single valid JSON object and
   nothing else.` with `You may include helpful commentary around the JSON object.`
4. `SchemaKeyRename(confidence→certainty)` in the Required-keys line.
5. `SchemaKeyRename(summary→synopsis)`.
6. `SchemaKeyRename(category→topic)`.
7. `TypeRuleRemoval` — remove the `Priority must be an integer…` line.
8. `RangeRuleRemoval` — remove the `Confidence must be a number…` line.
9. `SectionMarkerCorruption` — `### TICKET ###` → `@@@ TICKET @@@` in TICKET_TEMPLATE.
10. `SectionMarkerCorruption` — remove `### OUTPUT RULES ###` section from
    TICKET_TEMPLATE (keep placeholders `{system}`, `{few_shot}`, `{ticket}` intact!).
11. `WhitespaceNoise` — insert zero-width space `​` inside `Required JSON keys`
    (between "JSON" and "keys") in SYSTEM_PROMPT.
12. `FewShotDrop` — FEW_SHOT_BLOCK → `""`.

Rules: operate on the ORIGINAL constants read from the target's real
`prompt_templates.py` (parse module AST, extract constant values). A perturbation must
never remove a `{placeholder}` needed by `.format` (that would crash, not degrade).
`replace_constant` parses the module, swaps the matching `Assign` value with a new
`ast.Constant`, unparses.

## 8. `advanced/sandbox_runner.py` (owner: sandbox agent)

```python
def run_suite(project_dir: Path, *, timeout_s: float = 120.0) -> TestRunResult
def evaluate_patch(target_dir: Path, rel_path: str, patched_source: str, *, timeout_s: float = 60.0) -> TestRunResult
def evaluate_many(target_dir: Path, items: list[tuple[str, str, str]], *, jobs: int | None = None, timeout_s: float = 60.0, on_result: Callable[[str, TestRunResult], None] | None = None) -> dict[str, TestRunResult]
```
- `evaluate_patch`: copy target to fresh tmp dir (`shutil.copytree`, ignore
  `__pycache__`, `.pytest_cache`), overwrite `rel_path` with `patched_source`, run
  `[sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"]`
  with `cwd=tmpdir`, env: inherit + `PYTHONDONTWRITEBYTECODE=1`, kill at timeout,
  always clean up tmpdir.
- Outcome mapping: exit 0 → SURVIVED; exit 1 → KILLED; timeout → TIMEOUT; any other
  exit → ERROR. Parse failed test ids from pytest output (`FAILED path::name` lines,
  best-effort). `stdout_tail` = last 2000 chars combined stdout+stderr.
- `evaluate_many`: items are `(item_id, rel_path, patched_source)`;
  ThreadPoolExecutor (subprocess-bound), `jobs` default `min(8, os.cpu_count())`;
  results keyed by item_id; call `on_result` as each completes (for progress display).

## 9. `advanced/test_healer.py` (owner: healer agent)

Differential auto-healing via **subprocess probes** (no import-cache games):

```python
def heal_survivors(target_dir: Path, survivors: list[Mutant], *, seed: int = 1337, max_inputs: int = 2000) -> tuple[list[HealedTest], list[str]]
def build_prompt_contract_tests(target_dir: Path, surviving: list[Perturbation]) -> list[HealedTest]
def write_healed_test_file(target_dir: Path, healed: list[HealedTest], *, tests_dir: str = "tests") -> Path
```

- For each surviving CODE mutant with a non-empty `function_name` referring to a
  top-level function in the mutated file: generate candidate inputs from type hints —
  int pool `[-3, -1, 0, 1, 2, 3, 7, 10, 49, 50, 51, 99, 100, 101, 499, 500, 501, 1000,
  2500, 5000, 10000]` (large magnitudes push computed intermediates across caps),
  float = same as floats, bool `[True, False]`, str = string constants harvested from
  the module AST plus fallbacks (`""`, `"zzz"`, a ten-word sentence, and canonical
  adversarial JSON contract payloads) — plus every numeric literal harvested from the
  function's AST and its ±1 neighbors. Cartesian product over params, deterministic
  order, capped at `max_inputs`.
- Cross-function input synthesis: str-param pools are additionally enriched by running
  the module's own top-level `str -> str` single-parameter functions (excluding the
  function under probe) over harvested base strings in the PRISTINE copy — composed
  inputs like fully rendered prompts reach code paths raw literals cannot. Synthesized
  inputs feed both probe sides identically; synthesis failure degrades to no
  enrichment, never to an error.
- Raises-mode healing may reference exceptions defined in the probed module itself
  (emitted as `<module>.<ExcName>`, only when the name is a real module attribute) in
  addition to builtins.
- Type hints are resolved to scalar member sets: plain names, string annotations,
  PEP 604 unions (`int | None`), and module-level aliases (`NumberOrString: TypeAlias
  = float | str`, including inside `TYPE_CHECKING` blocks). Containers/generics are
  refused honestly.
- Float pools additionally carry non-integral values and orders of magnitude (10⁶…10³³,
  2¹⁰…2⁴⁰); int pools deliberately do NOT (an int parameter may be a precision or
  repeat count — `f"{x:.{10**33}f}"` never returns).
- Candidate order: the all-defaults anchor tuple, then each parameter swept through its
  whole pool with the others held at their defaults, then the diagonal product — so far
  boundaries are reached even when the full product dwarfs `max_inputs`.
- Module-level mutants (constant tables, flags) are healed by probing every top-level
  function of the module in definition order; the first re-verified discriminator wins
  and the emitted test calls that observing function.
- Probe protocol: write pristine copy and mutated copy of the target to two tmp dirs;
  in each, run a generated probe script that imports the function, applies the JSON
  list of candidate inputs, and prints JSON results (exceptions encoded
  `{"__exc__": "TypeName"}`). Diff the two result lists; first discriminating input
  where the ORIGINAL does not raise wins.
- Emit a pytest test asserting the ORIGINAL behavior (use `pytest.approx` for floats;
  `pytest.raises` if original raises while mutant doesn't). Test name
  `test_healed_<mutant_id>_<function_name>`; header comment names the mutant id,
  operator, and location it kills.
- Return `(healed_tests, unhealable_mutant_ids)` — unhealable = no discriminating
  input found (likely-equivalent mutant) or unsupported signature. Honesty required:
  never fabricate a test without a verified discriminating input.
- `build_prompt_contract_tests`: emit strict-contract tests (independent of which
  perturbation survived): for a battery of 5 fixed tickets, call
  `triage_ticket(t, strict=True)` and assert exact key set, types, and ranges per §5.3.
  These kill prompt survivors on re-run. Name them `test_healed_prompt_contract_<n>`.
- `write_healed_test_file`: write all healed tests into
  `<target>/tests/test_healed_assertions.py` with a generated-by header (overwrites).

## 10. `advanced/report.py` (owner: sandbox agent)

```python
def render_terminal(campaign: CampaignResult) -> None          # rich tables/panels
def write_json(campaign: CampaignResult, path: Path) -> None
def write_html(campaign: CampaignResult, path: Path) -> None   # single self-contained file
```
Show: bank composition; per-operator kill/survive breakdown; survivor list with
locations + snippets; healed tests; baseline line coverage vs pre-heal detection vs
post-heal mutation score ("false confidence gap"); wall time. HTML: inline CSS only,
CSS-bar charts, dark-scheme friendly, no external assets.

## 11. `advanced/trajectory_logger.py` + `baseline/run_baseline.py` (owner: baseline agent)

`TrajectoryLogger(path: Path, task: str, agent: str)` — `.log_step(instruction, action,
*, command=None, target=None, tool_output=None, human_checkpoint=None)` auto-increments
`step_index`, ISO-8601 UTC timestamps, `.save()` writes JSON matching the schema of
`trajectories/agent_trace_01.json`.

`run_baseline.py` (CLI: `--target targets/sample_app`, `--jobs`, `--seed 1337`,
`--json reports/baseline_report.json`):
1. Copy target to tmp, run `coverage run -m pytest` + `coverage json` there; report
   total line coverage % and test count (suite must be green — abort loudly if not).
2. Build the SAME frozen 50-bug bank (via `advanced.ast_mutator` +
   `advanced.prompt_perturbator` — the bank is the shared benchmark harness).
3. `evaluate_many` with the ORIGINAL suite; report detected/total, per-kind breakdown.
4. Print the false-confidence gap (e.g. "91% line coverage, 42% bug detection") via
   rich; write JSON report. Exit 0.

## 12. `advanced/run_mutation.py` + `main.py` (owner: integrator — do not write)

Orchestrates: clean-suite gate → bank build → parallel evaluation → heal → re-run
survivors → reports + trajectory. Reads every API above exactly as specified.

## 13. Determinism & honesty invariants (verify phase enforces these)

- Same seed ⇒ byte-identical bank and identical scores, any machine, any `--jobs`.
- Every number printed in README/REPRODUCTION_GUIDE comes from a real captured run.
- Unhealable/equivalent mutants are reported as such, never hidden.
- Total campaign wall time on an 8-core laptop target: under ~90s.

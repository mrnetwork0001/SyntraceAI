# 🧪 SyntraceAI — Autonomous Agentic Mutation Testing & Hallucination Stress-Tester

> Built for the **micro1 Frontier Engineering Challenge 2026**
> **License:** Apache 2.0 · **Author:** Ifeanyichukwu Onwo (`mrnetwork`)

SyntraceAI is an adversarial chaos agent for AI applications. It injects a deterministic
bank of **50 bugs** — 38 AST code mutations and 12 prompt perturbations — into a target
codebase, runs the target's own test suite against every bug in an isolated sandbox, and
measures what the suite *actually catches*. Bugs that survive are **auto-healed**:
differential input search synthesizes hardened assertion tests that pin correct behavior,
and the campaign re-runs to prove they kill.

**Every number in this README comes from a real, seeded, reproducible run** (~8 seconds,
$0 in API cost — see [REPRODUCTION_GUIDE.md](REPRODUCTION_GUIDE.md)).

---

## Intended user

AI engineers, software architects, and AI data labs (like micro1) shipping LLM
applications and agentic pipelines — anyone whose CI says "green, 90% coverage" and who
still gets paged for a bug the suite never touched.

## The bottleneck

Standard coverage tools (`coverage.py`, Jest) measure **lines executed**, not **behavior
defended**. A suite can visit 90% of the code while asserting almost nothing about it —
and prompt regressions (a lost instruction line, a renamed schema key, dropped few-shot
examples) are invisible to line coverage by construction.

Measured on this repo's demo target:

| | Baseline (`coverage.py` mindset) | Advanced (SyntraceAI) |
| :--- | :--- | :--- |
| Line coverage | **87.1%** — looks healthy | 87.1% — same suite, same code |
| Injected bugs detected | **24/50 (48.0%)** | **49/50 (98.0%)** after auto-healing |
| Prompt-drift detection | invisible | 12 perturbation attacks, all scored |
| Fixing the gaps | manual | **24 auto-healed assertion tests**, verified by re-run |

That 39-point false-confidence gap — 87% coverage vs 48% detection — is the number line
coverage was hiding.

## How it works

1. **Inject** — 7 AST operator families (arithmetic/comparison/boolean swaps, condition
   negation, constant & boundary shifts, return-value replacement) plus 12 prompt
   perturbations (role stripping, JSON-only directive removal, schema key renames,
   zero-width whitespace, few-shot drop…). Every mutant is compile-validated; comparison
   swaps inside clamp patterns are **proven equivalent and excluded** so the bank never
   contains an unkillable bug.
2. **Isolate** — each bug runs against the suite in its own sandboxed project copy,
   in parallel, timeout-guarded. Exit codes map to killed / survived / timeout / error;
   loud failures count as detections, silence never does.
3. **Score** — killed vs survived over the frozen 50-bug bank = the mutation score.
4. **Heal** — for each survivor, subprocess probes evaluate original vs mutant over
   boundary-aware generated inputs (type-hint pools, harvested literals ±1, pairwise
   string concatenations, and **cross-function synthesis**: the module's own
   `str -> str` functions compose realistic inputs like fully rendered prompts). Only a
   **re-verified discriminating input** becomes a test. Survivors with no such input are
   reported as likely-equivalent — never hidden. The demo campaign ends at 49/50 with
   exactly one such survivor, and manual analysis confirms it is a true equivalent
   mutant (a boundary guard whose fall-through computes the identical value).

## The demo target

`targets/sample_app` is a deterministic AI ticket-triage pipeline: prompt templates, a
rule-based **mock LLM that reads its own instructions** (each contract line in the
prompt has a real behavioral consequence — remove the JSON-only directive and it wraps
output in chatty prose; drop the few-shot examples and it forgets the `summary` field),
pydantic contract validation with a deliberately lenient salvage path, and
boundary-rich pricing/scoring logic. Its 48-test suite is green with 87.1% coverage and
realistic blind spots — the suite a rushed team actually ships.

## Quickstart

```bash
pip install -r requirements.txt
python main.py full          # baseline audit, then the full campaign (~8s)
python dashboard/server.py   # Mission Control UI at http://127.0.0.1:8377
```

Outputs: rich terminal report, `reports/mutation_report.json`,
self-contained `reports/mutation_report.html`, and an agent trajectory in
`trajectories/`. The dashboard landing page and scoreboard read the same reports —
nothing rendered anywhere is a mock number.

## Main failure modes

- **Over-mutation breaking the runner:** every mutant must pass `compile()` before it
  reaches a sandbox (AST validation sandboxing); runaway mutants (infinite loops) are
  killed by per-run timeouts and counted as loudly detected.
- **Equivalent mutants inflating the denominator:** provable cases (clamp-boundary
  comparison swaps) are excluded up front; unprovable ones are surfaced explicitly as
  unhealable rather than silently capping the score.
- **Fabricated healing:** the healer's honesty gate re-verifies every discriminating
  input in both project copies before a test is emitted; no verified input, no test.

## Hot take

Line coverage is a vanity metric. A 90%-coverage suite that catches 48% of injected
bugs is not a safety net — it is a green dashboard over an open manhole. Mutation
score, measured adversarially and healed automatically, is the metric that should gate
AI-application CI.

## Repository map

```
main.py                  CLI hub (baseline | mutate | full)
baseline/                coverage.py audit + bug-bank detection with the original suite
advanced/                mutation engine: ast_mutator, prompt_perturbator,
                         sandbox_runner, test_healer, report, trajectory_logger,
                         run_mutation (orchestrator), core_types (shared contract)
targets/sample_app/      demo AI triage pipeline + deliberately blind-spotted suite
dashboard/               FastAPI Mission Control (landing page + live dashboard)
selftests/               the engine's own test suite (79 tests)
docs/ARCHITECTURE.md     frozen module contract the engine is built against
trajectories/            agent execution traces (real runs)
reports/                 generated JSON/HTML reports (committed as evidence)
```

## License

Apache 2.0 — see [LICENSE](LICENSE).

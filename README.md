# 🧪 SyntraceAI - Autonomous Agentic Mutation Testing & Hallucination Stress-Tester

> Built for the **micro1 Frontier Engineering Challenge 2026**
> **License:** Apache 2.0 · **Author:** Ifeanyichukwu Onwo (`mrnetwork`)

SyntraceAI is an adversarial chaos agent for AI applications. It injects a deterministic
bank of **50 bugs** - 38 AST code mutations and 12 prompt perturbations - into a target
codebase, runs the target's own test suite against every bug in an isolated sandbox, and
measures what the suite *actually catches*. Bugs that survive are **auto-healed**:
differential input search synthesizes hardened assertion tests that pin correct behavior,
and the campaign re-runs to prove they kill.

**Every number in this README comes from a real, seeded, reproducible run** (~8 seconds,
$0 in API cost - see [REPRODUCTION_GUIDE.md](REPRODUCTION_GUIDE.md)).

---

## Intended user

AI engineers, software architects, and AI data labs (like micro1) shipping LLM
applications and agentic pipelines - anyone whose CI says "green, 90% coverage" and who
still gets paged for a bug the suite never touched.

## The bottleneck

Standard coverage tools (`coverage.py`, Jest) measure **lines executed**, not **behavior
defended**. A suite can visit 90% of the code while asserting almost nothing about it -
and prompt regressions (a lost instruction line, a renamed schema key, dropped few-shot
examples) are invisible to line coverage by construction.

Measured on this repo's demo target:

| | Baseline (`coverage.py` mindset) | Advanced (SyntraceAI) |
| :--- | :--- | :--- |
| Line coverage | **87.1%** - looks healthy | 87.1% - same suite, same code |
| AST code mutants detected | **18/38 (47.4%)** | **37/38 (97.4%)** after auto-healing |
| Prompt perturbations detected | **6/12** (invisible to coverage tooling) | **12/12** via strict-contract hardening |
| Overall injected bugs | **24/50 (48.0%)** | **49/50 (98.0%)** |
| Fixing the gaps | manual | **24 auto-healed assertion tests**, verified by re-run |

That 39-point false-confidence gap - 87% coverage vs 48% detection - is the number line
coverage was hiding. The **diagnosis** number is the 48% pre-heal detection; the 98% is
what the suite looks like after SyntraceAI repairs it.

## How it works

1. **Inject** - 7 AST operator families (arithmetic/comparison/boolean swaps, condition
   negation, constant & boundary shifts, return-value replacement) plus 12 prompt
   perturbations (role stripping, JSON-only directive removal, schema key renames,
   zero-width whitespace, few-shot drop…). Every mutant is compile-validated, and
   comparison swaps inside clamp patterns are **excluded as equivalent** (under
   documented numeric assumptions) - an unkillable bug in the bank would misstate the
   score. Equivalents the prover can't catch statically still surface honestly as
   unhealable survivors (the shipped run has exactly one, M010).
2. **Isolate** - each bug runs against the suite in its own sandboxed project copy,
   in parallel, timeout-guarded. Exit codes map to killed / survived / timeout / error;
   loud failures count as detections, silence never does.
3. **Score** - killed vs survived over the frozen 50-bug bank = the mutation score.
4. **Heal** - for each survivor, subprocess probes evaluate original vs mutant over
   boundary-aware generated inputs (type-hint pools, harvested literals ±1, pairwise
   string concatenations, and **cross-function synthesis**: the module's own
   `str -> str` functions compose realistic inputs like fully rendered prompts). Only a
   **re-verified discriminating input** becomes a test. Survivors with no such input are
   reported as likely-equivalent - never hidden. The demo campaign ends at 49/50 with
   exactly one such survivor, and manual analysis confirms it is a true equivalent
   mutant (a boundary guard whose fall-through computes the identical value).

## Third-party validation: humanize 4.16.0

A benchmark on a self-authored target proves the harness works; it doesn't prove the
tool finds anything on code we didn't write. So the same engine, unchanged, runs against
[humanize](https://github.com/python-humanize/humanize) 4.16.0 - a mature, MIT-licensed
library vendored verbatim in `targets/humanize/` together with the dependency-free part
of its own test suite (311 of its 784 tests - the `freezegun`/`pytest-benchmark`
modules are omitted and the code they cover is excluded from mutation) and declared
through a one-file target adapter (`syntrace_target.json`).

| humanize 4.16.0 (measured) | Frozen 38-mutant bank | Exhaustive: all 253 sites |
| :--- | :--- | :--- |
| Line coverage (mutated modules) | **98.1%** | 98.1% |
| Detected by humanize's own suite | **36/38 (94.7%)** | **218/253 (86.2%)** |
| Auto-healed tests generated | 0 needed | **12** |
| After healing | 94.7% - both survivors verified equivalent | **230/253 (90.9%)** |
| Wall time | 4.0s | 24.2s |

Two things this shows. First, SyntraceAI does **not manufacture findings** on a
well-tested library: at the standard bank depth humanize catches 94.7%, and the two
survivors are sign comparisons (`value < 0`, `value > 0`) guarded by
`math.isinf(value)` - only ±inf reaches them, so they are provably unobservable.
Second, at exhaustive depth its 98%-coverage suite still misses 35
behavior changes, and the healer wrote 12 tests humanize's own suite lacks - pinning
the unit-scaling thresholds nobody had asserted:

```python
assert humanize.number.intword(1e+27, '%.1f') == '1.0 octillion'
assert humanize.number.intword(1e+18, '%.1f') == '1.0 quintillion'
assert humanize.filesize.naturalsize(1e+33, False, False, '%.1f') == '1000.0 QB'
assert humanize.number.metric(1e+33, '', 3) == '1.00 x 10³³'
```

Every one was found by differential probing (default-anchored parameter sweeps over
orders-of-magnitude pools, module-level constants observed through the module's public
API) and re-verified before being emitted. The 23 remaining survivors are reported, not
hidden: the `isinf`-guarded family, a mutant only reachable through a `list[Any]`
signature the healer refuses, one `intword` power-table entry no probed magnitude
reaches, and threshold/format arithmetic inside `metric`, `intcomma`, `intword`,
`ordinal` (gettext context) and `fractional` (`limit_denominator`) that the current
input pools cannot discriminate.

## The demo target

`targets/sample_app` is a deterministic AI ticket-triage pipeline: prompt templates, a
rule-based **mock LLM that reads its own instructions** (each contract line in the
prompt has a real behavioral consequence - remove the JSON-only directive and it wraps
output in chatty prose; drop the few-shot examples and it forgets the `summary` field),
pydantic contract validation with a deliberately lenient salvage path, and
boundary-rich pricing/scoring logic. Its 48-test suite is green with 87.1% coverage and
realistic blind spots - the suite a rushed team actually ships.

## The value

One command turns "we have 87% coverage" into "our tests catch 48% of real behavior
changes, here are the 26 bugs that slipped through, and here are 24 generated tests -
each proven to kill a specific one." The diagnosis, the evidence, and the repair ship
together, deterministically, in seconds, for $0.

## Quickstart

```bash
pip install -r requirements.txt
python main.py full          # baseline audit (~3s) + full campaign (~8s) on the demo target
python advanced/run_mutation.py --target targets/humanize \
    --json reports/humanize_mutation_report.json \
    --trajectory trajectories/agent_trace_03.json     # third-party target, frozen bank (~4s)
python advanced/run_mutation.py --target targets/humanize --max-code-mutants 400 \
    --json reports/humanize_full_mutation_report.json \
    --trajectory trajectories/agent_trace_04.json     # exhaustive: writes the 12 healed tests (~25s)
python dashboard/server.py   # Mission Control UI at http://127.0.0.1:8377
```

`python advanced/run_mutation.py --fail-under 95` turns the campaign into a CI gate -
see [.github/workflows/ci.yml](.github/workflows/ci.yml), which runs the selftests and
all three campaigns on every push to `main` and every pull request. The HTML report and
trajectory paths follow `--json` unless set explicitly, so a second target's run never
overwrites the demo's committed artifacts.

Outputs: rich terminal report, `reports/mutation_report.json`,
self-contained `reports/mutation_report.html`, and an agent trajectory in
`trajectories/`. The dashboard landing page and scoreboard read the same reports -
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

## Threats to validity & limitations

Stated up front, because a benchmark you can't interrogate is a benchmark you can't
trust:

- **The demo target is self-authored, and the mock LLM's degradation rules are authored
  couplings.** The mock keys off the same contract lines the perturbator attacks - by
  design: it *simulates documented real-LLM failure modes* (prose-wrapped JSON, dropped
  fields, hallucinated keys) deterministically at $0, so the prompt-side numbers
  validate the harness mechanics, not model behavior. The load-bearing, least-gameable
  numbers are the **code-mutation rows (18/38 → 37/38)**: plain AST mutations against a
  plain pytest suite.
- **Post-heal detection is high by construction** - every healed test is synthesized
  from an input already verified to discriminate its mutant, so the re-run kill is
  expected. The informative diagnosis is the 48% pre-heal detection; the post-heal
  score measures that the healer's output is real and complete, not that healing is
  hard.
- **Loud breakage counts as detection.** Timeouts and collection errors map to
  "detected" (the suite failed loudly). In the shipped run this channel is unexercised:
  all 49 detections are genuine assertion failures (0 timeouts, 0 errors - check
  `reports/mutation_report.json`).
- **Generality limits.** A target is declared by a one-file adapter
  (`syntrace_target.json`: source package, tests dir, optional prompt module, excludes)
  and the code half of the engine ran unchanged on humanize. Remaining limits: the
  healer probes top-level functions with scalar, union, or alias-resolvable hints
  (`int/float/bool/str/None`, `float | str`, `NumberOrString`) and refuses containers;
  the prompt half requires the documented template contract (`docs/ARCHITECTURE.md`
  §5.1); the equivalence provers cover clamp swaps and `TYPE_CHECKING` guards under
  documented assumptions. Everything else stays in the bank and is judged empirically.

## Hot take

Line coverage is a vanity metric. A 90%-coverage suite that catches 48% of injected
bugs is not a safety net - it is a green dashboard over an open manhole. Mutation
score, measured adversarially and healed automatically, is the metric that should gate
AI-application CI.

## Repository map

```
main.py                  CLI hub (baseline | mutate | full)
baseline/                coverage.py audit + bug-bank detection with the original suite
advanced/                mutation engine: ast_mutator, prompt_perturbator,
                         sandbox_runner, test_healer, report, trajectory_logger,
                         target_config (adapter), run_mutation (orchestrator),
                         core_types (shared contract)
targets/sample_app/      demo AI triage pipeline + deliberately blind-spotted suite
targets/humanize/        third-party validation target (humanize 4.16.0, vendored)
dashboard/               FastAPI Mission Control (landing page + live dashboard)
.github/workflows/       CI: selftests + mutation-score gates on both targets
selftests/               the engine's own test suite (106 tests)
docs/ARCHITECTURE.md     frozen module contract the engine is built against
trajectories/            agent execution traces (real runs)
reports/                 generated JSON/HTML reports (committed as evidence)
```

## License

Apache 2.0 - see [LICENSE](LICENSE).

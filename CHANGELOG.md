# 📜 SyntraceAI - Agentic Improvement Changelog

Real iterations from the build, each guided by measured agent feedback. All numbers are
from actual seeded runs (seed 1337): the frozen 50-bug bank for the demo target, and
the 38-mutant code bank plus an exhaustive 253-site campaign for humanize.

### Iteration 1 - Baseline implementation & the false-confidence measurement
- **Goal:** Quantify what standard line coverage hides.
- **Agent instruction:** "Run coverage.py over the target suite, then audit the same
  suite against the shared 50-bug bank in isolated sandboxes."
- **Result:** 87.1% line coverage, but only **24/50 (48.0%)** of injected bugs detected -
  a 39-point false-confidence gap.

### Iteration 2 - Six-module engine built in parallel against a frozen contract
- **Goal:** Full campaign: AST mutation + prompt perturbation + sandboxed evaluation +
  differential auto-healing + reporting, built by six agents against
  `docs/ARCHITECTURE.md`.
- **Agent instruction:** "Implement your module set exactly per the frozen contract;
  run and pass your own selftests before reporting."
- **Result:** First end-to-end campaign scored **39/50 (78.0%)** post-heal with 15
  healed tests - 8 code mutants unhealable, 3 prompt perturbations behaviorally inert.
  (Intermediate measurement from the build session, superseded before the first engine
  commit; the committed reports carry only the final numbers.)

### Iteration 3 - Equivalence proofs, healer input synthesis, behavioral prompt rules
- **Goal:** Close the gap honestly: no bank padding, no hidden survivors.
- **Agent feedback acted on:**
  - Two "unhealable" mutants were *provably equivalent* clamp-boundary comparison
    swaps → the mutator now proves and excludes that pattern from enumeration.
  - Six were healer input-pool misses → added cross-function input synthesis (the
    module's own `str -> str` functions compose realistic inputs like rendered
    prompts), pairwise string concatenation, non-integral floats, large-magnitude
    values, adversarial JSON contract payloads, and target-module exception support.
  - Three prompt perturbations had no behavioral consequence in the mock LLM → rules
    11–13 give every prompt contract line a real degradation (percentage confidence,
    forgotten summary field, trailing commentary).
  - Baseline audited the healed suite by accident and used a different coverage basis
    → it now strips generated healed tests first and measures app-only coverage,
    identical to the campaign's basis.
- **Result:** **49/50 (98.0%)** final mutation score, **24 auto-healed assertion
  tests**, one surviving mutant reported as likely-equivalent (confirmed a true
  equivalent by manual analysis). Campaign wall time ~8–10s (7.9s idle, 10.2s in the
  committed report). Determinism verified: repeated runs produce identical results.

### Iteration 4 - Third-party validation, target adapter, CI gate
- **Goal:** Answer the strongest critique from an adversarial review pass - "the demo
  target is self-authored" - with evidence on code we did not write.
- **Agent feedback acted on:**
  - Replaced the hard-coded `app/` layout with a one-file target adapter
    (`syntrace_target.json`) and vendored humanize 4.16.0 with its dependency-free
    test modules as `targets/humanize`.
  - First humanize run (before the `TYPE_CHECKING` exclusions reshaped the bank):
    35/38 (92.1%) detection, **0 heals** - every survivor was either a true
    equivalent or out of the healer's reach. Diagnosis showed 25 probed-without-
    discriminator sites and 6 module-level sites (humanize's `intword` power table).
  - Healer upgrades: union/alias type-hint resolution (`NumberOrString = float | str`),
    default-anchored single-parameter sweeps before the diagonal product, module-level
    mutants healed by observing them through the module's public API, and
    orders-of-magnitude pools. Lesson learned the hard way: magnitudes must stay out of
    `int` pools - `precision=10**33` fed to an f-string never returns; probes timed out
    and the exhaustive run crawled past ten minutes until the pools were split by type.
  - Mutator: `TYPE_CHECKING` guards and flags are recognized as unkillable no-ops.
  - Mutation and baseline reports are byte-stable across runs (per-bug timing fields
    stripped); CI workflow gates both targets with `--fail-under`; sibling HTML and
    trajectory paths follow `--json` so a second target never overwrites the demo's
    committed artifacts.
- **Result:** humanize standard bank **36/38 (94.7%)**, both survivors verified
  equivalent; exhaustive campaign **218/253 → 230/253 (86.2% → 90.9%)** with **12
  auto-healed tests** humanize's own suite lacks. Demo target unchanged at 98.0%.
  Selftests: 106.

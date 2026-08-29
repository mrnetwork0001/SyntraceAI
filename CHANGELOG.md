# 📜 SyntraceAI — Agentic Improvement Changelog

Real iterations from the build, each guided by measured agent feedback. All numbers are
from actual runs on the frozen 50-bug bank (seed 1337).

### Iteration 1 — Baseline implementation & the false-confidence measurement
- **Goal:** Quantify what standard line coverage hides.
- **Agent instruction:** "Run coverage.py over the target suite, then audit the same
  suite against the shared 50-bug bank in isolated sandboxes."
- **Result:** 87.1% line coverage, but only **24/50 (48.0%)** of injected bugs detected —
  a 39-point false-confidence gap.

### Iteration 2 — Six-module engine built in parallel against a frozen contract
- **Goal:** Full campaign: AST mutation + prompt perturbation + sandboxed evaluation +
  differential auto-healing + reporting, built by six agents against
  `docs/ARCHITECTURE.md`.
- **Agent instruction:** "Implement your module set exactly per the frozen contract;
  run and pass your own selftests before reporting."
- **Result:** First end-to-end campaign scored **39/50 (78.0%)** post-heal with 15
  healed tests — 8 code mutants unhealable, 3 prompt perturbations behaviorally inert.

### Iteration 3 — Equivalence proofs, healer input synthesis, behavioral prompt rules
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
  equivalent by manual analysis). Campaign wall time 7.9s. Determinism verified:
  repeated runs produce identical results.

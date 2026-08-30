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

### Iteration 5 - Adversarial code review with executed repros
- **Goal:** Harden the new adapter and healer paths against everything a second
  verification fleet could actually break, not just what it could argue about.
- **Agent feedback acted on (each reproduced by a script before it was fixed):**
  - A `..` in the adapter's `source_package` escaped the target and turned harness
    errors into a silent 100% score; a `..` in `tests_dir` deleted a file outside the
    target. The adapter now rejects any path that leaves the target, validates that
    every `exclude` entry exists (a typo silently excluded nothing), supports directory
    excludes, and both entrypoints refuse a healed-test path outside the target.
  - The healer's honesty gate re-verified with one subprocess, so hash-order-dependent
    output (joining a set) could yield a flaky test ~half the time. Verification now
    runs under two pinned `PYTHONHASHSEED` values and refuses non-finite inputs.
  - `TYPE_CHECKING` bodies and aliased guards still produced unkillable mutants; the
    mutator now skips everything inside a type-checking block.
  - Healed tests written to a `tests_dir` pytest never collects passed the gate
    trivially; the orchestrator now checks the collected count and aborts loudly.
  - Dashboard paired the exhaustive humanize set with the frozen-bank baseline; report
    sets are matched exactly and unknown ids return 404.
- **Result:** all measured numbers unchanged; selftests 115.

### Iteration 6 - Make it usable on the user's own project
- **Goal:** The engine already ran on any Python project, but only the CLI could reach
  that: the dashboard's buttons were hard-wired to the bundled demo, and nothing in the
  UI explained what the tool does or what to press first.
- **Changes:**
  - Report paths derive from the target (`reports/<project>_mutation_report.json`), so a
    user's run can never overwrite the demo's committed evidence. The demo keeps the
    unprefixed names.
  - The dashboard takes a **project path**: a `Run against` field plus one-click presets,
    passed to the CLI as `--target`. Each project gets its own report set in the
    selector, and the run auto-selects it when it finishes.
  - A dismissible **"How this works"** panel (01-04), a `?` button to reopen it, an empty
    state that names the button to press, plain-language metric labels ("bugs your tests
    caught" rather than "pre-heal detection"), and hover explanations on every tile.
  - README gained a **"Use it on your own project"** section with the one-file adapter.
- **Result:** verified end to end against projects outside the repo. Demo and humanize
  numbers unchanged; selftests 122.

### Iteration 7 - Adversarial pass on the "your own project" flow
- **Goal:** A three-agent fleet drove the new flow the way a judge would. It found that
  the headline feature was broken for the most common layout, and that two of the three
  run buttons refused to run at all.
- **Fixed (each reproduced before and after):**
  - A plain `app/` + `tests/` project with no prompt module crashed with a
    `PromptContractError` traceback - the exact layout the UI says needs no config. The
    prompt module is now auto-detected: absent means a code-only campaign.
  - `Run Baseline Audit` and `Run Full` aborted on any project that could not yield
    exactly 38 mutation sites. The frozen bank is now enforced only for the demo, whose
    committed evidence depends on it; other projects are audited at their real size.
  - Report identity keyed on the directory *name*, so a project merely named
    `sample_app` - or any name with no ASCII alphanumerics - silently overwrote the
    committed demo evidence, and two projects sharing a name overwrote each other.
    Identity is now the resolved path: bundled targets keep their documented names,
    every other project gets its name plus a digest of its path.
  - A relative `--target` resolved against the SyntraceAI repo rather than the user's
    directory, so the documented `--target .` audited the wrong project.
  - Baseline-only runs produced a report the dashboard could never display; slugs longer
    than the route's limit were offered then rejected; `/api/presets` 500'd when
    `targets/` was absent; a non-project directory started a doomed run instead of
    failing fast.
- **Result:** all measured numbers unchanged; selftests 125, including regressions for
  every case above.

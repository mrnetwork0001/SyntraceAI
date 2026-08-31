# 🎬 SyntraceAI - 5-Minute Solution Video Script

> Screen-recording plan with timings. Everything shown on screen is a live run -
> no pre-baked numbers. Covers the seven required beats: problem, baseline, one
> realistic execution, final comparison, changelog, the change that contributed
> most, and one removed experiment. Speech budget ~700 words (~150 wpm); target
> runtime 4:50 with 10s slack.

---

**[0:00-0:30] The problem** *(screen: landing page at http://127.0.0.1:8377)*

"This test suite has 87% line coverage and every test passes. It also misses more
than half the bugs I'm about to inject into it. I'm Ifeanyichukwu Onwo, and this is
SyntraceAI - an adversarial mutation-testing agent for AI applications. Line coverage
tells you what your tests *visit*. SyntraceAI measures what they *catch* - then
repairs the gaps automatically."

**[0:30-1:05] The baseline** *(screen: terminal - `python baseline/run_baseline.py`)*

"The target is an AI ticket-triage pipeline - prompt templates, a deterministic mock
LLM that reads its own instructions, contract validation, boundary-heavy pricing -
with the suite a rushed team really ships. The baseline is the coverage.py mindset:
measure coverage, then audit the same suite against a frozen bank of 50 injected
bugs - 38 AST mutations, 12 prompt perturbations - each in an isolated sandbox.
Result: 87.1% coverage, but only 24 of 50 bugs caught. Forty-eight percent. That
39-point gap is invisible to every coverage dashboard."

**[1:05-2:15] One realistic execution** *(screen: dashboard `/app`, press Run Mutation Campaign; the progress bar and live log stream)*

"Now the advanced engine, end to end, from Mission Control. Step one, a clean-suite
gate. Step two, the bank - and note this line: comparison swaps inside clamp patterns
are *proven equivalent* and excluded, so that pattern never pads the bank with
unkillable bugs.
Step three, all fifty bugs run in parallel, each in its own sandboxed copy. Step four,
auto-healing: for every survivor, SyntraceAI probes the original and the mutant as
subprocesses over generated inputs, and only an input *re-verified* to discriminate
becomes a test. No verified input, no test - honesty is enforced in code. Step five
re-runs the survivors against the healed suite. About ten seconds, zero API calls."

**[2:15-2:45] The final comparison** *(screen: completion banner, then the tiles: 48.0% -> 98.0%)*

"Before healing: 24 of 50. After: 49 of 50 - 98 percent - with 24 generated tests,
each proven by re-run to kill a specific bug. The one survivor is reported as
likely-equivalent, and manual analysis confirms it - a boundary guard whose
fall-through computes the identical value. Reporting that survivor honestly matters
more to me than a synthetic 100. And on code nobody here wrote - humanize, 98%
coverage, 311 tests - the same engine finds 35 missed behavior changes at exhaustive
depth and writes 12 tests that library never had."

**[2:45-3:30] The changelog** *(screen: CHANGELOG.md, scrolling slowly through iterations 1-8)*

"The improvement changelog is the real story - eight iterations, each driven by
measured evidence. Iteration one measured the 87-versus-48 gap. Iteration two: six
agents built the engine in parallel against a frozen contract - first campaign scored
78%. Instead of shipping that, I root-caused every miss at its code site. Iteration
three closed the gap honestly: two 'unhealable' mutants were provably equivalent, so
the mutator now excludes them; six more were healer input misses. Iteration four
validated on third-party code. Iterations five and seven were adversarial review
fleets - they found a path escape that faked a 100% score, a flaky honesty gate, and
report identities that could overwrite the committed evidence. Six made it work on any
project. And eight hardened it for the public: a read-only
deployed instance that refuses to execute anything."

**[3:30-4:10] The change that contributed most** *(screen: advanced/test_healer.py, the cross-function synthesis block)*

"The change that contributed most: teaching the healer to build inputs that
actually *reach* the failure. Six survivors only misbehave on realistic composed
inputs, like a fully rendered prompt - no input pool touched their code paths. So
the healer now runs the target module's *own* string functions over harvested
literals - cross-function synthesis - alongside pairwise concatenations and
non-integral floats. Those six became six verified tests, the biggest single block
of the climb from 78 to 98 percent. And a sibling enrichment - magnitude pools -
is what later found the twelve humanize gaps."

**[4:10-4:40] One removed experiment** *(screen: CHANGELOG.md iteration 4, the int-pool lesson)*

"And one experiment I removed. To reach humanize's unit-scaling thresholds I added
orders-of-magnitude values - up to 10 to the 33rd - to *every* numeric input pool.
The probes started hanging: an integer parameter can be a format precision, and
formatting a float to 10-to-the-33rd decimal places never returns. The exhaustive run
crawled past ten minutes. I removed magnitudes from the int pools entirely and kept
them float-only - the timeouts vanished and the run dropped to 24 seconds. The
lesson is in the code as a comment, and in the changelog as a warning."

**[4:40-5:00] Close** *(screen: README results table, then https://syntraceai-app.vercel.app)*

"Coverage said 87. Adversarial measurement said 48. SyntraceAI healed it to 98 -
deterministic, seeded, zero API cost. One command reproduces every number:
`python main.py full`. There's a read-only live instance at the link on screen.
Line coverage is a vanity metric - measure what your tests defend. Thanks for
watching."

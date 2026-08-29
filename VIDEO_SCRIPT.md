# 🎬 SyntraceAI — 5-Minute Solution Video Script

> Screen-recording plan with timings. Everything shown on screen is a live run —
> no pre-baked numbers.

---

**[0:00–0:35] Hook — the false-confidence gap** *(screen: landing page at http://127.0.0.1:8377)*

"This test suite has 87% line coverage and every test passes. It also misses more than
half the bugs I'm about to inject into it. I'm Ifeanyichukwu Onwo, and this is
SyntraceAI — an autonomous chaos agent that measures what your tests actually defend,
then repairs the gaps automatically. Line coverage tells you what your tests *visit*.
SyntraceAI tells you what they *catch*."

**[0:35–1:20] The target & the baseline** *(screen: terminal — `python baseline/run_baseline.py`)*

"The demo target is an AI ticket-triage pipeline: prompt templates, a deterministic
mock LLM that reads its own instructions, pydantic contract validation, and
boundary-heavy pricing logic — with the kind of test suite a rushed team really ships.
The baseline audit runs coverage.py, then injects a frozen bank of 50 bugs — 38 AST
mutations and 12 prompt perturbations — and runs the original suite against each one
in an isolated sandbox. Watch the result: 87.1% line coverage… but only 24 of 50 bugs
detected. Forty-eight percent. That 39-point gap is invisible to every coverage
dashboard."

**[1:20–2:40] The advanced campaign** *(screen: terminal — `python advanced/run_mutation.py`)*

"Now the advanced engine. Step one, a clean-suite gate. Step two, the bank: seven AST
operator families — and note this line: comparison swaps inside clamp patterns are
*proven equivalent* and excluded, so the bank never contains an unkillable bug. Step
three, every bug runs in parallel in its own sandboxed project copy — the whole
campaign takes about eight seconds. Step four is the part I'm proudest of:
auto-healing. For every surviving bug, SyntraceAI probes the original and the mutant
as subprocesses over generated inputs — type-hint pools, harvested boundary literals,
and cross-function synthesis, where the module's own functions compose realistic
inputs like fully rendered prompts. Only an input that's re-verified to discriminate
becomes a test. No verified input, no test — honesty is enforced in code."

**[2:40–3:30] The result** *(screen: final terminal summary, then `reports/mutation_report.html`)*

"Final score: 98 percent — 49 of 50 injected bugs detected, 24 hardened assertion
tests generated automatically and proven by re-run. The one survivor is reported as
likely-equivalent, and manual analysis confirms it: a boundary guard whose fall-through
computes the identical value. Reporting that survivor honestly matters more to me than
a synthetic 100."

**[3:30–4:20] Mission Control & the healed tests** *(screen: dashboard `/app`, then `tests/test_healed_assertions.py`)*

"Everything streams into Mission Control — a FastAPI dashboard where you can launch
campaigns, watch the engine log live, and inspect every survivor and every healed
test. And here's the generated suite itself: each test names the mutant it kills, the
operator, the location, and pins the original behavior on the verified discriminating
input — including strict prompt-contract tests that catch hallucination paths like
renamed schema keys and prose-wrapped JSON."

**[4:20–5:00] Close** *(screen: README results table)*

"Same code, same suite: coverage said 87 percent, adversarial measurement said 48 —
and SyntraceAI healed it to 98, deterministically, in eight seconds, for zero API
dollars. One command reproduces every number you've seen: `python main.py full`.
Line coverage is a vanity metric — measure what your tests defend. Thanks for
watching."

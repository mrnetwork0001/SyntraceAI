# 🛠️ SyntraceAI - Deterministic Reproduction Guide

Every command below was executed on a clean environment before this guide was written;
the expected outputs are pasted from real runs. The engine is seeded (default `1337`)
and fully deterministic: same seed → identical bank, identical scores, on any machine,
at any `--jobs` parallelism.

## 1. Environment setup

Requires Python 3.11+ (developed and verified on 3.14). No API keys, no network access,
$0 in model cost - the demo target uses a deterministic rule-based mock LLM.

```bash
git clone https://github.com/mrnetwork0001/SyntraceAI.git   # or unzip the submission archive
cd SyntraceAI
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Engine selftests (optional but recommended)

```bash
python -m pytest selftests/ -q
```
*Expected:* `144 passed`

## 3. Baseline solution (standard line-coverage mindset)

```bash
python baseline/run_baseline.py
```
*Expected output (key lines):*
```
suite is green: 48 tests passed, line coverage 87.1%
False-confidence gap: 87% line coverage, 48% bug detection (24/50 injected bugs caught by the original suite)
```

## 4. Advanced solution (SyntraceAI mutation campaign)

```bash
python advanced/run_mutation.py
```
*Expected output (final line):*
```
Mutation Score: 98.0% | Injected Bugs Detected: 49/50 | Auto-Healed Assertion Tests Generated: 24
```
The one surviving mutant (`M010`) is reported as likely-equivalent; manual analysis
confirms it is a true equivalent mutant (`coupon_pct <= 0.0` → `< 0.0` around a guard
whose fall-through computes the identical value at the boundary).

Or run both in sequence with one command:

```bash
python main.py full
```

## 4b. Third-party validation target (humanize 4.16.0, vendored)

```bash
python baseline/run_baseline.py --target targets/humanize --json reports/humanize_baseline_report.json
python advanced/run_mutation.py --target targets/humanize \
    --json reports/humanize_mutation_report.json --html reports/humanize_mutation_report.html \
    --trajectory trajectories/campaign_trace_humanize.json
```
*Expected (key lines):*
```
suite is green: 311 tests passed, line coverage 98.1%
Mutation Score: 94.7% | Injected Bugs Detected: 36/38 | Auto-Healed Assertion Tests Generated: 0
```
Both survivors sit inside `_format_not_finite`, where `math.isinf(value)` guards the
comparison: `M036` swaps `value < 0` to `<=`, `M016` shifts `value > 0` to `> -1`. Only
±inf ever reaches them, so neither change is observable - reported as likely-equivalent.

Note: every campaign starts by deleting the target's generated
`tests/test_healed_assertions.py`. The 12-test humanize file is NOT committed to the
repository - it is produced by
the exhaustive campaign below; running only the 38-bank campaign (0 heals needed)
leaves no healed file behind until you run the exhaustive one again.

Exhaustive campaign over every mutation site (~25s):
```bash
python advanced/run_mutation.py --target targets/humanize --max-code-mutants 400 \
    --json reports/humanize_full_mutation_report.json --html reports/humanize_full_mutation_report.html \
    --trajectory trajectories/campaign_trace_humanize_full.json
```
*Expected (key lines):*
```
AST mutation sites discovered: 253 → bank of 253 code mutants + 0 prompt perturbations
Detected 218/253 (86.2%) with the original suite
Synthesized 12 hardened assertion tests (23 survivor(s) classified likely-equivalent/unhealable)
Mutation Score: 90.9% | Injected Bugs Detected: 230/253 | Auto-Healed Assertion Tests Generated: 12
```

## 4c. CI gate

`python advanced/run_mutation.py --fail-under 95` exits 1 when the final score drops
below the threshold. `.github/workflows/ci.yml` runs the selftests, the demo campaign
(gate 95%), the humanize frozen-bank campaign (gate 85%) and the humanize exhaustive
campaign (gate 88%) on Python 3.11 and 3.12 for every push to `main` and every pull
request.

## 5. Mission Control dashboard (optional)

```bash
python dashboard/server.py
```
Open http://127.0.0.1:8377 - landing page with live campaign stats; **Launch App**
opens the dashboard at `/app`. There, **Run against** takes the path of any local Python
project (the demo and humanize are one-click presets), and **Run Mutation Campaign**
streams the engine log live. Each project writes its own report set, selectable from the
dropdown in the header.

**Reset** (the arrow button in the header) offers two things: *Clear the view* blanks the
screen without touching disk - remembered per report set, so it survives a reload and
lifts when you restore it or run a campaign - and *Delete saved reports* removes a
report set's files along with the healed-test file that campaign wrote. The demo and humanize sets are the
report files committed to this repo, so Reset refuses to delete them - only report sets
from your own projects can be cleared.

## 5b. Run it on your own project

```bash
python advanced/run_mutation.py --target ~/code/my-app
```
If the layout isn't `app/` + `tests/`, add `syntrace_target.json` to that project:
```json
{ "source_package": "myapp", "tests_dir": "tests", "prompt_templates": null }
```
Reports land in `reports/<project>_*` and the generated tests are written into the
project's own tests directory. Nothing is uploaded; the tool reads and mutates temporary
copies locally.

## 6. Artifacts produced

| Path | Contents |
| :--- | :--- |
| `reports/baseline_report.json` | coverage %, detection counts, per-operator breakdown |
| `reports/mutation_report.json` | full campaign result (pre/post-heal, survivors, healed tests) |
| `reports/mutation_report.html` | self-contained visual report |
| `targets/sample_app/tests/test_healed_assertions.py` | the auto-generated hardened suite |
| `trajectories/campaign_trace_demo.json` | the campaign's own execution trajectory (engine output, not a coding-agent trace) |
| `reports/humanize_*_report.json` / `.html` | third-party target: baseline, 38-bank campaign, exhaustive campaign |
| `targets/humanize/tests/test_healed_assertions.py` | 12 tests SyntraceAI wrote for humanize (generated by the exhaustive campaign; not committed) |
| `trajectories/campaign_trace_humanize*.json` | humanize campaign trajectories |

## 7. Runtime & cost

- **Runtime** (8-core laptop, hardware-dependent): demo campaign ~8–10s (7.9s on an
  idle machine; the committed report records 9.5s), baseline audit ~3s,
  `python main.py full` ~12–14s end to end.
- **API cost:** $0.00 - local AST parsing + deterministic mock LLM only.
- **Repeatability note:** both `run_baseline.py` and `run_mutation.py` delete any
  previously generated `test_healed_assertions.py` before measuring, so every run
  starts from the original un-hardened suite and reproduces the same numbers.
- **One campaign at a time:** campaigns against the same target share that target's
  generated `test_healed_assertions.py` and the default report paths, and there is no
  lock. Two concurrent runs (for example a CLI campaign while a dashboard-launched one
  is in flight) can leak one run's healed tests into the other's original-suite phase
  and corrupt the pre-heal number. Run campaigns serially.
- **What "identical" means:** across repeated runs (any `--jobs`, any supported Python
  version) every verdict, score, mutant ID, and healed test is identical. The mutation
  report JSON is byte-stable except for the single top-level `wall_time_s` field -
  `diff` two runs yourself.

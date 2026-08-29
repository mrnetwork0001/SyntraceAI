# 🛠️ SyntraceAI — Deterministic Reproduction Guide

Every command below was executed on a clean environment before this guide was written;
the expected outputs are pasted from real runs. The engine is seeded (default `1337`)
and fully deterministic: same seed → identical bank, identical scores, on any machine,
at any `--jobs` parallelism.

## 1. Environment setup

Requires Python 3.11+ (developed and verified on 3.14). No API keys, no network access,
$0 in model cost — the demo target uses a deterministic rule-based mock LLM.

```bash
git clone https://github.com/mrnetwork/SyntraceAI.git
cd SyntraceAI
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Engine selftests (optional but recommended)

```bash
python -m pytest selftests/ -q
```
*Expected:* `79 passed`

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

## 5. Mission Control dashboard (optional)

```bash
python dashboard/server.py
```
Open http://127.0.0.1:8377 — landing page with live campaign stats; **Launch App**
opens the dashboard at `/app` where you can trigger runs and watch the engine log live.

## 6. Artifacts produced

| Path | Contents |
| :--- | :--- |
| `reports/baseline_report.json` | coverage %, detection counts, per-operator breakdown |
| `reports/mutation_report.json` | full campaign result (pre/post-heal, survivors, healed tests) |
| `reports/mutation_report.html` | self-contained visual report |
| `targets/sample_app/tests/test_healed_assertions.py` | the auto-generated hardened suite |
| `trajectories/agent_trace_02.json` | the campaign's own execution trajectory |

## 7. Runtime & cost

- **Runtime:** ~8 seconds for the full campaign on an 8-core laptop (measured 7.9s);
  baseline audit ~6s.
- **API cost:** $0.00 — local AST parsing + deterministic mock LLM only.
- **Repeatability note:** both `run_baseline.py` and `run_mutation.py` delete any
  previously generated `test_healed_assertions.py` before measuring, so every run
  starts from the original un-hardened suite and reproduces the same numbers.

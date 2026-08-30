# Trajectories

Two different kinds of file live here. The distinction matters, so the names
carry it.

## `agent_trace_01.json` - the coding-agent trajectory

The trace of the **coding agent that built this project**: Claude Code driving
an orchestrator plus six parallel implementation subagents against a frozen
module contract. This is the file that answers the challenge's agent-trajectory
requirement.

It records, per step, the instruction the agent was given, the action it took,
the command it ran, how the tools responded, and the **human checkpoints** where
the owner reviewed and redirected the work - including step 3, where the first
end-to-end campaign came back at 78% and every miss was root-caused at its code
site before anything was changed.

## `campaign_trace_*.json` - engine output

These are **not** coding-agent traces. They are the SyntraceAI engine narrating
its own run: gate the suite, build the bank, evaluate in sandboxes, heal, re-run.
The engine writes them through `advanced/trajectory_logger.py`, and they are
**regenerated on every campaign**.

| File | Produced by |
| :--- | :--- |
| `campaign_trace_demo.json` | `python main.py mutate` (demo target) |
| `campaign_trace_humanize.json` | the humanize frozen-bank campaign |
| `campaign_trace_humanize_full.json` | the humanize exhaustive campaign |

A campaign against your own project writes `campaign_trace_<project>.json`
alongside its report set.

Both kinds share one schema, described in `docs/ARCHITECTURE.md` §11.

"""SyntraceAI - Autonomous Agentic Mutation Testing & Hallucination Stress-Tester.

CLI hub. Subcommands delegate to the baseline auditor and the advanced engine:

    python main.py baseline   # coverage.py audit + bug-bank detection (original suite)
    python main.py mutate     # full adversarial campaign with auto-healing
    python main.py full       # baseline, then the advanced campaign

Extra arguments are passed through, e.g.:
    python main.py mutate --seed 1337 --jobs 8
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

BANNER = """\
==========================================================================
 🧪 SYNTRACEAI - Autonomous Agentic Mutation Testing Fleet
 Chaos Engineering & Adversarial Mutation for AI Systems
==========================================================================\
"""

COMMANDS = {
    "baseline": [REPO_ROOT / "baseline" / "run_baseline.py"],
    "mutate": [REPO_ROOT / "advanced" / "run_mutation.py"],
}


def run(script: Path, extra: list[str]) -> int:
    sys.stdout.flush()
    return subprocess.call([sys.executable, str(script), *extra], cwd=REPO_ROOT)


def main(argv: list[str]) -> int:
    print(BANNER)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    command, extra = argv[0], argv[1:]
    if command == "full":
        code = run(COMMANDS["baseline"][0], extra)
        if code != 0:
            return code
        return run(COMMANDS["mutate"][0], extra)
    if command in COMMANDS:
        return run(COMMANDS[command][0], extra)

    print(f"Unknown command: {command!r}. Use baseline | mutate | full.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

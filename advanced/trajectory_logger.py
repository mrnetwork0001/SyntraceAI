"""Agent trajectory JSON logging (ARCHITECTURE.md §11).

Records the steps an autonomous agent takes while building or running a
campaign, in the exact schema of ``trajectories/agent_trace_01.json``::

    {
      "task": "...", "agent": "...", "timestamp": "2026-08-29T12:00:00Z",
      "steps": [
        {"step_index": 1, "instruction": "...", "action": "...",
         "command": "...", "target": "...", "tool_output": "...",
         "human_checkpoint": "..."}
      ]
    }

``step_index`` auto-increments from 1. The four optional step keys
(``command``, ``target``, ``tool_output``, ``human_checkpoint``) are omitted
entirely when ``None`` — never serialized as ``null``. The top-level
timestamp is ISO-8601 UTC with a ``Z`` suffix, captured when the logger is
created (trajectory start time).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class TrajectoryLogger:
    """Accumulates trajectory steps and writes them as pretty-printed JSON."""

    def __init__(self, path: Path, task: str, agent: str) -> None:
        self.path = Path(path)
        self.task = task
        self.agent = agent
        self.timestamp = _utc_now_iso()
        self.steps: list[dict[str, Any]] = []

    def log_step(
        self,
        instruction: str,
        action: str,
        *,
        command: str | None = None,
        target: str | None = None,
        tool_output: str | None = None,
        human_checkpoint: str | None = None,
    ) -> dict[str, Any]:
        """Append one step and return it; ``step_index`` auto-increments from 1.

        Optional keys whose value is ``None`` are omitted from the step
        entirely, matching the reference trace schema.
        """
        step: dict[str, Any] = {
            "step_index": len(self.steps) + 1,
            "instruction": instruction,
            "action": action,
        }
        optional: tuple[tuple[str, str | None], ...] = (
            ("command", command),
            ("target", target),
            ("tool_output", tool_output),
            ("human_checkpoint", human_checkpoint),
        )
        for key, value in optional:
            if value is not None:
                step[key] = value
        self.steps.append(step)
        return step

    def to_dict(self) -> dict[str, Any]:
        """The full trajectory document, in reference-schema key order."""
        return {
            "task": self.task,
            "agent": self.agent,
            "timestamp": self.timestamp,
            "steps": list(self.steps),
        }

    def save(self) -> Path:
        """Write the trajectory as pretty-printed JSON and return the path.

        Parent directories are created if missing; an existing file is
        overwritten, so calling :meth:`save` repeatedly keeps the file in
        sync with the steps logged so far.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return self.path

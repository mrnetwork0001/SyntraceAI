"""Selftests for advanced.trajectory_logger (ARCHITECTURE.md §11).

Verifies the logger against the schema of the reference trace
``trajectories/agent_trace_01.json``: top-level key set and order, step
auto-indexing from 1, omission of None optionals, ISO-8601 UTC timestamp,
and pretty-printed valid JSON output.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from advanced.core_types import repo_root
from advanced.trajectory_logger import TrajectoryLogger

REFERENCE_TRACE = repo_root() / "trajectories" / "agent_trace_01.json"


def _make_logger(tmp_path: Path) -> TrajectoryLogger:
    return TrajectoryLogger(tmp_path / "trace.json", task="Selftest task", agent="selftest-agent")


def _saved(logger: TrajectoryLogger) -> dict:
    return json.loads(logger.save().read_text(encoding="utf-8"))


def test_save_writes_valid_pretty_json(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log_step("run the suite", "run_command", command="pytest -q", tool_output="all green")
    path = logger.save()
    assert path == tmp_path / "trace.json"
    text = path.read_text(encoding="utf-8")
    json.loads(text)  # valid JSON
    assert text.count("\n") > 5  # pretty-printed, not one line
    assert '  "task"' in text  # indent=2


def test_top_level_schema_keys_and_order(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log_step("do a thing", "noop")
    data = _saved(logger)
    assert list(data.keys()) == ["task", "agent", "timestamp", "steps"]
    assert data["task"] == "Selftest task"
    assert data["agent"] == "selftest-agent"
    assert isinstance(data["steps"], list)


def test_timestamp_is_iso8601_utc(tmp_path: Path) -> None:
    data = _saved(_make_logger(tmp_path))
    timestamp = data["timestamp"]
    assert timestamp.endswith("Z")
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_step_index_auto_increments(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    for i in range(4):
        logger.log_step(f"instruction {i}", "run_command")
    data = _saved(logger)
    assert [step["step_index"] for step in data["steps"]] == [1, 2, 3, 4]


def test_none_optionals_are_omitted(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log_step("bare step", "noop")
    logger.log_step("partial step", "write_to_file", target="advanced/x.py")
    path = logger.save()
    bare, partial = json.loads(path.read_text(encoding="utf-8"))["steps"]
    assert set(bare) == {"step_index", "instruction", "action"}
    assert set(partial) == {"step_index", "instruction", "action", "target"}
    assert partial["target"] == "advanced/x.py"
    assert "null" not in path.read_text(encoding="utf-8")


def test_all_optionals_recorded_in_schema_order(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log_step(
        "full step",
        "run_command",
        command="echo hi",
        target="somewhere.py",
        tool_output="hi",
        human_checkpoint="Approved",
    )
    (step,) = _saved(logger)["steps"]
    assert list(step.keys()) == [
        "step_index",
        "instruction",
        "action",
        "command",
        "target",
        "tool_output",
        "human_checkpoint",
    ]
    assert step["command"] == "echo hi"
    assert step["target"] == "somewhere.py"
    assert step["tool_output"] == "hi"
    assert step["human_checkpoint"] == "Approved"


def test_replaying_reference_trace_reproduces_its_steps(tmp_path: Path) -> None:
    """Feeding the reference trace's steps through the logger must reproduce
    them exactly — same keys present, same keys omitted, same indices."""
    reference = json.loads(REFERENCE_TRACE.read_text(encoding="utf-8"))
    logger = TrajectoryLogger(
        tmp_path / "replay.json", task=reference["task"], agent=reference["agent"]
    )
    for ref_step in reference["steps"]:
        logger.log_step(
            ref_step["instruction"],
            ref_step["action"],
            command=ref_step.get("command"),
            target=ref_step.get("target"),
            tool_output=ref_step.get("tool_output"),
            human_checkpoint=ref_step.get("human_checkpoint"),
        )
    ours = _saved(logger)
    assert set(ours.keys()) == set(reference.keys())
    assert ours["steps"] == reference["steps"]


def test_save_creates_missing_parent_dirs(tmp_path: Path) -> None:
    logger = TrajectoryLogger(tmp_path / "nested" / "deep" / "trace.json", task="t", agent="a")
    assert logger.save().is_file()


def test_resave_reflects_new_steps(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log_step("first", "noop")
    assert len(_saved(logger)["steps"]) == 1
    logger.log_step("second", "noop")
    data = _saved(logger)
    assert len(data["steps"]) == 2
    assert data["steps"][1]["step_index"] == 2

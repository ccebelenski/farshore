"""The general's war diary: every epoch attempt leaves a JSONL record."""

from __future__ import annotations

import json
from pathlib import Path

from empire.ai.general.trace import EpochTraceWriter


def test_writer_appends_one_json_line_per_record(tmp_path: Path) -> None:
    writer = EpochTraceWriter(tmp_path / "traces" / "war.jsonl")
    writer.write({"turn": 8, "failure": None})
    writer.write({"turn": 16, "failure": "general unavailable"})
    lines = writer.path.read_text().splitlines()
    assert [json.loads(line)["turn"] for line in lines] == [8, 16]
    assert json.loads(lines[1])["failure"] == "general unavailable"


def test_writer_survives_unwritable_path() -> None:
    writer = EpochTraceWriter(Path("/proc/definitely/not/writable/war.jsonl"))
    writer.write({"turn": 8})  # must not raise; writer disables itself
    writer.write({"turn": 16})


def test_controller_traces_success_and_failure_epochs(tmp_path: Path) -> None:
    """Both a failed and a successful epoch leave diary lines with the
    briefing text, and the success carries its amendments."""
    from empire.ai.general.client import ChatAnswer
    from empire.ai.general.controller import LlmGeneralController
    from tests.empire.ai.general.test_controller import FORM_ANSWER, _staged_board, _view

    class _FailsOnce:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str, *, seed: int, system: str | None = None) -> ChatAnswer:
            del prompt, seed
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("down")
            return ChatAnswer(
                text=FORM_ANSWER, finish_reason="stop", attempts=1, model="stub"
            )

    game, p1 = _staged_board()
    controller = LlmGeneralController(client=_FailsOnce(), cadence=2)
    controller.attach_trace(EpochTraceWriter(tmp_path / "war.jsonl"))
    controller.plan_turn(_view(game, p1, 1))
    controller.plan_turn(_view(game, p1, 3))

    records = [json.loads(line) for line in (tmp_path / "war.jsonl").read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["failure"] is not None and "ORDERS CONTRACT" in records[0]["briefing"]
    assert records[1]["failure"] is None
    assert records[1]["amendments"] and records[1]["registry"]
    # The commander's plan is surfaced as its own JSONL field on every record.
    assert "plan" in records[0] and "plan" in records[1]

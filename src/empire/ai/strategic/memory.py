"""`AIMemory`: the strategic AI's cross-turn state (see
`planning/03-ai-design.md` §3.5).

Carries the previous turn's goals (continuity / anti-thrash, later phases) and
the active `TaskForce`s the operational planner maintains across turns. Learned
beliefs and production history attach here in later phases, and the whole thing
is serialized with the save in Phase 16.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from empire.ai.strategic.goals.base import Goal

if TYPE_CHECKING:
    from empire.ai.strategic.operational import TaskForce


def _no_task_forces() -> list[TaskForce]:
    return []


@dataclass
class AIMemory:
    """Persistent state the AI carries between turns."""

    last_goals: tuple[Goal, ...] = ()
    task_forces: list[TaskForce] = field(default_factory=_no_task_forces)
    next_task_force_id: int = 1

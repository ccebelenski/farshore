"""`AIMemory`: the strategic AI's cross-turn state (see
`planning/03-ai-design.md` §3.5).

Carries the previous turn's goals (continuity / anti-thrash, later phases) and
the active `TaskForce`s the operational planner maintains across turns. Learned
beliefs and production history attach here in later phases, and the whole thing
is serialized with the save in Phase 16.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from empire.ai.strategic.goals.base import Goal
from empire.ai.strategic.goals.concrete import goal_from_dict

if TYPE_CHECKING:
    from empire.ai.strategic.operational import TaskForce


def _no_task_forces() -> list[TaskForce]:
    return []


def _no_abandoned_targets() -> dict[int, int]:
    return {}


@dataclass
class AIMemory:
    """Persistent state the AI carries between turns."""

    last_goals: tuple[Goal, ...] = ()
    task_forces: list[TaskForce] = field(default_factory=_no_task_forces)
    next_task_force_id: int = 1
    # City ids the operational layer abandoned as hopeless, mapped to the turn
    # it scrapped the siege (Phase 15.7). While a target sits in cooldown the
    # planner refuses to re-assemble a force for it, so the greedy strategist
    # re-proposing the same doomed capture can't cause assemble/abandon thrash.
    abandoned_targets: dict[int, int] = field(default_factory=_no_abandoned_targets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_goals": [g.to_dict() for g in self.last_goals],
            "task_forces": [tf.to_dict() for tf in self.task_forces],
            "next_task_force_id": self.next_task_force_id,
            "abandoned_targets": [[cid, t] for cid, t in self.abandoned_targets.items()],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AIMemory:
        # Local import: TaskForce lives in `operational`, which imports this
        # module's type only under TYPE_CHECKING (no runtime cycle).
        from empire.ai.strategic.operational import TaskForce

        return cls(
            last_goals=tuple(goal_from_dict(g) for g in data.get("last_goals", [])),
            task_forces=[TaskForce.from_dict(tf) for tf in data.get("task_forces", [])],
            next_task_force_id=int(data.get("next_task_force_id", 1)),
            abandoned_targets={
                int(cid): int(t) for cid, t in data.get("abandoned_targets", [])
            },
        )

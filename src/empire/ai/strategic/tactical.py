"""`TacticalExecutor`: turn the live task forces into one `UnitMove` per unit.

It resolves each unit's role from its task force, asks the registry for the
matching `Behavior`, and collects the moves. Units with no force (idle) get
`DefaultBehavior` (hold). `revise_move` routes a mid-turn surprise to the same
behavior for a single corrective step (see `03-ai-design.md` §1, §3.4).
"""

from __future__ import annotations

from empire.ai.strategic.behaviors.base import HuntBehavior
from empire.ai.strategic.behaviors.registry import DEFAULT_BEHAVIOR, behavior_for
from empire.ai.strategic.operational import Role, TaskForce
from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import UnitMove
from empire.contracts.world_view import WorldView
from empire.core.identity import UnitId
from empire.core.unit import Unit


class TacticalExecutor:
    """Per-unit movement from task-force role assignments."""

    def __init__(self) -> None:
        self._hunt = HuntBehavior()

    def plan_moves(
        self, forces: list[TaskForce], view: WorldView
    ) -> list[UnitMove]:
        assignment = self._assignments(forces)
        moves: list[UnitMove] = []
        for unit in sorted(view.own_units, key=lambda u: int(u.id)):
            if unit.is_aboard():
                continue  # cargo rides its carrier; it isn't independently moved
            pair = assignment.get(unit.id)
            if pair is None:
                # Unassigned → hunt mode (explore), not sentry: idling in a
                # friendly city gets a unit disbanded (§5.4).
                moves.append(self._hunt.next_move(unit, view, None))
                continue
            force, role = pair
            moves.append(behavior_for(unit.kind, role).next_move(unit, view, force))
        return moves

    def revise_move(
        self,
        unit: Unit,
        surprise: Surprise,
        view: WorldView,
        forces: list[TaskForce],
    ) -> UnitMove:
        for force in forces:
            role = force.role_assignments.get(unit.id)
            if role is not None:
                return behavior_for(unit.kind, role).revise(unit, surprise, view, force)
        return DEFAULT_BEHAVIOR.revise(unit, surprise, view, None)

    @staticmethod
    def _assignments(
        forces: list[TaskForce],
    ) -> dict[UnitId, tuple[TaskForce, Role]]:
        assignment: dict[UnitId, tuple[TaskForce, Role]] = {}
        for force in forces:
            if force.is_terminal():
                continue
            for unit_id, role in force.role_assignments.items():
                assignment[unit_id] = (force, role)
        return assignment

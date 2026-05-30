"""Army behaviors: assault (take the objective) and garrison (hold near a
friendly city)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from empire.ai.strategic.behaviors.base import (
    Behavior,
    advance_toward,
    force_target,
    sentry,
)
from empire.contracts.turn_plan import UnitMove
from empire.contracts.world_view import WorldView
from empire.core.unit import Unit
from empire.pathfinding.cost import ARMY as ARMY_PROFILE

if TYPE_CHECKING:
    from empire.ai.strategic.operational import TaskForce


class ArmyAssaultBehavior(Behavior):
    """March toward the objective city; the engine resolves combat/capture as
    the army steps onto an enemy unit or city."""

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        return advance_toward(unit, force_target(force), view, ARMY_PROFILE)


class ArmyGarrisonBehavior(Behavior):
    """Hold the defended city. Armies can't sit *on* a friendly city (§5.4),
    so this closes to its edge and then holds, bodying any approaching enemy.
    """

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        target = force_target(force)
        if target is None:
            return sentry(unit)
        # Already adjacent (or on it): hold station — repair / intercept.
        if unit.coord.chebyshev_to(target) <= 1:
            return sentry(unit)
        return advance_toward(unit, target, view, ARMY_PROFILE)

"""Fighter behaviors: strike (range-aware attack toward the objective) and
scout (push into the unknown, return before the tank runs dry)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from empire.ai.strategic.behaviors.base import (
    Behavior,
    advance_toward,
    force_target,
    nearest_friendly_city,
    nearest_frontier,
    sentry,
)
from empire.contracts.turn_plan import UnitMove
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.unit import Unit
from empire.pathfinding.cost import AIR as AIR_PROFILE

if TYPE_CHECKING:
    from empire.ai.strategic.operational import TaskForce


def _fuel_safe(unit: Unit, destination: Coord | None, view: WorldView) -> bool:
    """True if `unit` could fly to `destination` and still reach a friendly
    refuel port — i.e. it isn't about to run out of fuel mid-mission."""
    home = nearest_friendly_city(unit, view)
    if home is None:
        return True  # nowhere to return to anyway; press on
    home_dist = unit.coord.chebyshev_to(home)
    dest_dist = 0 if destination is None else unit.coord.chebyshev_to(destination)
    return unit.range >= home_dist + dest_dist


class FighterStrikeBehavior(Behavior):
    """Fly toward the objective, but peel home to refuel when fuel is short."""

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        target = force_target(force)
        if not _fuel_safe(unit, target, view):
            return advance_toward(unit, nearest_friendly_city(unit, view), view, AIR_PROFILE)
        return advance_toward(unit, target, view, AIR_PROFILE)


class FighterScoutBehavior(Behavior):
    """Push toward unexplored ground; return to refuel when fuel is short."""

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        if not _fuel_safe(unit, force_target(force), view):
            return advance_toward(unit, nearest_friendly_city(unit, view), view, AIR_PROFILE)
        target = force_target(force) or nearest_frontier(unit, view)
        if target is None:
            return sentry(unit)
        return advance_toward(unit, target, view, AIR_PROFILE)

"""Naval behaviors: transport ferry, ship escort, ship patrol, sub ambush."""

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
from empire.core.coord import Coord
from empire.core.unit import Unit
from empire.pathfinding.cost import SEA as SEA_PROFILE

if TYPE_CHECKING:
    from empire.ai.strategic.operational import TaskForce


class TransportFerryBehavior(Behavior):
    """Sail toward the landing zone. Armies board automatically by stepping
    onto the transport (engine load); amphibious unload is issued separately
    by the executor when the transport reaches the target shore."""

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        return advance_toward(unit, force_target(force), view, SEA_PROFILE)


class ShipEscortBehavior(Behavior):
    """Stay close to the force's transport, screening it from threats. Falls
    back to the objective when there's no transport to guard."""

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        guard = _escorted_coord(unit, view, force)
        if guard is not None:
            # Already alongside: hold and intercept rather than crowd the cell.
            if unit.coord.chebyshev_to(guard) <= 1:
                return sentry(unit)
            return advance_toward(unit, guard, view, SEA_PROFILE)
        return advance_toward(unit, force_target(force), view, SEA_PROFILE)


class ShipPatrolBehavior(Behavior):
    """Sweep toward the objective area, engaging shipping it runs into."""

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        return advance_toward(unit, force_target(force), view, SEA_PROFILE)


class SubAmbushBehavior(Behavior):
    """Run to the objective (a shipping lane) submerged, striking on contact."""

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        return advance_toward(unit, force_target(force), view, SEA_PROFILE)


def _escorted_coord(
    unit: Unit, view: WorldView, force: TaskForce | None
) -> Coord | None:
    """Coord of the transport this escort is guarding, if any."""
    if force is None:
        return None
    from empire.core.unit import UnitKind

    own = {u.id: u for u in view.own_units}
    for uid in force.unit_ids:
        guarded = own.get(uid)
        if guarded is not None and guarded.kind is UnitKind.TRANSPORT:
            return guarded.coord
    return None

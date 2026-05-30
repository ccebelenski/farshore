"""The `Behavior` ABC, `DefaultBehavior`, and shared movement helpers.

A `Behavior` turns one unit's situation — itself, the live `WorldView`, and
its `TaskForce` (or none) — into a single-turn `UnitMove`. Behaviors are
stateless and independent: each knows only its own unit, never the others
(see `planning/03-ai-design.md` §3.4). `revise` produces a one-step correction
after a mid-turn `Surprise`; the default re-plans against the live view, which
never crashes for any surprise variant.

The `behavior_for(kind, role)` registry (see `registry.py`) lives in the AI
layer rather than on `Unit`, because `core` may not depend on `ai`. The
per-unit-kind modularity the design wanted is preserved: each kind's behaviors
live in their own module and register their own (role → behavior) map.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import UnitMove
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.unit import Unit
from empire.pathfinding.bfs import BFSPathfinder
from empire.pathfinding.cost import PathCostProfile

if TYPE_CHECKING:
    from empire.ai.strategic.operational import TaskForce

_BFS = BFSPathfinder()


class Behavior(ABC):
    """Per-(unit, role) turn-time movement policy."""

    @abstractmethod
    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        """The unit's intended move this turn."""

    def revise(
        self,
        unit: Unit,
        surprise: Surprise,
        view: WorldView,
        force: TaskForce | None,
    ) -> UnitMove:
        """A single revised step after a mid-turn surprise.

        Default: re-plan a fresh move against the now-current view. Subclasses
        with special handling override; this base is total over every
        `Surprise` variant and never raises.
        """
        del surprise
        return self.next_move(unit, view, force)


class DefaultBehavior(Behavior):
    """Hold position. The fallback for any (unit, role) pair without a
    dedicated behavior, and for idle units."""

    def next_move(
        self, unit: Unit, view: WorldView, force: TaskForce | None
    ) -> UnitMove:
        del view, force
        return UnitMove(unit_id=unit.id)

    def revise(
        self,
        unit: Unit,
        surprise: Surprise,
        view: WorldView,
        force: TaskForce | None,
    ) -> UnitMove:
        del surprise, view, force
        return UnitMove(unit_id=unit.id)


# --- shared movement helpers -------------------------------------------------


def sentry(unit: Unit) -> UnitMove:
    """Stay put this turn."""
    return UnitMove(unit_id=unit.id)


def advance_toward(
    unit: Unit, target: Coord | None, view: WorldView, profile: PathCostProfile
) -> UnitMove:
    """Step toward `target` along a fog-aware least-cost path.

    Emits up to the unit's movement budget of cells. Returns a sentry move
    when there's no target or no known path (the engine re-validates and may
    stop early — e.g. an army halts at the edge of a friendly city, §5.4)."""
    if target is None:
        return sentry(unit)
    path = _BFS.find_path(
        start=unit.coord,
        goal=target,
        real_map=view.real_map(),
        profile=profile,
        view=view.own_player.view,
    )
    if path is None or path.steps == 0:
        return sentry(unit)
    budget = unit.moves_this_turn()
    steps = path.cells[1 : 1 + budget]
    return UnitMove(unit_id=unit.id, path=tuple((c.x, c.y) for c in steps))


def force_target(force: TaskForce | None) -> Coord | None:
    return None if force is None else force.target


def nearest_friendly_city(unit: Unit, view: WorldView) -> Coord | None:
    """The friendly city closest to `unit` (e.g. a refuel/repair port)."""
    cities = view.own_cities
    if not cities:
        return None
    return min(cities, key=lambda c: c.coord.chebyshev_to(unit.coord)).coord

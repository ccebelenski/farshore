"""`FeasibilityOracle`: cheap, pure forward-checks the strategist runs before
committing to a goal (see `planning/03-ai-design.md` §3.2, §7).

No state — every answer is a deterministic function of the `WorldView` passed
in, so the same world always yields the same verdict. The checks are
intentionally coarse: they exist to keep the strategist from proposing
obviously-impossible goals, not to plan the operation (that is Phase 13's job,
with real pathfinding).
"""

from __future__ import annotations

from empire.ai.strategic.goals.base import ForceComposition
from empire.ai.strategic.intel.report import Threat
from empire.contracts.world_view import WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.tile import TerrainKind
from empire.core.unit import UNIT_REGISTRY, UnitKind

# How close a friendly unit must be to count toward a city's defence.
DEFENSE_RADIUS = 3
# Safety cap on the reachability BFS so a fast unit over a long horizon does
# not explode the frontier; well beyond any realistic single-goal distance.
_MAX_REACH_STEPS = 60


class FeasibilityOracle:
    """Stateless forward-check service for the strategist."""

    def can_assemble(
        self, composition: ForceComposition, by_turn: int, view: WorldView
    ) -> bool:
        """Could we field `composition` by turn `by_turn`?

        Coarse capacity model: units we already own cover part of the bill;
        the shortfall must be buildable within the window's total production
        (one point per owned city per turn). Existing-units-only when the
        window is zero or negative.
        """
        turns = by_turn - view.turn
        if turns < 0:
            return False

        owned_by_kind: dict[UnitKind, int] = {}
        for unit in view.own_units:
            owned_by_kind[unit.kind] = owned_by_kind.get(unit.kind, 0) + 1

        required_production = 0
        for kind, needed in composition.entries:
            shortfall = needed - owned_by_kind.get(kind, 0)
            if shortfall > 0:
                required_production += shortfall * UNIT_REGISTRY[kind].build_time

        available_production = len(view.own_cities) * turns
        return required_production <= available_production

    def defensible(self, city: City, threat: Threat, view: WorldView) -> bool:
        """Can we hold `city` against `threat` with nearby friendly forces?

        Coarse: sum the combat strength of friendly units within
        `DEFENSE_RADIUS` of the city and compare to the threat's projected
        combat power. (Cities have no HP of their own, §5.4 — defence is the
        garrison-able force around them.)
        """
        defender_strength = sum(
            type(unit).strength
            for unit in view.own_units
            if unit.coord.chebyshev_to(city.coord) <= DEFENSE_RADIUS
        )
        return defender_strength >= threat.combat_power

    def reachable(
        self,
        start: Coord,
        goal: Coord,
        kind: UnitKind,
        by_turn: int,
        view: WorldView,
    ) -> bool:
        """Could a unit of `kind` get from `start` to `goal` by `by_turn`?

        A bounded BFS over terrain legal for the kind, treating unobserved
        cells as traversable (optimistic — the operational layer verifies with
        real pathfinding). Capped by the unit's movement budget over the
        window and by `_MAX_REACH_STEPS`.
        """
        if not view.in_bounds(goal):
            return False
        turns = by_turn - view.turn
        if turns < 0:
            return False
        cls = UNIT_REGISTRY[kind]
        max_steps = min(cls.speed * turns, _MAX_REACH_STEPS)
        if start == goal:
            return True
        if max_steps <= 0:
            return False

        legal = cls.legal_terrain
        reached = {start}
        frontier = {start}
        for _ in range(max_steps):
            nxt: set[Coord] = set()
            for cell in frontier:
                for neighbor in cell.neighbors():
                    if neighbor in reached or not view.in_bounds(neighbor):
                        continue
                    if _passable(view, neighbor, legal):
                        if neighbor == goal:
                            return True
                        nxt.add(neighbor)
            if not nxt:
                break
            reached |= nxt
            frontier = nxt
        return False


def _passable(view: WorldView, c: Coord, legal: frozenset[TerrainKind]) -> bool:
    tile = view.terrain_at(c)
    if tile is None:
        return True  # unobserved — assume we could pass (verified later)
    return tile.terrain in legal

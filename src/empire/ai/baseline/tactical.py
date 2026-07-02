"""`BaselineTactical`: per-unit-kind decision functions.

The dispatcher (`BaselineAI`) calls `decide(unit, view)` for each owned unit
and gets back the cells the unit intends to enter this turn (1-step path
for ARMY/speed-1 units). Per-kind logic is split into dedicated methods so
weight tables and heuristics stay scoped to one unit type at a time.

The ARMY decision is the real one. Other unit kinds fall through to
`_sentry_decision` — BaselineAI's players only build Army.

Performance shape (BaselineAI is also the search AIs' opponent model, so
its planning cost bounds every playout): one BFS
`DistanceField` flood per unit answers all of that unit's objective
distances at once, and the winner's path is reconstructed by descending the
same field — no per-(unit, target) searches. Distances (hence scores and the
chosen objective) are exactly what per-pair `find_path` produced; only ties
between equal-length *routes* may break differently (arena-revalidated).
"""

from __future__ import annotations

from dataclasses import dataclass

from empire.ai.vision import frontier_cells, terrain_for_view
from empire.contracts.turn_plan import UnitMove
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.tile import TerrainKind
from empire.core.unit import Unit, UnitKind
from empire.pathfinding.cost import ARMY as ARMY_COST_PROFILE
from empire.pathfinding.distance_field import DistanceField, PassabilityGrid

# Weight tables (initial v1 — tuned by self-play). Scores are
# `weight / (distance + 1)` so closer objectives outrank farther ones.
W_ARMY_ENEMY_CITY = 200
W_ARMY_NEUTRAL_CITY = 150
W_ARMY_ENEMY_UNIT = 60
W_ARMY_EXPLORE = 10
# How far from a known frontier coord the army will actively scout.
ARMY_EXPLORE_RADIUS = 8


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A scored objective. The winner's path is reconstructed afterwards —
    scoring needs only the distance, which the unit's field answers in O(1)."""

    score: float
    target: Coord


@dataclass(frozen=True, slots=True)
class _ViewContext:
    """Per-planning-view shared state: the passability bitmap and the global
    frontier set. Both depend only on (map, fog snapshot), so every unit
    planned against the same `WorldView` shares one copy."""

    grid: PassabilityGrid
    frontier: frozenset[Coord]


class BaselineTactical:
    """Per-unit-kind decision dispatch.

    `decide` returns the unit's planned `UnitMove` for the turn. The returned
    path includes at most `unit.moves_this_turn()` cells (the engine
    re-validates and will truncate, but we trim here to keep plans tidy).

    A `_ViewContext` (passability grid + frontier set) is cached per planning
    view — `WorldView` objects are constructed fresh per controller call, so
    view identity scopes the cache to one consistent fog snapshot.
    """

    def __init__(self) -> None:
        self._ctx_view: WorldView | None = None
        self._ctx: _ViewContext | None = None

    def decide(self, unit: Unit, view: WorldView) -> UnitMove:
        if unit.kind is UnitKind.ARMY:
            return self._army_decide(unit, view)
        # Other unit kinds: stay put. They aren't produced under the baseline's
        # ARMY-only production policy, but the dispatcher must remain total.
        return self._sentry(unit)

    # ---- ARMY --------------------------------------------------------------

    def _army_decide(self, unit: Unit, view: WorldView) -> UnitMove:
        """Greedy ARMY policy: capture cities, then attack enemies, then explore.

        Scoring blends objective weight with inverse path length. The unit
        always emits a 1-cell step (Army speed = 1), which keeps replanning
        responsive to fog-of-war updates from the next turn's scan.
        """
        ctx = self._context_for(view)
        field = DistanceField(unit.coord, ctx.grid)
        candidates: list[_Candidate] = []

        # Enemy cities — highest weight, capture wins the game.
        for city in view.known_enemy_cities:
            cand = self._score_objective(field, city.coord, W_ARMY_ENEMY_CITY)
            if cand is not None:
                candidates.append(cand)

        # Neutral cities — production gain, slightly lower than enemy capture.
        for city in view.neutral_cities:
            cand = self._score_objective(field, city.coord, W_ARMY_NEUTRAL_CITY)
            if cand is not None:
                candidates.append(cand)

        # Enemy units we can fight (Army attack preferences are 'ATFPDCSB' —
        # land combat against another army is the relevant matchup here).
        for enemy in view.known_enemy_units:
            # Army can only step on land or city tiles. Filter out water
            # sightings (sub/patrol/etc.) — pathfinding would reject them
            # but it's cheaper to skip up front.
            terrain = self._terrain_for_view(view, enemy.snapshot.coord)
            if terrain is TerrainKind.WATER:
                continue
            cand = self._score_objective(field, enemy.snapshot.coord, W_ARMY_ENEMY_UNIT)
            if cand is not None:
                candidates.append(cand)

        # Exploration: pick a small set of frontier coords (unseen cells
        # adjacent to a seen tile within reach) and score them weakly.
        for frontier in self._frontier_candidates(unit, ctx):
            cand = self._score_objective(field, frontier, W_ARMY_EXPLORE)
            if cand is not None:
                candidates.append(cand)

        if not candidates:
            return self._sentry(unit)

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        # Reconstruct the winner's path from the unit's own field. The field
        # scored the target reachable, so this cannot miss.
        cells = field.path_to(best.target)
        if cells is None or len(cells) < 2:
            return self._sentry(unit)
        return self._step_along(unit, cells)

    # ---- shared helpers ---------------------------------------------------

    def _sentry(self, unit: Unit) -> UnitMove:
        return UnitMove(unit_id=unit.id)

    def _score_objective(
        self,
        field: DistanceField,
        target: Coord,
        weight: float,
    ) -> _Candidate | None:
        """Score `target` by inverse distance from the unit's field. Distances
        equal `find_path(...).steps` exactly (uniform ARMY costs), so scores
        match the per-pair search this replaced."""
        steps = field.steps_to(target)
        if steps is None or steps == 0:
            return None
        return _Candidate(score=weight / (steps + 1), target=target)

    def _context_for(self, view: WorldView) -> _ViewContext:
        """The shared per-view context, rebuilt when the view object changes."""
        if view is not self._ctx_view:
            grid = PassabilityGrid(
                view.real_map(), ARMY_COST_PROFILE, view.own_player.view,
            )
            self._ctx = _ViewContext(grid=grid, frontier=frontier_cells(view))
            self._ctx_view = view
        ctx = self._ctx
        assert ctx is not None  # set whenever _ctx_view is set
        return ctx

    def _step_along(self, unit: Unit, cells: tuple[Coord, ...]) -> UnitMove:
        """Emit the next `unit.moves_this_turn()` cells of the path (excl. start)."""
        budget = unit.moves_this_turn()
        # cells[0] == start; intended steps start at index 1.
        steps = cells[1 : 1 + budget]
        return UnitMove(
            unit_id=unit.id,
            path=tuple((c.x, c.y) for c in steps),
        )

    def _terrain_for_view(self, view: WorldView, c: Coord) -> TerrainKind | None:
        """Terrain at `c` from the player's view (shared helper re-export)."""
        return terrain_for_view(view, c)

    def _frontier_candidates(self, unit: Unit, ctx: _ViewContext) -> list[Coord]:
        """Sample frontier coords near `unit`, capped at 12, in the same
        box-scan order the original per-unit radius scan used (so the sample —
        and therefore behavior — is unchanged; only the per-cell recompute
        became a set lookup)."""
        cap = 12
        results: list[Coord] = []
        radius = ARMY_EXPLORE_RADIUS
        ux, uy = unit.coord.x, unit.coord.y
        frontier = ctx.frontier
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if len(results) >= cap:
                    return results
                c = Coord(ux + dx, uy + dy)
                if c in frontier:
                    results.append(c)
        return results

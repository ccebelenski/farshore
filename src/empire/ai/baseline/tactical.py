"""`BaselineTactical`: per-unit-kind decision functions.

The dispatcher (`BaselineAI`) calls `decide(unit, view)` for each owned unit
and gets back the cells the unit intends to enter this turn (1-step path
for ARMY/speed-1 units). Per-kind logic is split into dedicated methods so
weight tables and heuristics stay scoped to one unit type at a time.

Phase 9 ships the ARMY decision in full. Other unit kinds fall through to
`_sentry_decision` — BaselineAI's starting players only build Army, and
adding Fighter/Patrol/etc. is a follow-up that doesn't require rewriting
this scaffold.
"""

from __future__ import annotations

from dataclasses import dataclass

from empire.contracts.turn_plan import UnitMove
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.tile import TerrainKind
from empire.core.unit import Unit, UnitKind
from empire.pathfinding.bfs import BFSPathfinder
from empire.pathfinding.cost import ARMY as ARMY_COST_PROFILE
from empire.pathfinding.cost import PathCostProfile
from empire.pathfinding.pathfinder import Path

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
    """A scored objective with the path used to reach it."""

    score: float
    path: Path


class BaselineTactical:
    """Per-unit-kind decision dispatch.

    `decide` returns the unit's planned `UnitMove` for the turn. The returned
    path includes at most `unit.moves_this_turn()` cells (the engine
    re-validates and will truncate, but we trim here to keep plans tidy).
    """

    def __init__(self) -> None:
        self._bfs = BFSPathfinder()

    def decide(self, unit: Unit, view: WorldView) -> UnitMove:
        if unit.kind is UnitKind.ARMY:
            return self._army_decide(unit, view)
        # Other unit kinds: stay put. They aren't produced under Phase 9's
        # ARMY-only production policy, but the dispatcher must remain total.
        return self._sentry(unit)

    # ---- ARMY --------------------------------------------------------------

    def _army_decide(self, unit: Unit, view: WorldView) -> UnitMove:
        """Greedy ARMY policy: capture cities, then attack enemies, then explore.

        Scoring blends objective weight with inverse path length. The unit
        always emits a 1-cell step (Army speed = 1), which keeps replanning
        responsive to fog-of-war updates from the next turn's scan.
        """
        candidates: list[_Candidate] = []
        profile = ARMY_COST_PROFILE

        # Enemy cities — highest weight, capture wins the game.
        for city in view.known_enemy_cities:
            cand = self._score_objective(
                unit, city.coord, view, profile, W_ARMY_ENEMY_CITY,
            )
            if cand is not None:
                candidates.append(cand)

        # Neutral cities — production gain, slightly lower than enemy capture.
        for city in view.neutral_cities:
            cand = self._score_objective(
                unit, city.coord, view, profile, W_ARMY_NEUTRAL_CITY,
            )
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
            cand = self._score_objective(
                unit, enemy.snapshot.coord, view, profile, W_ARMY_ENEMY_UNIT,
            )
            if cand is not None:
                candidates.append(cand)

        # Exploration: pick a small set of frontier coords (unseen cells
        # adjacent to a seen tile within reach) and score them weakly.
        for frontier in self._frontier_candidates(unit, view):
            cand = self._score_objective(
                unit, frontier, view, profile, W_ARMY_EXPLORE,
            )
            if cand is not None:
                candidates.append(cand)

        if not candidates:
            return self._sentry(unit)

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        return self._step_along(unit, best.path)

    # ---- shared helpers ---------------------------------------------------

    def _sentry(self, unit: Unit) -> UnitMove:
        return UnitMove(unit_id=unit.id)

    def _score_objective(
        self,
        unit: Unit,
        target: Coord,
        view: WorldView,
        profile: PathCostProfile,
        weight: float,
    ) -> _Candidate | None:
        """BFS from `unit.coord` to `target`; score by inverse path length."""
        path = self._bfs.find_path(
            start=unit.coord,
            goal=target,
            real_map=view.real_map(),
            profile=profile,
            view=view.own_player.view,
        )
        if path is None or path.steps == 0:
            return None
        score = weight / (path.steps + 1)
        return _Candidate(score=score, path=path)

    def _step_along(self, unit: Unit, path: Path) -> UnitMove:
        """Emit the next `unit.moves_this_turn()` cells of `path` (excl. start)."""
        budget = unit.moves_this_turn()
        # path.cells[0] == start; intended steps start at index 1.
        steps = path.cells[1 : 1 + budget]
        return UnitMove(
            unit_id=unit.id,
            path=tuple((c.x, c.y) for c in steps),
        )

    def _terrain_for_view(self, view: WorldView, c: Coord) -> TerrainKind | None:
        """Terrain at `c` from the player's view: visible→real, remembered→last seen."""
        tile = view.terrain_at(c)
        return tile.terrain if tile is not None else None

    def _frontier_candidates(
        self, unit: Unit, view: WorldView,
    ) -> list[Coord]:
        """Sample frontier coords near `unit` — seen-but-walkable cells whose
        unseen neighbors invite exploration.

        We pick the seen-cell side rather than the unseen-cell side so the
        BFS goal is guaranteed reachable through known terrain. Sampling
        stays small (cap at 12) to keep the per-unit cost bounded.
        """
        seen = view.own_player.view.seen
        cap = 12
        results: list[Coord] = []
        radius = ARMY_EXPLORE_RADIUS
        ux, uy = unit.coord.x, unit.coord.y
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if len(results) >= cap:
                    return results
                c = Coord(ux + dx, uy + dy)
                if not view.in_bounds(c):
                    continue
                if not seen(c):
                    continue
                terrain = self._terrain_for_view(view, c)
                if terrain is None or terrain is TerrainKind.WATER:
                    continue
                # Frontier cell = at least one unseen on-board 8-neighbor.
                has_unseen_neighbor = False
                for n in c.neighbors():
                    if view.in_bounds(n) and not seen(n):
                        has_unseen_neighbor = True
                        break
                if has_unseen_neighbor:
                    results.append(c)
        return results

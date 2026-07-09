"""`Pathfinder` ABC and `Path` value type.

The ABC implements the generic best-first search with a pluggable heuristic;
`BFSPathfinder._heuristic` returns 0 (uniform-cost search / Dijkstra).
"""

from __future__ import annotations

import heapq
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from empire.core.coord import Coord
from empire.core.map import Map, ViewMap
from empire.core.tile import TerrainKind
from empire.pathfinding.cost import PathCostProfile

ThreatFn = Callable[[Coord], float]


@dataclass(frozen=True, slots=True)
class Path:
    """An ordered sequence of cells from start to goal (inclusive)."""

    cells: tuple[Coord, ...]
    total_cost: int

    @property
    def length(self) -> int:
        """Number of cells in the path (including start)."""
        return len(self.cells)

    @property
    def steps(self) -> int:
        """Number of moves (transitions between cells)."""
        return max(0, len(self.cells) - 1)


class Pathfinder(ABC):
    """A* with a pluggable heuristic.

    Subclasses just override `_heuristic`. `find_path` is shared.
    """

    def find_path(
        self,
        start: Coord,
        goal: Coord,
        real_map: Map,
        profile: PathCostProfile,
        view: ViewMap | None = None,
        threat_at: ThreatFn | None = None,
    ) -> Path | None:
        """Find a minimum-cost path from `start` to `goal`.

        Returns `None` if no path exists. Returns a single-cell path
        (just `start`) if start == goal.

        Parameters
        ----------
        view :
            If provided, cells not in `view.seen()` use `profile.unknown_cost`
            (and are impassable if that's `None`). If omitted, all cells are
            treated as fully known and use their terrain's actual cost.
        threat_at :
            Optional callable returning a per-cell threat value used with
            `profile.danger_weight`. Threats are added to the per-cell cost
            scaled by the weight (rounded). If omitted, no danger weighting.
        """
        if start == goal:
            return Path(cells=(start,), total_cost=0)

        # Straightness tiebreak: among equal-cost candidates, prefer cells
        # nearer the straight start->goal line (integer cross-product
        # magnitude). 8-way grids admit MANY equal-cost paths; without this
        # the winner is expansion-order luck and can wander far off the
        # line — step-optimal but visually "the long way around". Pure
        # tie-breaking: cost optimality is untouched.
        line_dx, line_dy = goal.x - start.x, goal.y - start.y

        def deviation(c: Coord) -> int:
            return abs((c.x - start.x) * line_dy - (c.y - start.y) * line_dx)

        # Open set: priority queue keyed on (f, h, deviation, tiebreaker,
        # coord) — smaller remaining-cost first among equal f (drives
        # goal-ward), then line-hugging, then insertion order (prevents
        # heapq from comparing Coord objects).
        counter = 0
        open_heap: list[tuple[int, int, int, int, Coord]] = [
            (0, self._heuristic(start, goal), deviation(start), counter, start)
        ]
        counter += 1

        came_from: dict[Coord, Coord] = {}
        g_score: dict[Coord, int] = {start: 0}

        while open_heap:
            _, _, _, _, current = heapq.heappop(open_heap)

            if current == goal:
                return self._reconstruct(came_from, current, g_score[goal])

            for neighbor in current.neighbors():
                if not real_map.in_bounds(neighbor):
                    continue
                if not real_map.tile(neighbor).on_board:
                    continue  # the unwalkable 1-cell border ring

                # Determine cell cost.
                if view is not None and not view.seen(neighbor):
                    if profile.unknown_cost is None:
                        continue  # impassable when unknown
                    cell_cost: int = profile.unknown_cost
                else:
                    terrain: TerrainKind = real_map.terrain_at(neighbor)
                    base = profile.cost_for(terrain)
                    if base is None:
                        continue  # impassable terrain for this unit
                    cell_cost = base

                # Danger overlay.
                if threat_at is not None and profile.danger_weight > 0.0:
                    cell_cost += round(profile.danger_weight * threat_at(neighbor))

                tentative_g = g_score[current] + cell_cost
                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    h = self._heuristic(neighbor, goal)
                    heapq.heappush(
                        open_heap,
                        (tentative_g + h, h, deviation(neighbor), counter, neighbor),
                    )
                    counter += 1

        return None

    @abstractmethod
    def _heuristic(self, c: Coord, goal: Coord) -> int:
        """Estimated minimum remaining cost from `c` to `goal`.

        Must be admissible: never overestimate true remaining cost.
        Returning 0 reduces A* to uniform-cost search (Dijkstra).
        """

    @staticmethod
    def _reconstruct(
        came_from: dict[Coord, Coord], end: Coord, total_cost: int,
    ) -> Path:
        cells: list[Coord] = [end]
        current = end
        while current in came_from:
            current = came_from[current]
            cells.append(current)
        cells.reverse()
        return Path(cells=tuple(cells), total_cost=total_cost)

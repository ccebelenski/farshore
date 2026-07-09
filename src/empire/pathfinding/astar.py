"""`AStarPathfinder`: A* with the Chebyshev-distance heuristic.

Admissible for every profile this game uses: all passable cells cost >= 1
(danger weighting only ADDS cost) and one step covers at most one Chebyshev
unit, so `chebyshev(c, goal) <= true remaining cost` always — A* returns the
same minimum-cost paths as Dijkstra, it just expands far fewer cells and,
with the base class's goal-ward/line-hugging tiebreaks, picks the straight
ones among equal-cost alternatives. Use for point-to-point routes (go-to,
patrol legs); keep `BFSPathfinder` where a heuristic is meaningless
(frontier floods).
"""

from empire.core.coord import Coord
from empire.pathfinding.pathfinder import Pathfinder


class AStarPathfinder(Pathfinder):
    """A* under the Chebyshev heuristic (admissible on 8-way unit-cost grids)."""

    def _heuristic(self, c: Coord, goal: Coord) -> int:
        return c.chebyshev_to(goal)

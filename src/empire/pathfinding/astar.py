"""`AStarPathfinder`: A* with Chebyshev-distance heuristic.

Faster than `BFSPathfinder` for point-to-point queries on maps with
uniform-ish costs, because the heuristic focuses the search toward the
goal. The Chebyshev heuristic is admissible when each step costs >= 1
(which `PathCostProfile` defaults satisfy).
"""

from empire.core.coord import Coord
from empire.pathfinding.pathfinder import Pathfinder


class AStarPathfinder(Pathfinder):
    """A* with Chebyshev-distance heuristic, admissible for step cost >= 1."""

    def _heuristic(self, c: Coord, goal: Coord) -> int:
        return c.chebyshev_to(goal)

"""`BFSPathfinder`: uniform-cost search (Dijkstra) via A* with heuristic = 0.

When the cost profile uses uniform costs (1 per cell), this is equivalent
to a pure BFS. With varied costs, it's a min-cost search. Use when:
- A* heuristic would be too aggressive (e.g., danger fields make A*
  prune cells that turn out to be necessary).
- The goal isn't a single point but a frontier search where the
  heuristic isn't meaningful.
"""

from empire.core.coord import Coord
from empire.pathfinding.pathfinder import Pathfinder


class BFSPathfinder(Pathfinder):
    """Uniform-cost search. Zero heuristic = Dijkstra over terrain costs."""

    def _heuristic(self, c: Coord, goal: Coord) -> int:
        del c, goal
        return 0

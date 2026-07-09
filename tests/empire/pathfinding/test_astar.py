"""`AStarPathfinder` (go-to routing): cost-optimal like Dijkstra, but picks
the straight path among equal-cost alternatives (playtest: 'go-to wanders a
long way off the optimal line')."""

from empire.core.coord import Coord
from empire.pathfinding.astar import AStarPathfinder
from empire.pathfinding.bfs import BFSPathfinder
from empire.pathfinding.cost import ARMY, SEA
from tests.empire.support import build_map as _build_map


def _deviation_cap(cells: tuple[Coord, ...], start: Coord, goal: Coord) -> int:
    dx, dy = goal.x - start.x, goal.y - start.y
    return max(
        abs((c.x - start.x) * dy - (c.y - start.y) * dx) for c in cells
    )


def test_open_field_path_hugs_the_straight_line() -> None:
    """(0,0)->(8,3) on open land: optimal length AND every cell stays inside
    the start/goal bounding box, close to the ideal line — no staircase
    wander (a step-optimal path may otherwise dip far off the line)."""
    m = _build_map(["L" * 9] * 6)
    start, goal = Coord(0, 0), Coord(8, 3)
    p = AStarPathfinder().find_path(start, goal, m, ARMY)
    assert p is not None
    assert p.steps == 8  # chebyshev-optimal
    for c in p.cells:
        assert 0 <= c.x <= 8 and 0 <= c.y <= 3  # never leaves the box
    # Hugs the line: max integer cross-product deviation stays under one
    # driving-axis unit (~= within one cell of the ideal line).
    assert _deviation_cap(p.cells, start, goal) <= 8


def test_matches_dijkstra_cost_around_obstacles() -> None:
    """A* is exact: same minimum cost as BFS/Dijkstra when terrain forces a
    detour (heuristic is admissible, never trades cost for shape)."""
    rows = [
        "LLWLL",
        "LLWLL",
        "LLWLL",
        "LLWLL",
        "LLLLL",
    ]
    m = _build_map(rows)
    a = AStarPathfinder().find_path(Coord(0, 0), Coord(4, 0), m, ARMY)
    b = BFSPathfinder().find_path(Coord(0, 0), Coord(4, 0), m, ARMY)
    assert a is not None and b is not None
    assert a.total_cost == b.total_cost
    assert a.cells[-1] == Coord(4, 0)


def test_sea_path_straight_across_open_water() -> None:
    m = _build_map(["W" * 8] * 8)
    p = AStarPathfinder().find_path(Coord(0, 7), Coord(7, 0), m, SEA)
    assert p is not None
    assert p.steps == 7  # pure diagonal
    assert all(c.x + c.y == 7 for c in p.cells)  # exactly the anti-diagonal

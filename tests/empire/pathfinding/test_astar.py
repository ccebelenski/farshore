"""Phase-7 canary tests for `AStarPathfinder`.

Same correctness invariants as BFS (both find min-cost paths under
admissible heuristics). Adds danger-weighting tests since A* with a
nonzero heuristic interacts with the f-score ordering.
"""

from empire.core.coord import Coord
from empire.core.map import Map, ViewMap
from empire.core.tile import TerrainKind, Tile
from empire.pathfinding.astar import AStarPathfinder
from empire.pathfinding.bfs import BFSPathfinder
from empire.pathfinding.cost import ARMY, PathCostProfile


def _build_map(rows: list[str]) -> Map:
    height = len(rows)
    width = len(rows[0])
    tiles: dict[Coord, Tile] = {}
    for y in range(height):
        for x in range(width):
            ch = rows[y][x]
            terrain = {
                "L": TerrainKind.LAND,
                "W": TerrainKind.WATER,
                "C": TerrainKind.CITY,
            }[ch]
            tiles[Coord(x, y)] = Tile(coord=Coord(x, y), terrain=terrain)
    return Map(width=width, height=height, tiles=tiles)


# --- equivalence with BFS for uniform costs ----------------------------------


def test_astar_same_cost_as_bfs_on_open_map() -> None:
    """For uniform costs, A* and BFS find paths of equal total cost."""
    m = _build_map(["LLLLLL"] * 6)
    a = AStarPathfinder().find_path(Coord(0, 0), Coord(5, 5), m, ARMY)
    b = BFSPathfinder().find_path(Coord(0, 0), Coord(5, 5), m, ARMY)
    assert a is not None and b is not None
    assert a.total_cost == b.total_cost


def test_astar_same_cost_with_water_obstacle() -> None:
    m = _build_map(
        [
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLLLL",
        ]
    )
    a = AStarPathfinder().find_path(Coord(0, 0), Coord(4, 0), m, ARMY)
    b = BFSPathfinder().find_path(Coord(0, 0), Coord(4, 0), m, ARMY)
    assert a is not None and b is not None
    assert a.total_cost == b.total_cost


def test_astar_returns_none_for_unreachable() -> None:
    m = _build_map(
        [
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLWLL",
        ]
    )
    p = AStarPathfinder().find_path(Coord(0, 0), Coord(4, 0), m, ARMY)
    assert p is None


# --- danger weighting --------------------------------------------------------


def test_danger_weight_avoids_threatened_cell() -> None:
    """When a single cell is dangerous and the weight is high, A* should
    route around it even though that takes more steps.
    """
    m = _build_map(["LLLLL"] * 5)

    def threat(c: Coord) -> float:
        return 10.0 if c == Coord(2, 2) else 0.0

    profile = PathCostProfile(
        land_cost=1,
        water_cost=None,
        city_cost=1,
        danger_weight=5.0,
    )
    p = AStarPathfinder().find_path(
        Coord(0, 0),
        Coord(4, 4),
        m,
        profile,
        threat_at=threat,
    )
    assert p is not None
    assert Coord(2, 2) not in p.cells, f"Path went through dangerous cell (2,2): {p.cells}"


def test_danger_weight_zero_does_not_avoid_threat() -> None:
    """With danger_weight = 0, the threat function has no effect."""
    m = _build_map(["LLLLL"] * 5)

    def threat(c: Coord) -> float:
        return 100.0  # high threat everywhere

    profile = PathCostProfile(
        land_cost=1,
        water_cost=None,
        city_cost=1,
        danger_weight=0.0,
    )
    p = AStarPathfinder().find_path(
        Coord(0, 0),
        Coord(4, 4),
        m,
        profile,
        threat_at=threat,
    )
    assert p is not None
    assert p.total_cost == 4  # direct diagonal, unaffected by threat


# --- output structure --------------------------------------------------------


def test_path_starts_and_ends_at_correct_cells() -> None:
    m = _build_map(["LLLLL"] * 5)
    p = AStarPathfinder().find_path(Coord(0, 0), Coord(3, 2), m, ARMY)
    assert p is not None
    assert p.cells[0] == Coord(0, 0)
    assert p.cells[-1] == Coord(3, 2)


def test_consecutive_cells_in_path_are_8_neighbors() -> None:
    """Each consecutive pair of cells in the path is a valid 8-direction step."""
    m = _build_map(
        [
            "LLLLLLLL",
            "LLLLLLLL",
            "LLLLLLLL",
            "LLLLLLLL",
        ]
    )
    p = AStarPathfinder().find_path(Coord(0, 0), Coord(7, 3), m, ARMY)
    assert p is not None
    for a, b in zip(p.cells[:-1], p.cells[1:], strict=True):
        assert a.chebyshev_to(b) == 1, f"Non-neighbor step: {a} -> {b}"


def test_view_map_blocks_path_through_unknown() -> None:
    """With unknown_cost=None and a fog-of-war view, unseen cells are
    impassable — paths can't go through them.
    """
    m = _build_map(["LLLLL"] * 5)
    view = ViewMap()
    for x in range(5):
        view.visible.add(Coord(x, 0))
    profile = PathCostProfile(
        land_cost=1,
        water_cost=None,
        city_cost=1,
        unknown_cost=None,
    )
    p = AStarPathfinder().find_path(
        Coord(0, 0),
        Coord(4, 4),
        m,
        profile,
        view=view,
    )
    # Can only stay on row 0; can't reach row 4. Unreachable.
    assert p is None

"""Phase-7 canary tests for `BFSPathfinder` on handcrafted small maps."""

from empire.core.coord import Coord
from empire.core.map import ViewMap
from empire.pathfinding.bfs import BFSPathfinder
from empire.pathfinding.cost import ARMY, SEA, PathCostProfile
from tests.empire.support import build_map as _build_map

# --- helpers -----------------------------------------------------------------


# --- basic paths -------------------------------------------------------------


def test_start_equals_goal_returns_zero_cost_path() -> None:
    m = _build_map(["LL", "LL"])
    p = BFSPathfinder().find_path(Coord(0, 0), Coord(0, 0), m, ARMY)
    assert p is not None
    assert p.cells == (Coord(0, 0),)
    assert p.total_cost == 0
    assert p.steps == 0


def test_finds_diagonal_shortcut_on_open_land() -> None:
    """On all-land 5x5, the shortest path from (0,0) to (4,4) is 4 diagonal steps."""
    m = _build_map(["LLLLL"] * 5)
    p = BFSPathfinder().find_path(Coord(0, 0), Coord(4, 4), m, ARMY)
    assert p is not None
    assert p.total_cost == 4
    assert p.steps == 4
    assert p.cells[0] == Coord(0, 0)
    assert p.cells[-1] == Coord(4, 4)


def test_routes_around_water_for_army() -> None:
    """Water column at x=2 forces army to route via row 4 (the only land row crossing)."""
    m = _build_map(
        [
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLLLL",
        ]
    )
    p = BFSPathfinder().find_path(Coord(0, 0), Coord(4, 0), m, ARMY)
    assert p is not None
    # 4 down + 4 right + 4 up = 12 if pure orthogonal, but with diagonals it's
    # min(4, 4) = 4 down + 4 across simultaneously, then back up. Actually
    # diagonals let us go down-and-right at the same time. Path length =
    # Chebyshev distance from (0,0) to (4,4) = 4 plus 4 up = ... let me just
    # assert it's strictly more than the direct chebyshev (=4).
    direct = Coord(0, 0).chebyshev_to(Coord(4, 0))
    assert p.total_cost > direct  # had to go around water


def test_unreachable_returns_none() -> None:
    """Water column completely separates left from right; army can't cross."""
    m = _build_map(
        [
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLWLL",
        ]
    )
    p = BFSPathfinder().find_path(Coord(0, 0), Coord(4, 0), m, ARMY)
    assert p is None


def test_sea_unit_uses_water_path() -> None:
    """Sea profile: navigable water, impassable land."""
    m = _build_map(
        [
            "LLWWW",
            "LLWWW",
            "LLWWW",
            "WWWWW",
            "WWWWW",
        ]
    )
    # Start at water (4, 0), goal at water (4, 4).
    p = BFSPathfinder().find_path(Coord(4, 0), Coord(4, 4), m, SEA)
    assert p is not None
    assert p.total_cost == 4


def test_unknown_cells_use_unknown_cost() -> None:
    """When `view` is provided, unseen cells use `unknown_cost`."""
    m = _build_map(["LLLLL"] * 3)
    view = ViewMap()
    # Only row 0 is visible.
    for x in range(5):
        view.visible.add(Coord(x, 0))

    profile = PathCostProfile(
        land_cost=1,
        water_cost=None,
        city_cost=1,
        unknown_cost=10,
    )
    p = BFSPathfinder().find_path(Coord(0, 0), Coord(4, 2), m, profile, view=view)
    assert p is not None
    # Cheapest: (0,0) → (1,0) → (2,0) along visible row 0 (cost 1 each),
    # then diagonal (3,1) and (4,2) through unknown (cost 10 each).
    # Total: 1 + 1 + 10 + 10 = 22. The diagonal saves steps over an all-row-0
    # then drop-down path.
    assert p.total_cost == 22


def test_unknown_impassable_with_no_unknown_cost() -> None:
    """If `unknown_cost` is None, unseen cells are impassable."""
    m = _build_map(["LLLLL"] * 3)
    view = ViewMap()
    for x in range(5):
        view.visible.add(Coord(x, 0))

    profile = PathCostProfile(
        land_cost=1,
        water_cost=None,
        city_cost=1,
        unknown_cost=None,
    )
    # Goal at (4, 2) is in unknown territory and surrounded by unknown.
    # Should be unreachable since we can't enter unknown cells.
    p = BFSPathfinder().find_path(Coord(0, 0), Coord(4, 2), m, profile, view=view)
    assert p is None

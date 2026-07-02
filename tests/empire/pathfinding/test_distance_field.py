"""`DistanceField` exactness: field distances must equal `find_path` steps.

The equivalence property is what `BaselineTactical` relies on — its scoring
switched from per-(unit, target) BFS to one flood per unit on the guarantee
that distances (hence scores and chosen objectives) are numerically
identical (Phase 15.8 Step 0). `path_to` must produce a *valid* shortest
path; its equal-length route tie-breaks may differ from `find_path`'s.
"""

import itertools
import random

import pytest

from empire.core.coord import Coord
from empire.core.map import Map, ViewMap
from empire.core.ruleset import MapProfile
from empire.mapgen.height_field import HeightFieldMapGenerator
from empire.pathfinding.bfs import BFSPathfinder
from empire.pathfinding.cost import ARMY, SEA, PathCostProfile
from empire.pathfinding.distance_field import DistanceField, PassabilityGrid
from tests.empire.support import build_map as _build_map

# --- helpers -----------------------------------------------------------------


def _assert_field_matches_find_path(
    m: Map, origin: Coord, view: ViewMap | None = None
) -> None:
    """For every goal cell != origin: field steps == find_path steps, and
    path_to is a valid path of exactly that length."""
    grid = PassabilityGrid(m, ARMY, view)
    field = DistanceField(origin, grid)
    bfs = BFSPathfinder()
    for y in range(m.height):
        for x in range(m.width):
            goal = Coord(x, y)
            if goal == origin:
                continue
            path = bfs.find_path(origin, goal, m, ARMY, view=view)
            steps = field.steps_to(goal)
            if path is None:
                assert steps is None, f"{goal}: field {steps}, find_path None"
                assert field.path_to(goal) is None
            else:
                assert steps == path.steps, f"{goal}: field {steps}, path {path.steps}"
                _assert_valid_shortest_path(field, grid, origin, goal)


def _assert_valid_shortest_path(
    field: DistanceField, grid: PassabilityGrid, origin: Coord, goal: Coord
) -> None:
    cells = field.path_to(goal)
    steps = field.steps_to(goal)
    assert cells is not None and steps is not None
    assert cells[0] == origin and cells[-1] == goal
    assert len(cells) - 1 == steps  # exactly shortest length
    for a, b in itertools.pairwise(cells):
        assert a.chebyshev_to(b) == 1  # contiguous 8-dir steps
        assert grid.is_passable(b)  # every entered cell is passable


# --- hand-built canaries -------------------------------------------------------


def test_open_field_distances_are_chebyshev() -> None:
    m = _build_map(["LLLLL"] * 5)
    field = DistanceField(Coord(2, 2), PassabilityGrid(m, ARMY))
    for y in range(5):
        for x in range(5):
            assert field.steps_to(Coord(x, y)) == Coord(x, y).chebyshev_to(Coord(2, 2))


def test_water_detour_and_unreachable() -> None:
    m = _build_map(
        [
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLWLL",
            "LLLLL",
        ]
    )
    field = DistanceField(Coord(0, 0), PassabilityGrid(m, ARMY))
    # Must route around the water column via row 4.
    steps = field.steps_to(Coord(4, 0))
    assert steps is not None
    assert steps > Coord(0, 0).chebyshev_to(Coord(4, 0))

    sealed = _build_map(["LLWLL"] * 5)
    field2 = DistanceField(Coord(0, 0), PassabilityGrid(sealed, ARMY))
    assert field2.steps_to(Coord(4, 0)) is None
    assert field2.path_to(Coord(4, 0)) is None


def test_impassable_goal_is_unreachable() -> None:
    """find_path can never *enter* an impassable goal; the field mirrors that."""
    m = _build_map(["LLWLL"] * 3)
    field = DistanceField(Coord(0, 1), PassabilityGrid(m, ARMY))
    assert field.steps_to(Coord(2, 1)) is None  # the water column


def test_impassable_origin_still_floods() -> None:
    """find_path never checks its start cell; neither does the flood. (An army
    can't actually stand on water, but the contract mirrors find_path.)"""
    m = _build_map(["LLWLL"] * 3)
    field = DistanceField(Coord(2, 1), PassabilityGrid(m, ARMY))  # water origin
    assert field.steps_to(Coord(1, 1)) == 1
    assert field.steps_to(Coord(3, 0)) == 1


def test_origin_cell_itself_is_zero() -> None:
    m = _build_map(["LLL"])
    field = DistanceField(Coord(1, 0), PassabilityGrid(m, ARMY))
    assert field.steps_to(Coord(1, 0)) == 0
    assert field.path_to(Coord(1, 0)) == (Coord(1, 0),)


def test_off_board_queries_return_none() -> None:
    m = _build_map(["LLL"])
    field = DistanceField(Coord(1, 0), PassabilityGrid(m, ARMY))
    assert field.steps_to(Coord(-1, 0)) is None
    assert field.steps_to(Coord(3, 5)) is None


def test_fog_unknown_passable_for_army_profile() -> None:
    """ARMY's unknown_cost=1: unseen cells are walkable in both implementations."""
    m = _build_map(["LLLLL"] * 3)
    view = ViewMap()
    for x in range(5):
        view.visible.add(Coord(x, 0))  # only row 0 seen
    _assert_field_matches_find_path(m, Coord(0, 0), view)


def test_fog_hides_water_consistently() -> None:
    """Unseen water is optimistically passable (unknown_cost) — exactly as
    find_path treats it. Seen water is impassable in both."""
    m = _build_map(
        [
            "LLWLL",
            "LLWLL",
            "LLWLL",
        ]
    )
    # See everything (water column blocks)...
    full = ViewMap()
    for y in range(3):
        for x in range(5):
            full.visible.add(Coord(x, y))
    _assert_field_matches_find_path(m, Coord(0, 1), full)
    # ...vs see only the left bank (unknown column is hopefully-walkable).
    partial = ViewMap()
    for y in range(3):
        for x in range(2):
            partial.visible.add(Coord(x, y))
    _assert_field_matches_find_path(m, Coord(0, 1), partial)


def test_non_uniform_profile_rejected() -> None:
    m = _build_map(["LL"])
    weighted = PathCostProfile(land_cost=1, water_cost=None, city_cost=1, unknown_cost=10)
    with pytest.raises(ValueError, match="uniform-cost"):
        PassabilityGrid(m, weighted)


def test_sea_profile_supported() -> None:
    """SEA is also uniform (water 1, city 1, land None) — fields work for ships."""
    m = _build_map(
        [
            "LLWWW",
            "LLWWW",
            "WWWWW",
        ]
    )
    grid = PassabilityGrid(m, SEA)
    field = DistanceField(Coord(4, 0), grid)
    assert field.steps_to(Coord(0, 2)) is not None  # along the open water
    assert field.steps_to(Coord(0, 0)) is None  # land is impassable for ships


# --- generated-map equivalence sweep ------------------------------------------


def test_equivalence_on_generated_map() -> None:
    """Full-board equivalence on a real generated map, fogless and fogged."""
    profile = MapProfile(
        width=24, height=16, water_ratio=40, smooth_iterations=5,
        num_cities=8, min_city_distance=3,
    )
    rng = random.Random(11)
    m, cities = HeightFieldMapGenerator().generate(profile, rng)
    origins = [c.coord for c in cities[:3]]

    for origin in origins:
        _assert_field_matches_find_path(m, origin, view=None)

    # Fogged: a player who has seen a random half of the board.
    view = ViewMap()
    for y in range(m.height):
        for x in range(m.width):
            if rng.random() < 0.5:
                view.visible.add(Coord(x, y))
    for origin in origins:
        _assert_field_matches_find_path(m, origin, view)

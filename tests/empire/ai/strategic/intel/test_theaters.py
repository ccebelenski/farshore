"""Canary tests for theater detection (Phase 11)."""

from __future__ import annotations

from empire.ai.strategic.intel.report import TheaterState
from empire.ai.strategic.intel.theaters import detect_theaters
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId
from tests.empire.ai.strategic.intel._world import grid_map, player, reveal_all, world


def test_two_islands_yield_two_theaters() -> None:
    """The headline gate: a two-island map produces exactly two theaters."""
    p1 = player(1)
    rmap = grid_map(
        [
            "..~~~..",
            "..~~~..",
            "..~~~..",
        ]
    )
    reveal_all(p1, rmap)

    theaters = detect_theaters(world(rmap, p1))
    assert len(theaters) == 2


def test_friendly_island_is_friendly_core() -> None:
    p1 = player(1)
    my_city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    rmap = grid_map(["..~~", "..~~"], cities={Coord(0, 0): my_city})
    reveal_all(p1, rmap)

    theaters = detect_theaters(world(rmap, p1))
    (land,) = [t for t in theaters if t.friendly_city_ids]
    assert land.state is TheaterState.FRIENDLY_CORE
    assert land.friendly_city_ids == (my_city.id,)


def test_island_with_both_sides_is_contested() -> None:
    p1 = player(1)
    p2 = player(2, "Enemy")
    mine = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    theirs = City(id=CityId(2), coord=Coord(2, 0), owner=p2)
    rmap = grid_map(["...", "..."], cities={mine.coord: mine, theirs.coord: theirs})
    reveal_all(p1, rmap)

    (theater,) = detect_theaters(world(rmap, p1))
    assert theater.state is TheaterState.CONTESTED
    assert mine.id in theater.friendly_city_ids
    assert theirs.id in theater.enemy_city_ids


def test_neutral_only_island_is_contested() -> None:
    p1 = player(1)
    neutral = City(id=CityId(9), coord=Coord(1, 0), owner=None)
    rmap = grid_map(["..."], cities={neutral.coord: neutral})
    reveal_all(p1, rmap)

    (theater,) = detect_theaters(world(rmap, p1))
    assert theater.state is TheaterState.CONTESTED
    assert theater.neutral_city_ids == (neutral.id,)

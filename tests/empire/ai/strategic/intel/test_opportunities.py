"""Canary tests for opportunity assessment (Phase 11)."""

from __future__ import annotations

from empire.ai.strategic.intel.opportunities import assess_opportunities
from empire.ai.strategic.intel.report import OpportunityKind
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, UnitId
from empire.core.unit import Army
from tests.empire.ai.strategic.intel._world import (
    grid_map,
    place,
    player,
    reveal,
    world,
)


def test_visible_neutral_city_is_an_opportunity() -> None:
    """A reachable neutral city yields a CAPTURE_NEUTRAL_CITY opportunity."""
    p1 = player(1)
    neutral = City(id=CityId(1), coord=Coord(5, 2), owner=None)
    rmap = grid_map(["." * 8 for _ in range(5)], cities={Coord(5, 2): neutral})
    place(rmap, Army(UnitId(1), p1, Coord(2, 2)))
    reveal(p1, Coord(2, 2), Coord(5, 2))

    opps = assess_opportunities(world(rmap, p1))

    capture = [o for o in opps if o.kind is OpportunityKind.CAPTURE_NEUTRAL_CITY]
    assert len(capture) == 1
    assert capture[0].target_city_id == neutral.id
    assert capture[0].distance == 3  # chebyshev from the army


def test_no_friendly_assets_means_no_opportunities() -> None:
    """With nothing to act with, even a visible neutral city is no opportunity."""
    p1 = player(1)
    neutral = City(id=CityId(2), coord=Coord(3, 3), owner=None)
    rmap = grid_map(["." * 6 for _ in range(6)], cities={Coord(3, 3): neutral})
    reveal(p1, Coord(3, 3))

    assert assess_opportunities(world(rmap, p1)) == ()


def test_nearer_target_outscores_farther_equal_target() -> None:
    """Distance discount: the closer of two neutral cities ranks first."""
    p1 = player(1)
    near = City(id=CityId(10), coord=Coord(3, 0), owner=None)
    far = City(id=CityId(11), coord=Coord(9, 0), owner=None)
    rmap = grid_map(["." * 12 for _ in range(2)], cities={near.coord: near, far.coord: far})
    place(rmap, Army(UnitId(1), p1, Coord(1, 0)))
    reveal(p1, Coord(1, 0), near.coord, far.coord)

    opps = assess_opportunities(world(rmap, p1))

    assert opps[0].target_city_id == near.id
    assert opps[0].score > opps[1].score


def test_enemy_city_and_visible_enemy_unit_are_opportunities() -> None:
    p1 = player(1)
    p2 = player(2, "Enemy")
    enemy_city = City(id=CityId(20), coord=Coord(6, 1), owner=p2)
    rmap = grid_map(["." * 9 for _ in range(4)], cities={enemy_city.coord: enemy_city})
    place(rmap, Army(UnitId(1), p1, Coord(1, 1)))
    place(rmap, Army(UnitId(2), p2, Coord(4, 1)))
    reveal(p1, Coord(1, 1), Coord(4, 1), enemy_city.coord)

    kinds = {o.kind for o in assess_opportunities(world(rmap, p1))}
    assert OpportunityKind.CAPTURE_ENEMY_CITY in kinds
    assert OpportunityKind.ATTACK_ENEMY_UNIT in kinds

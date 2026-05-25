"""Spot-checks for `BaselineAI` per planning/05-implementation-plan.md Phase 9.

These are *behavioral* tests against canned `WorldView` situations — they
assert the AI makes the intuitively-correct decision, not specific score
values. Weight tuning may shift the latter; the former should be invariant.
"""

from __future__ import annotations

from empire.ai.baseline import BaselineAI
from empire.contracts.surprise import BlockedBy, PathBlocked, TargetLost
from empire.contracts.world_view import WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army


def _all_land_map(w: int, h: int, cities: dict[Coord, City] | None = None) -> Map:
    cities = cities or {}
    tiles: dict[Coord, Tile] = {}
    for x in range(w):
        for y in range(h):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.LAND)
    return Map(width=w, height=h, tiles=tiles)


def _player(pid: int, name: str) -> Player:
    return Player(id=PlayerId(pid), name=name, is_ai=True, view=ViewMap())


def test_army_moves_toward_adjacent_neutral_city() -> None:
    """An army next to a visible neutral city moves into it on the next step."""
    p1 = _player(1, "P1")
    city_coord = Coord(3, 2)
    neutral = City(id=CityId(1), coord=city_coord, owner=None)
    real_map = _all_land_map(8, 8, cities={city_coord: neutral})
    army_coord = Coord(2, 2)
    army = Army(UnitId(1), p1, army_coord)
    real_map.place_unit(army, army_coord)

    # P1 sees the army's neighborhood + the city.
    p1.view.visible.update({army_coord, city_coord, Coord(2, 1), Coord(2, 3)})

    view = WorldView(real_map=real_map, player=p1, turn=1, rules=STANDARD)
    plan = BaselineAI().plan_turn(view)
    assert len(plan.moves) == 1
    move = plan.moves[0]
    assert move.unit_id == army.id
    # First step of the path is the city cell.
    assert move.path[0] == (city_coord.x, city_coord.y)


def test_idle_owned_city_gets_production_order() -> None:
    """A captured city (no building set) receives an ARMY production order."""
    p1 = _player(1, "P1")
    city_coord = Coord(4, 4)
    city = City(id=CityId(7), coord=city_coord, owner=p1)
    real_map = _all_land_map(8, 8, cities={city_coord: city})
    p1.view.visible.add(city_coord)

    view = WorldView(real_map=real_map, player=p1, turn=1, rules=STANDARD)
    plan = BaselineAI().plan_turn(view)
    assert any(o.city_id == city.id for o in plan.production_orders), plan


def test_revise_move_returns_a_unitmove_for_known_unit() -> None:
    """`revise_move` re-plans against the live view and never crashes."""
    p1 = _player(1, "P1")
    neutral_coord = Coord(5, 2)
    neutral = City(id=CityId(2), coord=neutral_coord, owner=None)
    real_map = _all_land_map(8, 8, cities={neutral_coord: neutral})
    army = Army(UnitId(3), p1, Coord(2, 2))
    real_map.place_unit(army, army.coord)
    p1.view.visible.update({army.coord, neutral_coord})

    view = WorldView(real_map=real_map, player=p1, turn=1, rules=STANDARD)
    ai = BaselineAI()

    for surprise in (
        PathBlocked(blocked_at=Coord(3, 2), by=BlockedBy.TERRAIN),
        TargetLost(target_id=CityId(2)),
    ):
        out = ai.revise_move(army.id, surprise, view)
        assert out.unit_id == army.id  # No crash; returned a UnitMove.


def test_no_objectives_means_sentry() -> None:
    """With no visible cities/enemies/frontier, the army emits no move."""
    p1 = _player(1, "P1")
    real_map = _all_land_map(8, 8)
    army = Army(UnitId(9), p1, Coord(3, 3))
    real_map.place_unit(army, army.coord)
    # Visible set is exactly the army's cell — no neighbors, so no frontier.
    p1.view.visible.add(army.coord)

    view = WorldView(real_map=real_map, player=p1, turn=1, rules=STANDARD)
    plan = BaselineAI().plan_turn(view)
    assert plan.moves == ()

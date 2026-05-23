"""Phase-3 canary tests for `WorldView`. Headline: the visibility contract —
a hidden tile is not reachable through the view.
"""

from __future__ import annotations

import pytest

from empire.contracts.world_view import WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, RememberedTile, UnitSnapshot, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, UnitKind


def _build_land_map(
    width: int,
    height: int,
    cities: dict[Coord, City] | None = None,
) -> Map:
    cities = cities or {}
    tiles: dict[Coord, Tile] = {}
    for x in range(width):
        for y in range(height):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.LAND)
    return Map(width=width, height=height, tiles=tiles)


@pytest.fixture()
def two_player_setup() -> tuple[Map, Player, Player]:
    m = _build_land_map(8, 8)
    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    return m, p1, p2


# --- scalar context ----------------------------------------------------------


def test_world_view_exposes_player_and_turn(two_player_setup: tuple[Map, Player, Player]) -> None:
    m, p1, _ = two_player_setup
    wv = WorldView(real_map=m, player=p1, turn=7, rules=STANDARD)
    assert wv.own_player is p1
    assert wv.turn == 7
    assert wv.rules is STANDARD


# --- visibility contract (THE PHASE 3 HEADLINE CANARY) ----------------------


def test_visible_tiles_contains_only_visible_set(
    two_player_setup: tuple[Map, Player, Player],
) -> None:
    """A coord that is not in the player's `visible` set must NOT appear in
    `visible_tiles()`. This is the Phase 3 exit-gate guarantee.
    """
    m, p1, _ = two_player_setup
    p1.view.visible.add(Coord(0, 0))
    p1.view.visible.add(Coord(1, 1))
    wv = WorldView(real_map=m, player=p1, turn=0, rules=STANDARD)
    visible = wv.visible_tiles()
    assert set(visible.keys()) == {Coord(0, 0), Coord(1, 1)}
    assert Coord(5, 5) not in visible
    assert Coord(7, 7) not in visible


def test_is_visible_predicate(two_player_setup: tuple[Map, Player, Player]) -> None:
    m, p1, _ = two_player_setup
    p1.view.visible.add(Coord(3, 4))
    wv = WorldView(real_map=m, player=p1, turn=0, rules=STANDARD)
    assert wv.is_visible(Coord(3, 4)) is True
    assert wv.is_visible(Coord(0, 0)) is False


def test_remembered_tiles_exposes_only_remembered_set(
    two_player_setup: tuple[Map, Player, Player],
) -> None:
    m, p1, _ = two_player_setup
    p1.view.remembered[Coord(2, 2)] = RememberedTile(
        coord=Coord(2, 2), terrain=TerrainKind.LAND, remembered_at=3,
    )
    wv = WorldView(real_map=m, player=p1, turn=5, rules=STANDARD)
    remembered = wv.remembered_tiles()
    assert set(remembered.keys()) == {Coord(2, 2)}


# --- own assets --------------------------------------------------------------


def test_own_cities_filters_by_owner() -> None:
    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    city_a = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    city_b = City(id=CityId(2), coord=Coord(7, 7), owner=p2)
    m = _build_land_map(8, 8, cities={Coord(0, 0): city_a, Coord(7, 7): city_b})
    wv = WorldView(real_map=m, player=p1, turn=0, rules=STANDARD)
    own = wv.own_cities
    assert [c.id for c in own] == [CityId(1)]


def test_own_units_filters_by_owner(two_player_setup: tuple[Map, Player, Player]) -> None:
    m, p1, p2 = two_player_setup
    u1 = Army(UnitId(1), p1, Coord(0, 0))
    u2 = Army(UnitId(2), p2, Coord(0, 0))
    m.place_unit(u1, Coord(0, 0))
    m.place_unit(u2, Coord(5, 5))
    wv = WorldView(real_map=m, player=p1, turn=0, rules=STANDARD)
    own_unit_ids = [u.id for u in wv.own_units]
    assert own_unit_ids == [UnitId(1)]


# --- enemy / neutral cities --------------------------------------------------


def test_known_enemy_cities_requires_visibility_or_memory() -> None:
    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    enemy_city = City(id=CityId(5), coord=Coord(6, 6), owner=p2)
    m = _build_land_map(8, 8, cities={Coord(6, 6): enemy_city})
    wv = WorldView(real_map=m, player=p1, turn=0, rules=STANDARD)

    # Without visibility, the enemy city is hidden.
    assert wv.known_enemy_cities == []

    # Make the cell visible — now it appears.
    p1.view.visible.add(Coord(6, 6))
    assert [c.id for c in wv.known_enemy_cities] == [CityId(5)]

    # Move it to remembered — it still appears (stale-but-known).
    p1.view.visible.remove(Coord(6, 6))
    p1.view.remembered[Coord(6, 6)] = RememberedTile(
        coord=Coord(6, 6), terrain=TerrainKind.CITY, remembered_at=0,
    )
    assert [c.id for c in wv.known_enemy_cities] == [CityId(5)]


def test_neutral_cities_filters_correctly() -> None:
    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    neutral = City(id=CityId(99), coord=Coord(4, 4), owner=None)
    m = _build_land_map(8, 8, cities={Coord(4, 4): neutral})
    wv = WorldView(real_map=m, player=p1, turn=0, rules=STANDARD)
    assert wv.neutral_cities == []  # not visible yet
    p1.view.visible.add(Coord(4, 4))
    assert [c.id for c in wv.neutral_cities] == [CityId(99)]


# --- known enemy units -------------------------------------------------------


def test_known_enemy_units_from_visible(two_player_setup: tuple[Map, Player, Player]) -> None:
    m, p1, p2 = two_player_setup
    enemy = Army(UnitId(10), p2, Coord(0, 0))
    m.place_unit(enemy, Coord(2, 2))
    wv = WorldView(real_map=m, player=p1, turn=4, rules=STANDARD)

    # Hidden first.
    assert wv.known_enemy_units == []

    # Reveal the cell — visible enemy.
    p1.view.visible.add(Coord(2, 2))
    known = wv.known_enemy_units
    assert len(known) == 1
    assert known[0].snapshot.unit_id == UnitId(10)
    assert known[0].seen_at_turn == 4  # current turn


def test_known_enemy_units_from_remembered(
    two_player_setup: tuple[Map, Player, Player],
) -> None:
    """A unit snapshot in a RememberedTile (no longer visible) should appear with
    its original remembered_at turn — not the current turn."""
    m, p1, _ = two_player_setup
    snap = UnitSnapshot(
        unit_id=UnitId(99),
        kind=UnitKind.DESTROYER,
        owner_id=PlayerId(2),
        coord=Coord(3, 3),
        hits=2,
    )
    p1.view.remembered[Coord(3, 3)] = RememberedTile(
        coord=Coord(3, 3), terrain=TerrainKind.WATER, remembered_at=2, last_units=[snap],
    )
    wv = WorldView(real_map=m, player=p1, turn=6, rules=STANDARD)
    known = wv.known_enemy_units
    assert len(known) == 1
    assert known[0].snapshot.unit_id == UnitId(99)
    assert known[0].seen_at_turn == 2  # original sighting, not current


def test_own_units_excluded_from_known_enemy_units(
    two_player_setup: tuple[Map, Player, Player],
) -> None:
    m, p1, _ = two_player_setup
    own = Army(UnitId(7), p1, Coord(0, 0))
    m.place_unit(own, Coord(1, 1))
    p1.view.visible.add(Coord(1, 1))
    wv = WorldView(real_map=m, player=p1, turn=0, rules=STANDARD)
    assert wv.known_enemy_units == []

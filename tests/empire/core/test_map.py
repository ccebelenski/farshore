"""Phase-2 canary tests for `Map` and the `ViewMap` stub.

The headline canary is the spatial-index property test: after a long sequence
of random placements / moves / removes, `Map.units_at(c)` must agree with a
brute-force scan over every live unit. This catches any desync between
`Unit.coord` and the derived index.
"""

from __future__ import annotations

import random

import pytest

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army

# --- helpers ------------------------------------------------------------------


def _build_empty_map(width: int, height: int, terrain: TerrainKind = TerrainKind.LAND) -> Map:
    tiles: dict[Coord, Tile] = {}
    for x in range(width):
        for y in range(height):
            c = Coord(x, y)
            tiles[c] = Tile(coord=c, terrain=terrain)
    return Map(width=width, height=height, tiles=tiles)


@pytest.fixture()
def player() -> Player:
    return Player(id=PlayerId(1), name="P", is_ai=False, view=ViewMap())


# --- bounds + tile queries ----------------------------------------------------


def test_in_bounds() -> None:
    m = _build_empty_map(10, 10)
    assert m.in_bounds(Coord(0, 0))
    assert m.in_bounds(Coord(9, 9))
    assert not m.in_bounds(Coord(-1, 0))
    assert not m.in_bounds(Coord(10, 0))
    assert not m.in_bounds(Coord(0, 10))


def test_tile_and_terrain_lookup() -> None:
    m = _build_empty_map(5, 5, TerrainKind.WATER)
    assert m.terrain_at(Coord(2, 2)) is TerrainKind.WATER
    assert m.tile(Coord(2, 2)).coord == Coord(2, 2)


def test_neighbors_only_yields_in_bounds_tiles() -> None:
    m = _build_empty_map(3, 3)
    # Corner (0,0) has 3 in-bounds neighbors.
    corner_neighbors = list(m.neighbors(Coord(0, 0)))
    assert len(corner_neighbors) == 3
    # Center (1,1) has all 8.
    center_neighbors = list(m.neighbors(Coord(1, 1)))
    assert len(center_neighbors) == 8


# --- city iteration -----------------------------------------------------------


def test_cities_iterates_only_tiles_with_cities(player: Player) -> None:
    tiles: dict[Coord, Tile] = {}
    for x in range(3):
        for y in range(3):
            c = Coord(x, y)
            tiles[c] = Tile(coord=c, terrain=TerrainKind.LAND)
    city_a = City(id=CityId(1), coord=Coord(0, 0), owner=player)
    city_b = City(id=CityId(2), coord=Coord(2, 2), owner=None)
    tiles[Coord(0, 0)] = Tile(coord=Coord(0, 0), terrain=TerrainKind.CITY, city=city_a)
    tiles[Coord(2, 2)] = Tile(coord=Coord(2, 2), terrain=TerrainKind.CITY, city=city_b)
    m = Map(width=3, height=3, tiles=tiles)
    cities = list(m.cities())
    assert {c.id for c in cities} == {CityId(1), CityId(2)}
    assert m.city_by_id(CityId(1)) is city_a
    assert m.city_by_id(CityId(999)) is None


# --- unit placement and queries ----------------------------------------------


def test_place_unit_updates_coord_and_index(player: Player) -> None:
    m = _build_empty_map(5, 5)
    u = Army(UnitId(1), player, Coord(0, 0))
    m.place_unit(u, Coord(2, 3))
    assert u.coord == Coord(2, 3)
    assert tuple(m.units_at(Coord(2, 3))) == (u,)
    assert m.unit_by_id(UnitId(1)) is u
    assert tuple(m.units_at(Coord(0, 0))) == ()


def test_place_multiple_units_at_same_coord(player: Player) -> None:
    """Stacking discipline is enforced by RuleSet (Phase 8); Map itself does not reject."""
    m = _build_empty_map(5, 5)
    u1 = Army(UnitId(1), player, Coord(0, 0))
    u2 = Army(UnitId(2), player, Coord(0, 0))
    m.place_unit(u1, Coord(1, 1))
    m.place_unit(u2, Coord(1, 1))
    assert set(m.units_at(Coord(1, 1))) == {u1, u2}


def test_move_unit_updates_coord_and_index(player: Player) -> None:
    m = _build_empty_map(5, 5)
    u = Army(UnitId(1), player, Coord(0, 0))
    m.place_unit(u, Coord(1, 1))
    m.move_unit(u, Coord(3, 4))
    assert u.coord == Coord(3, 4)
    assert tuple(m.units_at(Coord(1, 1))) == ()
    assert tuple(m.units_at(Coord(3, 4))) == (u,)


def test_move_unit_to_same_coord_is_noop(player: Player) -> None:
    m = _build_empty_map(5, 5)
    u = Army(UnitId(1), player, Coord(0, 0))
    m.place_unit(u, Coord(2, 2))
    m.move_unit(u, Coord(2, 2))
    assert tuple(m.units_at(Coord(2, 2))) == (u,)


def test_remove_unit_drops_from_index(player: Player) -> None:
    m = _build_empty_map(5, 5)
    u = Army(UnitId(1), player, Coord(0, 0))
    m.place_unit(u, Coord(2, 2))
    m.remove_unit(u)
    assert tuple(m.units_at(Coord(2, 2))) == ()
    assert m.unit_by_id(UnitId(1)) is None


def test_all_units_iterator(player: Player) -> None:
    m = _build_empty_map(5, 5)
    u1 = Army(UnitId(1), player, Coord(0, 0))
    u2 = Army(UnitId(2), player, Coord(0, 0))
    m.place_unit(u1, Coord(0, 0))
    m.place_unit(u2, Coord(1, 1))
    assert set(m.all_units()) == {u1, u2}


# --- THE SPATIAL-INDEX PROPERTY TEST ----------------------------------------


def _brute_force_units_at(m: Map, c: Coord) -> set[UnitId]:
    """Reference: scan all live units, return those whose coord == c."""
    return {u.id for u in m.all_units() if u.coord == c}


def _index_units_at(m: Map, c: Coord) -> set[UnitId]:
    """What the spatial index says is at c."""
    return {u.id for u in m.units_at(c)}


def test_spatial_index_consistency_under_random_ops(player: Player) -> None:
    """After many random place/move/remove ops, the index must agree with truth.

    Headline canary for Phase 2. Runs >1000 ops per planning/05 Phase 2 exit
    gate ("Spatial-index property test runs ≥1000 random ops without desync").
    """
    rng = random.Random(0xE3417E)  # fixed seed for determinism
    width, height = 12, 12
    m = _build_empty_map(width, height)

    units: list[Army] = []
    next_id = 1
    n_ops = 1500

    for _ in range(n_ops):
        op = rng.choice(("place", "move", "remove"))

        if op == "place" or not units:
            c = Coord(rng.randrange(width), rng.randrange(height))
            u = Army(UnitId(next_id), player, Coord(0, 0))
            next_id += 1
            m.place_unit(u, c)
            units.append(u)
        elif op == "move":
            u = rng.choice(units)
            c = Coord(rng.randrange(width), rng.randrange(height))
            m.move_unit(u, c)
        else:  # remove
            u = rng.choice(units)
            m.remove_unit(u)
            units.remove(u)

        # After every op, verify the index against brute force at every cell.
        for x in range(width):
            for y in range(height):
                c = Coord(x, y)
                assert _index_units_at(m, c) == _brute_force_units_at(m, c), (
                    f"Index desync at {c} after op={op}"
                )


# --- ViewMap stub -------------------------------------------------------------


def test_view_map_stub_reports_everything_seen() -> None:
    """Phase-2 stub: ViewMap.seen() returns True everywhere. Replaced in Phase 8."""
    v = ViewMap()
    assert v.seen(Coord(0, 0)) is True
    assert v.seen(Coord(100, 100)) is True
    assert v.remembered == {}
    assert v.visible == set()

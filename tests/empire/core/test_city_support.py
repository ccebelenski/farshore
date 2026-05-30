"""City support limits: garrison / airbase / dry-dock (spec §5.4).

Covers the turn-end disband rule, the "armies can't enter a friendly city"
movement rule, and the dock load/unload restriction.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from empire.combat.resolver import CombatResolver
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.engine import (
    StepOutcome,
    disband_overcrowded_city_units,
    execute_unit_path,
    execute_unload,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import (
    Army,
    Carrier,
    Destroyer,
    Fighter,
    Satellite,
    Transport,
    Unit,
)

_TERRAIN = {"L": TerrainKind.LAND, "W": TerrainKind.WATER, "C": TerrainKind.CITY}


def _map(rows: list[str], cities: dict[Coord, City] | None = None) -> Map:
    cities = cities or {}
    height, width = len(rows), len(rows[0])
    tiles: dict[Coord, Tile] = {}
    for y in range(height):
        for x in range(width):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=_TERRAIN[rows[y][x]])
    return Map(width=width, height=height, tiles=tiles)


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


def _place(m: Map, u: Unit) -> Unit:
    m.place_unit(u, u.coord)
    return u


# --- turn-end disband --------------------------------------------------------


def test_army_resting_in_friendly_city_is_disbanded(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(1, 1), owner=p1)
    m = _map(["LLL", "LCL", "LLL"], cities={Coord(1, 1): city})
    army = _place(m, Army(UnitId(1), p1, Coord(1, 1)))

    disbanded = disband_overcrowded_city_units(p1, m)

    assert disbanded == (army.id,)
    assert m.unit_by_id(army.id) is None


def test_dry_dock_keeps_one_ship_disbands_the_newer(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CW", "WW"], cities={Coord(0, 0): city})
    first = _place(m, Destroyer(UnitId(10), p1, Coord(0, 0)))
    second = _place(m, Destroyer(UnitId(11), p1, Coord(0, 0)))

    disbanded = disband_overcrowded_city_units(p1, m)

    assert disbanded == (second.id,)  # newest (higher id) loses its berth
    assert m.unit_by_id(first.id) is not None


def test_disband_scraps_the_empty_produced_ship_keeping_the_loaded_one(
    p1: Player,
) -> None:
    """The over-limit ship is the freshly produced (empty) hull; the loaded
    ship keeps the berth and its cargo rides out the stay (spec §5.4)."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CW", "WW"], cities={Coord(0, 0): city})
    loaded = _place(m, Transport(UnitId(1), p1, Coord(0, 0)))
    army = _place(m, Army(UnitId(2), p1, Coord(0, 0)))
    m.load_cargo(loaded, army)
    produced = _place(m, Transport(UnitId(3), p1, Coord(0, 0)))  # fresh, empty

    disbanded = disband_overcrowded_city_units(p1, m)

    assert disbanded == (produced.id,)
    assert m.unit_by_id(loaded.id) is not None
    assert m.unit_by_id(army.id) is not None  # cargo not drowned
    assert loaded.cargo == [army.id]


def test_disband_never_scraps_a_loaded_ship_when_an_empty_one_is_present(
    p1: Player,
) -> None:
    """A disband must not route a loaded ship through the combat-sink path,
    even when the empty ship is the *older* one. Cargo-safety is by intent,
    not by id luck (plain id-order would scrap the loaded ship here)."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CW", "WW"], cities={Coord(0, 0): city})
    empty_old = _place(m, Transport(UnitId(1), p1, Coord(0, 0)))  # lower id, empty
    loaded_new = _place(m, Transport(UnitId(5), p1, Coord(0, 0)))  # higher id, loaded
    army = _place(m, Army(UnitId(6), p1, Coord(0, 0)))
    m.load_cargo(loaded_new, army)

    disbanded = disband_overcrowded_city_units(p1, m)

    assert disbanded == (empty_old.id,)  # empty scrapped despite the lower id
    assert m.unit_by_id(loaded_new.id) is not None
    assert m.unit_by_id(army.id) is not None  # cargo safe


def test_airbase_holds_eight_fighters_disbands_the_ninth(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL", "LL"], cities={Coord(0, 0): city})
    fighters = [_place(m, Fighter(UnitId(20 + i), p1, Coord(0, 0))) for i in range(9)]

    disbanded = disband_overcrowded_city_units(p1, m)

    assert disbanded == (fighters[-1].id,)  # only the 9th
    assert sum(1 for f in fighters if m.unit_by_id(f.id) is not None) == 8


def test_satellite_is_exempt_from_city_limits(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL", "LL"], cities={Coord(0, 0): city})
    sat = _place(m, Satellite(UnitId(30), p1, Coord(0, 0)))

    assert disband_overcrowded_city_units(p1, m) == ()
    assert m.unit_by_id(sat.id) is not None


def test_disband_only_touches_the_acting_players_cities(p1: Player, p2: Player) -> None:
    enemy_city = City(id=CityId(1), coord=Coord(1, 1), owner=p2)
    m = _map(["LLL", "LCL", "LLL"], cities={Coord(1, 1): enemy_city})
    enemy_army = _place(m, Army(UnitId(1), p2, Coord(1, 1)))

    # p1's turn-end disband must not reach into p2's city.
    assert disband_overcrowded_city_units(p1, m) == ()
    assert m.unit_by_id(enemy_army.id) is not None


# --- movement: armies may not enter a friendly city --------------------------


def test_army_cannot_step_into_friendly_city(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p1)
    m = _map(["LCL"], cities={Coord(1, 0): city})
    army = _place(m, Army(UnitId(1), p1, Coord(0, 0)))

    outcome = execute_unit_path(
        unit=army,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )

    assert outcome.last_outcome is StepOutcome.FRIENDLY_CITY
    assert army.coord == Coord(0, 0)  # did not move


def test_army_capturing_enemy_city_succeeds_then_disbands_at_turn_end(
    p1: Player, p2: Player
) -> None:
    enemy_city = City(id=CityId(1), coord=Coord(1, 0), owner=p2)
    m = _map(["LCL"], cities={Coord(1, 0): enemy_city})
    army = _place(m, Army(UnitId(1), p1, Coord(0, 0)))
    deterministic = replace(STANDARD, army_capture_city_deterministic=True)

    outcome = execute_unit_path(
        unit=army,
        path=((1, 0),),
        real_map=m,
        rules=deterministic,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )

    # Capture is unaffected by the friendly-city rule (city was enemy at move time).
    assert enemy_city.id in outcome.cities_captured
    assert enemy_city.owner is p1
    assert army.coord == Coord(1, 0)

    # Now it sits in a (newly) friendly city, out of moves → disbanded next turn-end.
    assert disband_overcrowded_city_units(p1, m) == (army.id,)
    assert m.unit_by_id(army.id) is None


# --- dock load/unload restriction --------------------------------------------


def test_fighter_cannot_load_onto_carrier_in_dry_dock(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CW", "WW"], cities={Coord(0, 0): city})
    carrier = _place(m, Carrier(UnitId(1), p1, Coord(0, 0)))
    fighter = _place(m, Fighter(UnitId(2), p1, Coord(1, 0)))

    outcome = execute_unit_path(
        unit=fighter,
        path=((0, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )

    # Not a load — blocked by the friendly carrier instead; nothing went aboard.
    assert outcome.last_outcome is StepOutcome.BLOCKED_BY_FRIENDLY
    assert carrier.cargo == []
    assert not fighter.is_aboard()


def test_transport_in_dry_dock_cannot_unload(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL", "WL"], cities={Coord(0, 0): city})
    transport = _place(m, Transport(UnitId(1), p1, Coord(0, 0)))
    army = _place(m, Army(UnitId(2), p1, Coord(0, 0)))
    m.load_cargo(transport, army)
    army.loaded_this_turn = False  # allow unloading by rule timing

    outcome = execute_unload(
        cargo=army,
        to=Coord(1, 1),  # adjacent land
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )

    assert outcome.last_outcome is StepOutcome.ILLEGAL
    assert army.is_aboard()  # stayed aboard
    assert transport.cargo == [army.id]

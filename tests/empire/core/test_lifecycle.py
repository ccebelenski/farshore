"""Phase 10.8 — deferred unit lifecycle: fighter fuel (§3.5), satellite
orbit/lifetime (§2.4), and repair in friendly cities (§2.3)."""

from __future__ import annotations

import random

import pytest

from empire.combat.resolver import CombatResolver
from empire.core.city import City
from empire.core.coord import Coord, Direction
from empire.core.engine import (
    MoveOutcome,
    StepOutcome,
    advance_satellites,
    crash_out_of_fuel_fighters,
    execute_unit_path,
    repair_in_cities,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Carrier, Destroyer, Fighter, Satellite

# --- helpers -----------------------------------------------------------------


def _build_map(rows: list[str], cities: dict[Coord, City] | None = None) -> Map:
    cities = cities or {}
    height = len(rows)
    width = len(rows[0])
    terrain_for = {"L": TerrainKind.LAND, "W": TerrainKind.WATER, "C": TerrainKind.CITY}
    tiles: dict[Coord, Tile] = {}
    for y in range(height):
        for x in range(width):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=terrain_for[rows[y][x]])
    return Map(width=width, height=height, tiles=tiles)


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


@pytest.fixture()
def resolver() -> CombatResolver:
    return CombatResolver()


def _fly(
    fighter: Fighter, m: Map, resolver: CombatResolver, *cells: Coord
) -> MoveOutcome:
    return execute_unit_path(
        unit=fighter,
        path=tuple((c.x, c.y) for c in cells),
        real_map=m,
        rules=STANDARD,
        combat_resolver=resolver,
        rng=random.Random(0),
    )


# --- fighter fuel ------------------------------------------------------------


def test_fighter_burns_one_fuel_per_cell(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["WWWWW"])
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 5
    m.place_unit(fighter, Coord(0, 0))

    _fly(fighter, m, resolver, Coord(1, 0), Coord(2, 0), Coord(3, 0))

    assert fighter.coord == Coord(3, 0)
    assert fighter.range == 2


def test_fighter_refuels_landing_on_friendly_city(
    p1: Player, resolver: CombatResolver
) -> None:
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p1)
    m = _build_map(["WC"], cities={Coord(1, 0): city})
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 3
    m.place_unit(fighter, Coord(0, 0))

    _fly(fighter, m, resolver, Coord(1, 0))

    assert fighter.coord == Coord(1, 0)
    assert fighter.range == STANDARD.fighter_base_range  # refuelled


def test_fighter_refuels_when_loading_onto_carrier(
    p1: Player, resolver: CombatResolver
) -> None:
    m = _build_map(["WW"])
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 2
    carrier = Carrier(UnitId(2), p1, Coord(1, 0))
    m.place_unit(fighter, Coord(0, 0))
    m.place_unit(carrier, Coord(1, 0))

    outcome = _fly(fighter, m, resolver, Coord(1, 0))

    assert outcome.last_outcome is StepOutcome.LOADED
    assert fighter.range == STANDARD.fighter_base_range


def test_out_of_fuel_fighter_over_water_crashes(p1: Player) -> None:
    m = _build_map(["WW"])
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 0
    m.place_unit(fighter, Coord(0, 0))

    crashed = crash_out_of_fuel_fighters(m, STANDARD)

    assert crashed == (UnitId(1),)
    assert m.unit_by_id(UnitId(1)) is None


def test_out_of_fuel_fighter_on_friendly_city_survives(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _build_map(["C"], cities={Coord(0, 0): city})
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 0
    m.place_unit(fighter, Coord(0, 0))

    crashed = crash_out_of_fuel_fighters(m, STANDARD)

    assert crashed == ()
    assert m.unit_by_id(UnitId(1)) is fighter


# --- satellite orbit / lifetime ----------------------------------------------


def test_satellite_orbits_one_cell_per_round(p1: Player) -> None:
    m = _build_map(["LLLLL"])
    sat = Satellite(UnitId(1), p1, Coord(1, 0))
    sat.range = 10
    m.place_unit(sat, Coord(1, 0))

    moved, deorbited = advance_satellites(m)

    assert moved == (UnitId(1),)
    assert deorbited == ()
    assert sat.coord == Coord(2, 0)  # stepped east
    assert sat.orbit_direction is Direction.E
    assert sat.range == 9


def test_satellite_bounces_off_edge(p1: Player) -> None:
    m = _build_map(["LLL"])  # width 3; sat at the east edge heading east
    sat = Satellite(UnitId(1), p1, Coord(2, 0))
    sat.range = 10
    m.place_unit(sat, Coord(2, 0))

    advance_satellites(m)

    # Reflected: heading flips west, steps back to (1, 0).
    assert sat.orbit_direction is Direction.W
    assert sat.coord == Coord(1, 0)


def test_satellite_deorbits_at_end_of_lifetime(p1: Player) -> None:
    m = _build_map(["LLL"])
    sat = Satellite(UnitId(1), p1, Coord(0, 0))
    sat.range = 1
    m.place_unit(sat, Coord(0, 0))

    _, deorbited = advance_satellites(m)

    assert deorbited == (UnitId(1),)
    assert m.unit_by_id(UnitId(1)) is None


def test_satellite_cannot_be_attacked(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    m = _build_map(["LL"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    enemy_sat = Satellite(UnitId(2), p2, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(enemy_sat, Coord(1, 0))

    outcome = execute_unit_path(
        unit=army,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=resolver,
        rng=random.Random(0),
    )

    # No combat: the army moves onto the cell and the satellite is untouched.
    assert outcome.last_outcome is StepOutcome.OK
    assert army.coord == Coord(1, 0)
    assert m.unit_by_id(UnitId(2)) is enemy_sat


# --- repair ------------------------------------------------------------------


def test_stationary_unit_repairs_in_friendly_city(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _build_map(["C"], cities={Coord(0, 0): city})
    dest = Destroyer(UnitId(1), p1, Coord(0, 0))
    dest.hits = 1  # max_hits 3
    m.place_unit(dest, Coord(0, 0))

    repaired = repair_in_cities(m)

    assert repaired == (UnitId(1),)
    assert dest.hits == 2


def test_unit_does_not_repair_after_moving(p1: Player, resolver: CombatResolver) -> None:
    # A damaged destroyer sails from water into a friendly port city this
    # round: it began the turn outside the city, so it does not repair (spec
    # §2.3 requires beginning *and* ending stationary in the city).
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p1)
    m = _build_map(["WC"], cities={Coord(1, 0): city})
    dest = Destroyer(UnitId(1), p1, Coord(0, 0))
    dest.hits = 1
    m.place_unit(dest, Coord(0, 0))

    execute_unit_path(
        unit=dest,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=resolver,
        rng=random.Random(0),
    )
    repaired = repair_in_cities(m)

    assert UnitId(1) not in repaired  # moved into the city this round
    assert dest.hits == 1


def test_unit_does_not_repair_in_unowned_city(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p2)  # enemy-owned
    m = _build_map(["C"], cities={Coord(0, 0): city})
    dest = Destroyer(UnitId(1), p1, Coord(0, 0))
    dest.hits = 1
    m.place_unit(dest, Coord(0, 0))

    repaired = repair_in_cities(m)

    assert repaired == ()
    assert dest.hits == 1


def test_repair_caps_at_max_hits(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _build_map(["C"], cities={Coord(0, 0): city})
    dest = Destroyer(UnitId(1), p1, Coord(0, 0))  # full HP (3)
    m.place_unit(dest, Coord(0, 0))

    repaired = repair_in_cities(m)

    assert repaired == ()
    assert dest.hits == type(dest).max_hits

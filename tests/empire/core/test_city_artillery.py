"""City artillery: the FortifiedCities defensive mechanic (spec §4.7).

Cities fire a single-target ranged salvo (range 2, chance to hit, chance to
pin) once per round. These tests pin down the rules in isolation — targeting
priority, range, hit/miss, pinning, the one-shot cadence, neutral-city fire,
and the round-level reset — using deterministic RNG seeds.
"""

from __future__ import annotations

import random

import pytest

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.engine import (
    ArtilleryOutcome,
    city_can_fire_at,
    clear_movement_pins,
    execute_city_artillery,
    reactive_city_fire,
    reset_city_artillery,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD, RuleSet
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Destroyer, Fighter, Unit

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


# A fortified ruleset that always hits and always pins, so outcomes are
# deterministic regardless of RNG. Individual tests override the probabilities
# when they need to exercise the miss / no-pin branches.
FORT = RuleSet(
    name="TEST_FORT",
    map_profile=STANDARD.map_profile,
    army_capture_city_deterministic=True,
    city_artillery_range=2,
    city_artillery_hit_prob=1.0,
    city_artillery_pin_prob=1.0,
)


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


def _place(m: Map, u: Unit) -> Unit:
    m.place_unit(u, u.coord)
    return u


def _rng() -> random.Random:
    return random.Random(0)


# --- eligibility: range, ownership, the spent shot ---------------------------


def test_can_fire_on_enemy_within_range(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(2, 0)))  # distance 2 == range
    assert city_can_fire_at(city, army, FORT)


def test_cannot_fire_out_of_range(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(3, 0)))  # distance 3 > range 2
    assert not city_can_fire_at(city, army, FORT)


def test_cannot_fire_on_own_unit(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    own = _place(m, Army(UnitId(1), p1, Coord(1, 0)))
    assert not city_can_fire_at(city, own, FORT)


def test_disabled_ruleset_never_fires(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    assert not city_can_fire_at(city, army, STANDARD)  # range 0
    assert (
        execute_city_artillery(city, m, STANDARD, _rng()).outcome
        is ArtilleryOutcome.DISABLED
    )


def test_one_shot_per_round(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLL"], cities={Coord(0, 0): city})
    _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    _place(m, Army(UnitId(2), p2, Coord(2, 0)))

    first = execute_city_artillery(city, m, FORT, _rng())
    assert first.outcome is ArtilleryOutcome.TARGET_DESTROYED
    # Shot spent: a second call this round is refused.
    assert not city.artillery_ready
    second = execute_city_artillery(city, m, FORT, _rng())
    assert second.outcome is ArtilleryOutcome.NOT_READY


# --- targeting priority: army before fighter before everything else ----------


def test_targets_army_over_closer_fighter(p1: Player, p2: Player) -> None:
    """An army is the only unit that captures, so it is shelled first even when
    a fighter sits closer."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLL"], cities={Coord(0, 0): city})
    fighter = _place(m, Fighter(UnitId(1), p2, Coord(1, 0)))  # closer
    army = _place(m, Army(UnitId(2), p2, Coord(2, 0)))  # farther, higher priority

    result = execute_city_artillery(city, m, FORT, _rng())
    assert result.outcome is ArtilleryOutcome.TARGET_DESTROYED
    assert result.target_id == army.id
    assert m.unit_by_id(army.id) is None
    assert m.unit_by_id(fighter.id) is not None  # fighter untouched


def test_no_target_when_range_empty(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLL"], cities={Coord(0, 0): city})
    assert (
        execute_city_artillery(city, m, FORT, _rng()).outcome
        is ArtilleryOutcome.NO_TARGET
    )


# --- hit / miss / pin --------------------------------------------------------


def test_hit_kills_one_hp_unit_and_pins(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))

    result = execute_city_artillery(city, m, FORT, _rng())
    assert result.outcome is ArtilleryOutcome.TARGET_DESTROYED
    assert m.unit_by_id(army.id) is None


def test_miss_still_pins_and_spends_shot(p1: Player, p2: Player) -> None:
    """pin always, hit never: the army survives but is pinned and the shot is
    spent — the suppression effect that stops a lone attacker reaching a city."""
    rules = RuleSet(
        name="MISS_BUT_PIN",
        map_profile=STANDARD.map_profile,
        city_artillery_range=2,
        city_artillery_hit_prob=0.0,
        city_artillery_pin_prob=1.0,
    )
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))

    result = execute_city_artillery(city, m, rules, _rng())
    assert result.outcome is ArtilleryOutcome.MISSED
    assert m.unit_by_id(army.id) is not None  # survived
    assert army.pinned
    assert army.moves_this_turn() == 0  # land unit fully pinned
    assert not city.artillery_ready  # shot spent on a miss


def test_no_pin_when_pin_prob_zero(p1: Player, p2: Player) -> None:
    rules = RuleSet(
        name="HIT_NO_PIN",
        map_profile=STANDARD.map_profile,
        city_artillery_range=2,
        city_artillery_hit_prob=0.0,  # survive so we can read .pinned
        city_artillery_pin_prob=0.0,
    )
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))

    execute_city_artillery(city, m, rules, _rng())
    assert not army.pinned
    assert army.moves_this_turn() == Army.speed  # unhindered


def test_naval_pin_is_halved_not_zeroed(p1: Player, p2: Player) -> None:
    """Land/air pinned units lose all movement; naval keeps half (rounded
    down)."""
    rules = RuleSet(
        name="MISS_BUT_PIN",
        map_profile=STANDARD.map_profile,
        city_artillery_range=2,
        city_artillery_hit_prob=0.0,
        city_artillery_pin_prob=1.0,
    )
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CW"], cities={Coord(0, 0): city})
    dd = _place(m, Destroyer(UnitId(1), p2, Coord(1, 0)))
    full = Destroyer.speed

    execute_city_artillery(city, m, rules, _rng())
    assert dd.pinned
    assert dd.moves_this_turn() == full // 2


# --- neutral cities fire too -------------------------------------------------


def test_neutral_city_fires_on_any_player(p2: Player) -> None:
    """A neutral city (owner None) is hostile to everyone — 'neutral, not
    lame.'"""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=None)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    assert city_can_fire_at(city, army, FORT)
    result = execute_city_artillery(city, m, FORT, _rng())
    assert result.outcome is ArtilleryOutcome.TARGET_DESTROYED
    assert m.unit_by_id(army.id) is None


# --- reactive (overwatch) fire ----------------------------------------------


def test_reactive_fire_from_all_cities_in_range(p1: Player, p2: Player) -> None:
    """A unit that ends a move inside two cities' range draws a salvo from
    each that still has its shot."""
    c1 = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    c2 = City(id=CityId(2), coord=Coord(2, 0), owner=None)
    m = _map(["CLC"], cities={Coord(0, 0): c1, Coord(2, 0): c2})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))  # in range of both

    results = reactive_city_fire(army, m, FORT, _rng())
    # reactive_city_fire returns (city_id, result) pairs. The first salvo
    # (hit_prob 1.0) destroys the army, so at least one result is decisive and
    # the army is gone.
    assert any(
        result.outcome is ArtilleryOutcome.TARGET_DESTROYED
        for _city_id, result in results
    )
    assert m.unit_by_id(army.id) is None


def test_reactive_fire_disabled_under_standard(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    assert reactive_city_fire(army, m, STANDARD, _rng()) == []


# --- round-level housekeeping ------------------------------------------------


def test_reset_rearms_all_cities(p1: Player) -> None:
    c1 = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    c2 = City(id=CityId(2), coord=Coord(2, 0), owner=None)
    m = _map(["CLC"], cities={Coord(0, 0): c1, Coord(2, 0): c2})
    c1.artillery_ready = False
    c2.artillery_ready = False

    reset_city_artillery(m)
    assert c1.artillery_ready
    assert c2.artillery_ready


def test_clear_pins_restores_movement(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    army.pinned = True
    assert army.moves_this_turn() == 0

    clear_movement_pins(m)
    assert not army.pinned
    assert army.moves_this_turn() == Army.speed

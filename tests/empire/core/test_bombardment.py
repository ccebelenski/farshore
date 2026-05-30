"""Ground bombardment: surface warships strike adjacent shore/air targets
(spec §4.6)."""

from __future__ import annotations

import random

import pytest

from empire.combat.resolver import CombatResolver
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.engine import (
    BombardmentOutcome,
    BombardResult,
    can_bombard,
    execute_bombardment,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import (
    Army,
    Battleship,
    Carrier,
    Destroyer,
    Fighter,
    Patrol,
    Submarine,
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


def _bombard(m: Map, ship: Unit, target: Coord) -> BombardResult:
    return execute_bombardment(
        ship=ship,
        target=target,
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )


# --- eligibility -------------------------------------------------------------


def test_eligibility_by_kind_and_hp(p1: Player) -> None:
    assert can_bombard(Battleship(UnitId(1), p1, Coord(0, 0)))  # 18 HP
    assert can_bombard(Destroyer(UnitId(2), p1, Coord(0, 0)))  # 3 HP
    assert can_bombard(Patrol(UnitId(3), p1, Coord(0, 0)))  # 2 HP — fires, then 1
    # Wrong kinds never bombard.
    assert not can_bombard(Submarine(UnitId(4), p1, Coord(0, 0)))
    assert not can_bombard(Carrier(UnitId(5), p1, Coord(0, 0)))
    assert not can_bombard(Transport(UnitId(6), p1, Coord(0, 0)))
    # A warship at 1 HP can't fire — it always reserves its last point.
    patrol = Patrol(UnitId(7), p1, Coord(0, 0))
    patrol.hits = 1
    assert not can_bombard(patrol)


def test_ship_at_one_hp_cannot_bombard(p1: Player, p2: Player) -> None:
    m = _map(["WL"])
    bb = _place(m, Battleship(UnitId(1), p1, Coord(0, 0)))
    bb.hits = 1  # reserves its last point
    army = _place(m, Army(UnitId(2), p2, Coord(1, 0)))

    result = _bombard(m, bb, Coord(1, 0))

    assert result.outcome is BombardmentOutcome.INELIGIBLE
    assert m.unit_by_id(army.id) is not None  # untouched


# --- core hits ---------------------------------------------------------------


def test_bombard_destroys_shore_army_and_costs_one_hp(p1: Player, p2: Player) -> None:
    m = _map(["WL"])
    bb = _place(m, Battleship(UnitId(1), p1, Coord(0, 0)))
    army = _place(m, Army(UnitId(2), p2, Coord(1, 0)))

    result = _bombard(m, bb, Coord(1, 0))

    assert result.outcome is BombardmentOutcome.TARGET_DESTROYED
    assert result.target_id == army.id
    assert m.unit_by_id(army.id) is None
    assert bb.hits == Battleship.max_hits - 1  # paid 1 HP
    assert bb.coord == Coord(0, 0)  # ranged — did not move


def test_bombard_destroys_adjacent_fighter_over_water(p1: Player, p2: Player) -> None:
    m = _map(["WW"])
    dd = _place(m, Destroyer(UnitId(1), p1, Coord(0, 0)))
    fighter = _place(m, Fighter(UnitId(2), p2, Coord(1, 0)))

    result = _bombard(m, dd, Coord(1, 0))

    assert result.outcome is BombardmentOutcome.TARGET_DESTROYED
    assert m.unit_by_id(fighter.id) is None
    assert dd.hits == Destroyer.max_hits - 1


# --- range / no target -------------------------------------------------------


def test_out_of_range(p1: Player, p2: Player) -> None:
    m = _map(["WLL"])
    bb = _place(m, Battleship(UnitId(1), p1, Coord(0, 0)))
    _place(m, Army(UnitId(2), p2, Coord(2, 0)))  # two cells away

    assert _bombard(m, bb, Coord(2, 0)).outcome is BombardmentOutcome.OUT_OF_RANGE


def test_empty_cell_has_no_target(p1: Player) -> None:
    m = _map(["WL"])
    bb = _place(m, Battleship(UnitId(1), p1, Coord(0, 0)))
    assert _bombard(m, bb, Coord(1, 0)).outcome is BombardmentOutcome.NO_TARGET


def test_open_water_ship_is_not_a_bombard_target(p1: Player, p2: Player) -> None:
    """Bombardment is a land/air weapon; open-water naval duels stay move-in."""
    m = _map(["WW"])
    bb = _place(m, Battleship(UnitId(1), p1, Coord(0, 0)))
    enemy_dd = _place(m, Destroyer(UnitId(2), p2, Coord(1, 0)))

    result = _bombard(m, bb, Coord(1, 0))

    assert result.outcome is BombardmentOutcome.NO_TARGET
    assert m.unit_by_id(enemy_dd.id) is not None


# --- city garrison: priority + naval resolution ------------------------------


def test_docked_ship_is_hit_before_fighters_via_normal_combat(
    p1: Player, p2: Player
) -> None:
    """Shelling a coastal city hits the docked ship first (bigger target), and
    that resolves as ordinary combat — the airbase behind it is spared."""
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p2)
    m = _map(["WC"], cities={Coord(1, 0): city})
    bb = _place(m, Battleship(UnitId(1), p1, Coord(0, 0)))
    docked = _place(m, Transport(UnitId(2), p2, Coord(1, 0)))  # str 0 → BB always wins
    fighter = _place(m, Fighter(UnitId(3), p2, Coord(1, 0)))

    result = _bombard(m, bb, Coord(1, 0))

    assert result.outcome is BombardmentOutcome.TARGET_SUNK
    assert result.target_id == docked.id
    assert m.unit_by_id(docked.id) is None
    assert m.unit_by_id(fighter.id) is not None  # airbase shielded by the hull
    assert bb.coord == Coord(0, 0)  # stayed put


def test_naval_bombardment_can_cost_the_attacker(p1: Player, p2: Player) -> None:
    """Against a docked warship it's a real duel — exactly one side survives,
    and the outcome names whichever was lost (robust to the combat RNG)."""
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p1)
    m = _map(["WC"], cities={Coord(1, 0): city})
    attacker = _place(m, Destroyer(UnitId(1), p2, Coord(0, 0)))  # weaker
    docked_bb = _place(m, Battleship(UnitId(2), p1, Coord(1, 0)))  # strong defender

    result = execute_bombardment(
        ship=attacker,
        target=Coord(1, 0),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(1),
    )

    attacker_alive = m.unit_by_id(attacker.id) is not None
    victim_alive = m.unit_by_id(docked_bb.id) is not None
    assert attacker_alive != victim_alive  # exactly one survives
    if result.outcome is BombardmentOutcome.ATTACKER_SUNK:
        assert result.attacker_destroyed and not attacker_alive and victim_alive
    else:
        assert result.outcome is BombardmentOutcome.TARGET_SUNK
        assert attacker_alive and not victim_alive

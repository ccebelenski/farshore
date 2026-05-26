"""Phase-8 canary tests for the engine mechanics.

Scripted single-turn scenarios with deterministic expected outcomes:
- Movement validation (terrain legal, in bounds, step budget).
- Production tick (city builds Army, unit emerges).
- Fog updates from scan (unit moves, new cells visible).
- Combat trigger (Army moves onto enemy Army).
- City capture (Army moves onto neutral / enemy city).
- 20-turn hotseat smoke (NullControllers; engine doesn't crash).
"""

from __future__ import annotations

import random

import pytest

from empire.combat.resolver import CombatResolver
from empire.contracts.controller import NullController
from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove
from empire.core.city import City, ProductionState
from empire.core.coord import Coord
from empire.core.engine import (
    can_enter_terrain,
    execute_unit_path,
    is_legal_step,
    run_production_tick,
    scan_set_for_player,
)
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Battleship, UnitKind

# --- helpers -----------------------------------------------------------------


def _build_map(rows: list[str], cities: dict[Coord, City] | None = None) -> Map:
    cities = cities or {}
    height = len(rows)
    width = len(rows[0])
    tiles: dict[Coord, Tile] = {}
    _terrain_for = {
        "L": TerrainKind.LAND,
        "W": TerrainKind.WATER,
        "C": TerrainKind.CITY,
    }
    for y in range(height):
        for x in range(width):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=_terrain_for[rows[y][x]])
    return Map(width=width, height=height, tiles=tiles)


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


# --- movement validation -----------------------------------------------------


def test_army_cannot_enter_water(p1: Player) -> None:
    m = _build_map(["LLW", "LLW", "LLW"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    assert is_legal_step(army, Coord(1, 0), m, STANDARD) is True
    assert is_legal_step(army, Coord(2, 0), m, STANDARD) is False  # water


def test_army_can_enter_city(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(1, 0), owner=None)
    m = _build_map(["L L", "LLL"], cities={Coord(1, 0): city})
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    assert is_legal_step(army, Coord(1, 0), m, STANDARD) is True


def test_battleship_cannot_enter_land_only_city(p2: Player) -> None:
    """Sea unit may enter a CITY only when it's adjacent to water (port).
    A landlocked city is impassable to ships.
    """
    city = City(id=CityId(1), coord=Coord(1, 1), owner=None)
    m = _build_map(
        ["LLL", "L L", "LLL"],
        cities={Coord(1, 1): city},
    )
    bs = Battleship(UnitId(1), p2, Coord(0, 0))
    # We need the battleship somewhere ostensibly close. For this test the
    # only question is whether it could enter (1, 1).
    assert can_enter_terrain(bs, TerrainKind.CITY, m, Coord(1, 1)) is False


def test_step_must_be_one_cell_chebyshev(p1: Player) -> None:
    m = _build_map(["LLL", "LLL", "LLL"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    assert is_legal_step(army, Coord(1, 0), m, STANDARD) is True
    assert is_legal_step(army, Coord(1, 1), m, STANDARD) is True  # diagonal
    assert is_legal_step(army, Coord(2, 0), m, STANDARD) is False  # too far


# --- scan / visibility -------------------------------------------------------


def test_scan_set_includes_unit_and_city_radii(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(5, 5), owner=p1)
    m = _build_map(
        ["L" * 10 for _ in range(10)],
        cities={Coord(5, 5): city},
    )
    army = Army(UnitId(1), p1, Coord(1, 1))
    m.place_unit(army, Coord(1, 1))
    visible = scan_set_for_player(p1, m)
    # Army's scan_range is 2, so a 5x5 disc around (1, 1).
    assert Coord(1, 1) in visible
    assert Coord(3, 3) in visible  # within army's radius 2
    # City's scan_range is 2 too.
    assert Coord(5, 5) in visible
    assert Coord(7, 7) in visible
    # Cell well outside any disc: not visible.
    assert Coord(9, 9) not in visible


def test_scan_does_not_include_other_players_units(p1: Player, p2: Player) -> None:
    m = _build_map(["L" * 10 for _ in range(10)])
    enemy = Army(UnitId(2), p2, Coord(8, 8))
    m.place_unit(enemy, Coord(8, 8))
    visible = scan_set_for_player(p1, m)
    assert Coord(8, 8) not in visible


# --- production phase --------------------------------------------------------


def test_production_tick_emits_unit_when_ready(p1: Player) -> None:
    city = City(
        id=CityId(1),
        coord=Coord(0, 0),
        owner=p1,
        production=ProductionState(building=UnitKind.ARMY, work=4),
    )
    m = _build_map(["LL", "LL"], cities={Coord(0, 0): city})

    next_id = iter([UnitId(10), UnitId(11)])
    produced = run_production_tick(p1, m, STANDARD, lambda: next(next_id))
    assert len(produced) == 1
    new_army = produced[0]
    assert isinstance(new_army, Army)
    assert new_army.coord == Coord(0, 0)
    # Accumulator consumed by build_time (5 for Army).
    assert city.production.work == 0


def test_production_tick_does_not_emit_when_not_ready(p1: Player) -> None:
    city = City(
        id=CityId(1),
        coord=Coord(0, 0),
        owner=p1,
        production=ProductionState(building=UnitKind.ARMY, work=2),
    )
    m = _build_map(["LL", "LL"], cities={Coord(0, 0): city})
    produced = run_production_tick(p1, m, STANDARD, lambda: UnitId(10))
    assert produced == []
    assert city.production.work == 3  # ticked from 2 to 3


def test_production_waits_when_city_cell_occupied(p1: Player) -> None:
    """If the city's cell is occupied and stacking is off, the unit waits."""
    city = City(
        id=CityId(1),
        coord=Coord(0, 0),
        owner=p1,
        production=ProductionState(building=UnitKind.ARMY, work=4),
    )
    m = _build_map(["LL", "LL"], cities={Coord(0, 0): city})
    sitting = Army(UnitId(5), p1, Coord(0, 0))
    m.place_unit(sitting, Coord(0, 0))
    produced = run_production_tick(p1, m, STANDARD, lambda: UnitId(10))
    assert produced == []
    # Work hit threshold; ready() is True but we didn't consume.
    assert city.production.ready() is True


# --- combat trigger ---------------------------------------------------------


def test_army_attacks_adjacent_enemy(p1: Player, p2: Player) -> None:
    m = _build_map(["LLL", "LLL", "LLL"])
    a = Army(UnitId(1), p1, Coord(0, 0))
    d = Army(UnitId(2), p2, Coord(1, 0))
    m.place_unit(a, Coord(0, 0))
    m.place_unit(d, Coord(1, 0))

    resolver = CombatResolver()
    rng = random.Random(0)
    outcome = execute_unit_path(
        unit=a,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=resolver,
        rng=rng,
    )
    # Combat resolves; one of the two armies dies.
    assert len(outcome.units_destroyed) == 1
    assert outcome.units_destroyed[0] in (UnitId(1), UnitId(2))


# --- city capture ------------------------------------------------------------


def test_army_captures_neutral_city_with_deterministic_rules(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(1, 0), owner=None)
    m = _build_map(["LCL"], cities={Coord(1, 0): city})
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))

    from dataclasses import replace

    rules = replace(STANDARD, army_capture_city_deterministic=True)

    outcome = execute_unit_path(
        unit=army,
        path=((1, 0),),
        real_map=m,
        rules=rules,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )
    assert outcome.steps_taken == 1
    assert outcome.cities_captured == (CityId(1),)
    assert city.owner is p1
    assert army.coord == Coord(1, 0)


def test_capture_failure_destroys_army_when_nondeterministic(p1: Player) -> None:
    """With army_capture_city_deterministic=False, capture is a 50% roll.
    Use a seed that produces a failure on the first attempt.
    """
    city = City(id=CityId(1), coord=Coord(1, 0), owner=None)
    m = _build_map(["LCL"], cities={Coord(1, 0): city})
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))

    # Find a seed that fails the capture roll. random.random() >= 0.5 fails.
    failing_seed = None
    for s in range(20):
        if random.Random(s).random() >= 0.5:
            failing_seed = s
            break
    assert failing_seed is not None

    outcome = execute_unit_path(
        unit=army,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(failing_seed),
    )
    assert outcome.steps_taken == 0
    assert outcome.cities_captured == ()
    assert outcome.units_destroyed == (UnitId(1),)
    assert city.owner is None


# --- end-to-end turn loop with controllers ----------------------------------


def test_twenty_idle_turns_dont_crash(p1: Player, p2: Player) -> None:
    """Twenty rounds of NullController-vs-NullController on an empty map.

    No units, no cities, so this isn't a 'hotseat' in any meaningful
    sense — it's an idle-loop check that the turn manager cycles through
    all three phases (production/movement/scan) for 20 rounds without
    blowing up on the absence of anything to do. The integration scenario
    with real units lives in `test_integration_production_movement_combat`.
    """
    m = _build_map(["L" * 8 for _ in range(8)])
    g = Game(rules=STANDARD, real_map=m, players=[p1, p2], seed=0)
    g.attach_controller(p1.id, NullController())
    g.attach_controller(p2.id, NullController())
    for _ in range(20):
        g.run_turn()
    assert g.turn == 20


def test_production_emits_a_unit_after_build_time_turns(p1: Player, p2: Player) -> None:
    """A city with Army-target production and 0 work should emit an Army
    after exactly 5 turns (Army.build_time = 5).
    """
    city = City(
        id=CityId(1),
        coord=Coord(2, 2),
        owner=p1,
        production=ProductionState(building=UnitKind.ARMY, work=0),
    )
    m = _build_map(["L" * 6 for _ in range(6)], cities={Coord(2, 2): city})
    g = Game(rules=STANDARD, real_map=m, players=[p1, p2], seed=0)
    g.attach_controller(p1.id, NullController())
    g.attach_controller(p2.id, NullController())

    initial_units = list(m.all_units())
    assert initial_units == []
    # 5 ticks needed; production runs once per round per player.
    for _ in range(5):
        g.run_turn()
    final_units = list(m.all_units())
    assert len(final_units) == 1
    assert isinstance(final_units[0], Army)
    assert final_units[0].coord == Coord(2, 2)
    assert final_units[0].owner is p1


def test_fog_updates_when_unit_moves(p1: Player) -> None:
    """After a turn, the player's view reflects the new visible set (Army at
    its current position).
    """
    m = _build_map(["L" * 8 for _ in range(8)])
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))

    class _MovingController:
        def name(self) -> str:
            return "moving"

        def plan_turn(self, view: object) -> TurnPlan:
            del view
            return TurnPlan(moves=(UnitMove(unit_id=UnitId(1), path=((1, 1),)),))

        def revise_move(self, unit_id: UnitId, surprise: object, view: object) -> UnitMove:
            del surprise, view
            return UnitMove(unit_id=unit_id)

    g = Game(rules=STANDARD, real_map=m, players=[p1], seed=0)
    g.attach_controller(p1.id, _MovingController())
    g.run_turn()
    assert army.coord == Coord(1, 1)
    # After scan, the army's radius-2 disc around (1,1) is visible.
    # (3, 3) is within distance 2.
    assert Coord(3, 3) in p1.view.visible
    # Cells outside the disc are not in `visible` (but might be remembered if
    # they were previously visible — they weren't, so they're plain unseen).
    assert Coord(7, 7) not in p1.view.visible


def test_production_orders_change_target(p1: Player) -> None:
    """A controller's ProductionOrder changes the city's build target."""
    city = City(
        id=CityId(1),
        coord=Coord(0, 0),
        owner=p1,
        production=ProductionState(building=UnitKind.ARMY, work=2),
    )
    m = _build_map(["LL", "LL"], cities={Coord(0, 0): city})

    class _OrderingController:
        def name(self) -> str:
            return "ordering"

        def plan_turn(self, view: object) -> TurnPlan:
            del view
            return TurnPlan(
                production_orders=(ProductionOrder(city_id=CityId(1), target=UnitKind.FIGHTER),),
            )

        def revise_move(self, unit_id: UnitId, surprise: object, view: object) -> UnitMove:
            del surprise, view
            return UnitMove(unit_id=unit_id)

    g = Game(rules=STANDARD, real_map=m, players=[p1], seed=0)
    g.attach_controller(p1.id, _OrderingController())
    g.run_turn()
    assert city.production.building is UnitKind.FIGHTER

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


def test_production_emits_onto_occupied_city_cell(p1: Player) -> None:
    """Production always emits on the city cell, even if occupied (spec §5.1).

    Stacking on the city is a transient state; the turn-end disband phase
    (`disband_overcrowded_city_units`) enforces the city's support limits.
    """
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
    assert [u.id for u in produced] == [UnitId(10)]
    assert produced[0].coord == Coord(0, 0)
    # Both armies now share the city cell; work was consumed.
    assert {u.id for u in m.units_at(Coord(0, 0))} == {UnitId(5), UnitId(10)}
    assert city.production.ready() is False


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
    # The assault consumes the army (§4.5): it disbanded into the city.
    assert m.unit_by_id(army.id) is None


class _FixedRandom(random.Random):
    """An `rng` stub whose `random()` always returns a fixed value.

    Used so capture-roll tests assert the *rule* (`r >= 0.5` fails)
    rather than a particular seed-to-roll mapping. Brittleness fix:
    the previous version of this test brute-searched seeds 0..19 for
    one that failed, which silently breaks the moment the engine
    interposes any other RNG draw before the capture check.
    """

    def __init__(self, value: float) -> None:
        super().__init__()
        self._value = value

    def random(self) -> float:
        return self._value


def test_capture_failure_destroys_army_when_nondeterministic(p1: Player) -> None:
    """With `army_capture_city_deterministic=False`, capture is a 50% roll
    (`rng.random() >= 0.5` fails). When the roll fails, the attacking
    army is destroyed and ownership of the city is unchanged.
    """
    city = City(id=CityId(1), coord=Coord(1, 0), owner=None)
    m = _build_map(["LCL"], cities={Coord(1, 0): city})
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))

    outcome = execute_unit_path(
        unit=army,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=_FixedRandom(0.9),  # >= 0.5 → fail
    )
    assert outcome.steps_taken == 0
    assert outcome.cities_captured == ()
    assert outcome.units_destroyed == (UnitId(1),)
    assert city.owner is None


# --- multi-step `execute_unit_path` ------------------------------------------
#
# The single-step paths above cover one cell per call. These tests exercise
# the loop inside `execute_unit_path` — the budget gate, the mid-path
# combat abort, and the mid-path terrain abort. Use Battleship because
# its speed (2) gives a multi-step budget without bumping a unit's hits.


def test_multi_step_path_completes_when_clear(p1: Player) -> None:
    """A path within budget and across legal terrain completes all steps."""
    m = _build_map(["WWWW"])  # all water; battleship can traverse
    ship = Battleship(UnitId(1), p1, Coord(0, 0))
    m.place_unit(ship, Coord(0, 0))
    outcome = execute_unit_path(
        unit=ship,
        path=((1, 0), (2, 0)),  # exactly speed=2 cells
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )
    assert outcome.steps_taken == 2
    assert ship.coord == Coord(2, 0)
    assert outcome.units_destroyed == ()
    assert outcome.cities_captured == ()


def test_multi_step_path_aborts_on_terrain_at_step_two(p1: Player) -> None:
    """Water at step 1, land at step 2: battleship walks the first cell,
    refuses the land step, returns `steps_taken == 1`."""
    m = _build_map(["WWLW"])
    ship = Battleship(UnitId(1), p1, Coord(0, 0))
    m.place_unit(ship, Coord(0, 0))
    outcome = execute_unit_path(
        unit=ship,
        path=((1, 0), (2, 0)),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )
    assert outcome.steps_taken == 1
    assert ship.coord == Coord(1, 0)


def test_multi_step_path_resolves_combat_at_mid_step_and_continues(
    p1: Player, p2: Player,
) -> None:
    """Path goes W(start) → W(enemy) → W(empty). Combat at step 1 destroys
    the defender (deterministic when attacker overwhelms); attacker then
    has one move left and continues. Verifies that combat doesn't
    short-circuit the remaining path budget.
    """
    m = _build_map(["WWWW"])
    attacker = Battleship(UnitId(1), p1, Coord(0, 0))
    defender = Battleship(UnitId(2), p2, Coord(1, 0))
    defender.hits = 1  # one blow ends combat in attacker's favor
    m.place_unit(attacker, Coord(0, 0))
    m.place_unit(defender, Coord(1, 0))
    outcome = execute_unit_path(
        unit=attacker,
        path=((1, 0), (2, 0)),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )
    # Defender destroyed; attacker advances onto the cleared cell and uses
    # its remaining move to continue to (2,0).
    assert UnitId(2) in outcome.units_destroyed
    assert outcome.steps_taken == 2
    assert attacker.coord == Coord(2, 0)


def test_capture_success_when_nondeterministic_roll_passes(p1: Player) -> None:
    """Mirror of the failure case: `random() < 0.5` succeeds; army takes the city."""
    city = City(id=CityId(1), coord=Coord(1, 0), owner=None)
    m = _build_map(["LCL"], cities={Coord(1, 0): city})
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))

    outcome = execute_unit_path(
        unit=army,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=_FixedRandom(0.1),  # < 0.5 → succeed
    )
    assert outcome.steps_taken == 1
    assert outcome.cities_captured == (CityId(1),)
    assert outcome.units_destroyed == ()  # a disband, not a combat loss
    assert city.owner is p1
    assert m.unit_by_id(army.id) is None  # consumed by the capture (§4.5)


# --- end-to-end turn loop with controllers ----------------------------------


def test_integration_production_movement_combat_capture(
    p1: Player, p2: Player,
) -> None:
    """One `run_turn()` that exercises all four Phase-8 mechanics together.

    Setup (W = water, L = land, C = city):
      ```
      LCL         row 0: P1 capital(0,0) -- enemy_city(1,0) on direct route
      ```
    More precisely: P1 owns a city at (0,0) with ARMY production state
    `work=4` (one tick away from emitting). A P1 army already sits one
    cell west of a neutral city at (2,0), and a P2 army defends that
    city. The controller's `TurnPlan` walks the army onto (2,0).

    Expected after one `run_turn()`:
    - Production: city emits a new ARMY at (0,0) (work was 4, ticked to 5).
    - Movement: the existing army attacks; combat fires; the deterministic
      capture rule then transfers the city.
    - Scan: P1's fog updates from the new positions.

    This locks in the *interaction* of subsystems — each is unit-tested in
    isolation above, but a regression that breaks the pipeline (e.g. scan
    fired before movement, or production output not reachable to the
    controller) wouldn't surface in any single-mechanic test.
    """
    own_city = City(
        id=CityId(1),
        coord=Coord(0, 0),
        owner=p1,
        production=ProductionState(building=UnitKind.ARMY, work=4),
    )
    target_city = City(id=CityId(2), coord=Coord(2, 0), owner=None)
    m = _build_map(
        ["CLC"],
        cities={Coord(0, 0): own_city, Coord(2, 0): target_city},
    )
    army = Army(UnitId(1), p1, Coord(1, 0))
    m.place_unit(army, Coord(1, 0))

    # Use a deterministic ruleset variant so the capture roll is not random.
    from dataclasses import replace

    rules = replace(STANDARD, army_capture_city_deterministic=True)

    plan = TurnPlan(
        moves=(UnitMove(unit_id=army.id, path=((2, 0),)),),
    )

    class _ScriptedController:
        """Returns the prepared TurnPlan for p1; no-op for anyone else."""

        def __init__(self, prepared: TurnPlan) -> None:
            self._plan = prepared

        def name(self) -> str:
            return "Scripted"

        def plan_turn(self, view: object) -> TurnPlan:
            del view
            return self._plan

        def revise_move(
            self, unit_id: UnitId, surprise: object, view: object,
        ) -> UnitMove:
            del surprise, view
            return UnitMove(unit_id=unit_id)

    g = Game(
        rules=rules,
        real_map=m,
        players=[p1, p2],
        seed=0,
        combat_resolver=CombatResolver(),
    )
    g.attach_controller(p1.id, _ScriptedController(plan))
    g.attach_controller(p2.id, NullController())

    units_before = {u.id for u in m.all_units()}
    assert units_before == {UnitId(1)}

    g.run_turn()

    # 1. Production: capital emitted a new ARMY (Army#2 or higher); it gets
    # the birth-round §5.4 grace and survives on the capital.
    own_units = [u for u in m.all_units() if u.owner is p1]
    assert len(own_units) == 1, [u.id for u in own_units]
    assert own_units[0].coord == Coord(0, 0), \
        "expected newly-produced unit at the capital"
    assert own_units[0].id != UnitId(1)

    # 2. Capture: target city is now owned by p1 — and the conqueror
    # disbanded into it at p1's turn-end (spec §5.4: an army never survives
    # its own capture into the opponent's turn).
    assert target_city.owner is p1
    assert m.unit_by_id(UnitId(1)) is None

    # 3. Scan: p1 has visibility on (2,0) (scanned before the disband).
    assert Coord(2, 0) in p1.view.visible


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


def test_city_can_produce_ships_only_at_a_port() -> None:
    """Sea units build only at a city adjacent to water; land/air anywhere."""
    from empire.core.engine import city_can_produce

    # Port city at (1,1) touches water at (1,0); inland city at (3,3) doesn't.
    real_map = _build_map(
        [
            "LWLL",
            "LCLL",
            "LLLL",
            "LLLC",
        ]
    )
    port, inland = Coord(1, 1), Coord(3, 3)
    assert city_can_produce(UnitKind.BATTLESHIP, port, real_map)
    assert not city_can_produce(UnitKind.BATTLESHIP, inland, real_map)
    assert not city_can_produce(UnitKind.TRANSPORT, inland, real_map)
    # Land/air build regardless of coast.
    for kind in (UnitKind.ARMY, UnitKind.FIGHTER):
        assert city_can_produce(kind, port, real_map)
        assert city_can_produce(kind, inland, real_map)


def test_transport_blocked_by_enemy_unit_instead_of_crashing(p1: Player, p2: Player) -> None:
    """Regression: a non-combatant (transport, strength 0) ordered onto an enemy
    unit must be BLOCKED — it can't attack, so it can't enter. Before the fix the
    resolver raised CombatError('neither transport nor transport can engage the
    other') and crashed the turn (exposed by Portfolio-vs-Search at sea)."""
    from empire.core.engine import StepOutcome
    from empire.core.unit import Transport

    m = _build_map(["WW"])  # two water cells
    mover = Transport(UnitId(1), p1, Coord(0, 0))
    blocker = Transport(UnitId(2), p2, Coord(1, 0))
    m.place_unit(mover, Coord(0, 0))
    m.place_unit(blocker, Coord(1, 0))

    outcome = execute_unit_path(
        unit=mover,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=CombatResolver(),
        rng=random.Random(0),
    )
    assert outcome.last_outcome is StepOutcome.BLOCKED_BY_ENEMY
    assert outcome.steps_taken == 0
    assert outcome.units_destroyed == ()
    assert mover.coord == Coord(0, 0)  # stayed put
    assert m.unit_by_id(UnitId(2)) is blocker  # enemy untouched

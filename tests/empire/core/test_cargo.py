"""Phase 10.7 — Transport & Carrier cargo (load/unload), spec §2.2/§3.4."""

from __future__ import annotations

import dataclasses
import random

import pytest

from empire.combat.resolver import CombatResolver
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.engine import (
    MoveOutcome,
    StepOutcome,
    execute_unit_path,
    execute_unload,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD, RuleSet
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Carrier, Destroyer, Fighter, Transport, Unit
from tests.empire.support import build_map as _build_map

# --- helpers -----------------------------------------------------------------


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


@pytest.fixture()
def resolver() -> CombatResolver:
    return CombatResolver()


def _step(
    unit: Unit,
    to: Coord,
    m: Map,
    resolver: CombatResolver,
    rules: RuleSet = STANDARD,
) -> MoveOutcome:
    return execute_unit_path(
        unit=unit,
        path=((to.x, to.y),),
        real_map=m,
        rules=rules,
        combat_resolver=resolver,
        rng=random.Random(0),
    )


# --- loading -----------------------------------------------------------------


def test_army_loads_onto_adjacent_transport(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["LWW"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    transport = Transport(UnitId(2), p1, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(transport, Coord(1, 0))

    outcome = _step(army, Coord(1, 0), m, resolver)

    assert outcome.last_outcome is StepOutcome.LOADED
    assert transport.cargo == [UnitId(1)]
    assert army.carried_by == UnitId(2)
    assert army.is_aboard()
    # Aboard: in the registry but off the spatial index, so it shares the
    # carrier's cell without independently occupying it.
    assert m.unit_by_id(UnitId(1)) is army
    assert list(m.units_at(Coord(1, 0))) == [transport]
    assert army not in list(m.board_units())
    assert army.coord == Coord(1, 0)


def test_loaded_cargo_rides_along_when_carrier_moves(
    p1: Player, resolver: CombatResolver
) -> None:
    m = _build_map(["LWWW"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    transport = Transport(UnitId(2), p1, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(transport, Coord(1, 0))
    _step(army, Coord(1, 0), m, resolver)

    _step(transport, Coord(2, 0), m, resolver)

    assert transport.coord == Coord(2, 0)
    assert army.coord == Coord(2, 0)  # cargo tracks the carrier


def test_full_transport_rejects_further_loads(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["LWW"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    transport = Transport(UnitId(2), p1, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(transport, Coord(1, 0))
    # Saturate capacity (6) with placeholder ids.
    transport.cargo = [UnitId(100 + i) for i in range(6)]

    outcome = _step(army, Coord(1, 0), m, resolver)

    # can_carry is False, so it's a normal (illegal) step onto water.
    assert outcome.last_outcome is StepOutcome.ILLEGAL
    assert not army.is_aboard()
    assert army.coord == Coord(0, 0)


def test_damaged_carrier_has_reduced_capacity(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["LWW"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    transport = Transport(UnitId(2), p1, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(transport, Coord(1, 0))
    # max_hits=3, capacity=6 → at 1 HP, ceil(6*1/3) = 2.
    transport.hits = 1
    assert transport.effective_capacity() == 2
    transport.cargo = [UnitId(100), UnitId(101)]  # at reduced cap

    outcome = _step(army, Coord(1, 0), m, resolver)
    assert outcome.last_outcome is StepOutcome.ILLEGAL
    assert not army.is_aboard()


def test_fighter_loads_onto_carrier(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["WWW"])
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    carrier = Carrier(UnitId(2), p1, Coord(1, 0))
    m.place_unit(fighter, Coord(0, 0))
    m.place_unit(carrier, Coord(1, 0))

    outcome = _step(fighter, Coord(1, 0), m, resolver)

    assert outcome.last_outcome is StepOutcome.LOADED
    assert carrier.cargo == [UnitId(1)]
    assert fighter.carried_by == UnitId(2)


def test_transport_does_not_accept_fighter(p1: Player, resolver: CombatResolver) -> None:
    """Transport carries Army only (spec §2.2); a Fighter can't board it."""
    m = _build_map(["WWW"])
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    transport = Transport(UnitId(2), p1, Coord(1, 0))
    m.place_unit(fighter, Coord(0, 0))
    m.place_unit(transport, Coord(1, 0))

    outcome = _step(fighter, Coord(1, 0), m, resolver)
    # Fighter can legally fly over the transport's water cell, but stacking a
    # friendly is blocked — it does not load.
    assert outcome.last_outcome is StepOutcome.BLOCKED_BY_FRIENDLY
    assert not fighter.is_aboard()


def test_enemy_transport_does_not_accept_load(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    m = _build_map(["LWW"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    enemy_transport = Transport(UnitId(2), p2, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(enemy_transport, Coord(1, 0))

    outcome = _step(army, Coord(1, 0), m, resolver)
    # An army can't enter the enemy transport's water cell at all.
    assert outcome.last_outcome is StepOutcome.ILLEGAL
    assert not army.is_aboard()


# --- unloading ---------------------------------------------------------------


def _load(p: Player, m: Map, resolver: CombatResolver) -> tuple[Army, Transport]:
    army = Army(UnitId(1), p, Coord(0, 0))
    transport = Transport(UnitId(2), p, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(transport, Coord(1, 0))
    _step(army, Coord(1, 0), m, resolver)
    return army, transport


def test_cannot_unload_same_turn_as_loaded(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["LWL"])
    army, _ = _load(p1, m, resolver)
    assert army.loaded_this_turn is True

    outcome = execute_unload(army, Coord(0, 0), m, STANDARD, resolver, random.Random(0))
    assert outcome.last_outcome is StepOutcome.NO_UNLOAD_YET
    assert army.is_aboard()


def test_unload_to_shore_next_turn(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["LWL"])
    army, transport = _load(p1, m, resolver)
    army.loaded_this_turn = False  # simulate end-of-round reset

    outcome = execute_unload(army, Coord(2, 0), m, STANDARD, resolver, random.Random(0))

    assert outcome.last_outcome is StepOutcome.OK
    assert not army.is_aboard()
    assert army.coord == Coord(2, 0)
    assert transport.cargo == []
    assert list(m.units_at(Coord(2, 0))) == [army]


def test_unload_to_illegal_terrain_rejected(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["LWW"])  # only water around the transport
    army, _ = _load(p1, m, resolver)
    army.loaded_this_turn = False

    outcome = execute_unload(army, Coord(2, 0), m, STANDARD, resolver, random.Random(0))
    assert outcome.last_outcome is StepOutcome.ILLEGAL
    assert army.is_aboard()


def test_unload_storms_neutral_city(p1: Player, resolver: CombatResolver) -> None:
    city = City(id=CityId(9), coord=Coord(2, 0), owner=None)
    m = _build_map(["LWC"], cities={Coord(2, 0): city})
    army, transport = _load(p1, m, resolver)
    army.loaded_this_turn = False
    deterministic = dataclasses.replace(STANDARD, army_capture_city_deterministic=True)

    outcome = execute_unload(army, Coord(2, 0), m, deterministic, resolver, random.Random(0))

    assert outcome.last_outcome is StepOutcome.CAPTURED
    assert CityId(9) in outcome.cities_captured
    assert city.owner is p1
    # The amphibious assault consumes the army too (§4.5): it disbands into
    # the city it stormed; the transport's hold is empty either way.
    assert m.unit_by_id(army.id) is None
    assert transport.cargo == []


def test_escort_required_blocks_then_allows_unload(
    p1: Player, resolver: CombatResolver
) -> None:
    rules = dataclasses.replace(STANDARD, transport_escort_required_for_unload=True)
    m = _build_map(["LWL", "WWW"])
    army, _ = _load(p1, m, resolver)
    army.loaded_this_turn = False

    blocked = execute_unload(army, Coord(2, 0), m, rules, resolver, random.Random(0))
    assert blocked.last_outcome is StepOutcome.ILLEGAL
    assert army.is_aboard()

    # Park a friendly destroyer adjacent to the transport: now it may unload.
    escort = Destroyer(UnitId(5), p1, Coord(1, 1))
    m.place_unit(escort, Coord(1, 1))
    allowed = execute_unload(army, Coord(2, 0), m, rules, resolver, random.Random(0))
    assert allowed.last_outcome is StepOutcome.OK
    assert army.coord == Coord(2, 0)


# --- carrier loss ------------------------------------------------------------


def test_sinking_carrier_destroys_its_cargo(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["LWW"])
    _, transport = _load(p1, m, resolver)

    m.remove_unit(transport)

    assert m.unit_by_id(UnitId(2)) is None  # transport gone
    assert m.unit_by_id(UnitId(1)) is None  # cargo went down with it


# --- exit gate: winnability across water -------------------------------------


def test_ferry_across_water_captures_last_city_and_wins(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """Phase 10.7 exit gate: an army ferried to the enemy's island captures
    its last city, making the §8 win condition reachable across water."""
    from empire.core.game import Game

    city1 = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    city2 = City(id=CityId(2), coord=Coord(2, 0), owner=p2)  # P2's only city, an island
    m = _build_map(["CWC"], cities={Coord(0, 0): city1, Coord(2, 0): city2})
    army = Army(UnitId(1), p1, Coord(0, 0))
    transport = Transport(UnitId(2), p1, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(transport, Coord(1, 0))
    rules = dataclasses.replace(STANDARD, army_capture_city_deterministic=True)
    g = Game(rules=rules, real_map=m, players=[p1, p2], seed=0, combat_resolver=resolver)
    assert not g.is_over()

    # Turn 1: load the army onto the transport (the transport could then sail;
    # here the enemy island is already one cell away).
    assert _step(army, Coord(1, 0), m, resolver, rules).last_outcome is StepOutcome.LOADED
    army.loaded_this_turn = False  # next round arrives

    # Turn 2: storm ashore onto the enemy city. The army is consumed by the
    # capture (§4.5) — the win stands on city ownership, not the unit.
    landing = execute_unload(army, Coord(2, 0), m, rules, resolver, random.Random(0))
    assert landing.last_outcome is StepOutcome.CAPTURED
    assert city2.owner is p1
    assert m.unit_by_id(army.id) is None
    assert g.is_over()
    assert g.winner() is p1


def test_ai_amphibious_landing_via_turn_plan(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """An AIController can now disembark cargo: a TurnPlan UnloadOrder lands
    a loaded transport's army onto an adjacent enemy city, capturing it.
    (Before, the plan vocabulary had no unload — AIs couldn't do amphibious
    assault at all; Phase 15.9.)"""
    from empire.contracts.turn_plan import UnitMove, UnloadOrder
    from empire.core.game import Game

    # Enemy island city at (2,0); transport on water at (1,0) with an army
    # aboard; P1 also owns a home city so it isn't already lost.
    home = City(id=CityId(3), coord=Coord(0, 0), owner=p1)
    target = City(id=CityId(2), coord=Coord(2, 0), owner=p2)
    m = _build_map(["CWC"], cities={Coord(0, 0): home, Coord(2, 0): target})
    transport = Transport(UnitId(1), p1, Coord(1, 0))
    army = Army(UnitId(2), p1, Coord(1, 0))
    m.place_unit(transport, Coord(1, 0))
    m.place_unit(army, Coord(1, 0))
    m.load_cargo(transport, army)
    army.loaded_this_turn = False  # loaded on a prior turn
    rules = dataclasses.replace(STANDARD, army_capture_city_deterministic=True)
    game = Game(rules=rules, real_map=m, players=[p1, p2], seed=0, combat_resolver=resolver)

    # Drive P1's turn via a plan carrying an UnloadOrder onto the enemy city.
    class _Plan:
        production_orders = ()
        moves: tuple[UnitMove, ...] = ()
        unloads = (UnloadOrder(cargo_id=UnitId(2), to=(2, 0)),)
        set_orders = ()

    game.turn_manager._apply_turn_plan(p1, _Plan())  # pyright: ignore[reportPrivateUsage]

    assert target.owner is p1  # captured by the amphibious assault
    assert m.unit_by_id(UnitId(2)) is None  # army consumed by the capture (§4.5)
    assert transport.cargo == []  # hold emptied

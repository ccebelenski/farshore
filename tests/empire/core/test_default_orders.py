"""Phase 10.9 — city default-order enforcement on production (spec §5.3)."""

from __future__ import annotations

import random

import pytest

from empire.combat.resolver import CombatResolver
from empire.core.city import City, DefaultOrder, OrderKind
from empire.core.coord import Coord
from empire.core.engine import (
    apply_default_order,
    apply_standing_orders,
    run_production_tick,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.standing_order import PatrolPath, Sentry
from empire.core.unit import Army, UnitKind
from tests.empire.support import land_map as _land_map


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


# --- apply_default_order -----------------------------------------------------


def test_sentry_default_sets_sentry_order(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    city.default_orders[UnitKind.ARMY] = DefaultOrder(OrderKind.SENTRY)
    army = Army(UnitId(1), p1, Coord(0, 0))

    apply_default_order(army, city)

    assert isinstance(army.standing_order, Sentry)


def test_unset_default_leaves_no_order(p1: Player) -> None:
    """No explicit city default → the produced unit awaits orders (spec §5.3).

    It must NOT be auto-sentried, or the TUI auto-cycle would never offer it
    and the AI would treat it as already-handled.
    """
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    army = Army(UnitId(1), p1, Coord(0, 0))

    apply_default_order(army, city)  # no default configured → awaits orders

    assert army.standing_order is None


def test_move_to_default_sets_patrol_toward_target(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    city.default_orders[UnitKind.ARMY] = DefaultOrder(OrderKind.MOVE_TO, Coord(3, 0))
    army = Army(UnitId(1), p1, Coord(0, 0))

    apply_default_order(army, city)

    order = army.standing_order
    assert isinstance(order, PatrolPath)
    assert order.remaining == (Coord(1, 0), Coord(2, 0), Coord(3, 0))


def test_move_to_default_path_is_diagonal_adjacent_steps(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    city.default_orders[UnitKind.ARMY] = DefaultOrder(OrderKind.MOVE_TO, Coord(3, 2))
    army = Army(UnitId(1), p1, Coord(0, 0))

    apply_default_order(army, city)

    order = army.standing_order
    assert isinstance(order, PatrolPath)
    # Greedy diagonal then straight; each step is a single cell.
    assert order.remaining == (Coord(1, 1), Coord(2, 2), Coord(3, 2))
    prev = city.coord
    for c in order.remaining:
        assert prev.chebyshev_to(c) == 1
        prev = c


def test_attack_nearest_default_leaves_no_standing_order(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    city.default_orders[UnitKind.ARMY] = DefaultOrder(OrderKind.ATTACK_NEAREST_ENEMY)
    army = Army(UnitId(1), p1, Coord(0, 0))

    apply_default_order(army, city)

    # Targeting "nearest enemy" is a controller/AI concern (later phases).
    assert army.standing_order is None


# --- end-to-end through production -------------------------------------------


def test_produced_unit_inherits_city_default_and_marches(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    city.production.building = UnitKind.ARMY
    city.production.work = Army.build_time - 1  # one tick from completion
    city.default_orders[UnitKind.ARMY] = DefaultOrder(OrderKind.MOVE_TO, Coord(4, 0))
    m = _land_map(6, 1, cities={Coord(0, 0): city})

    counter = {"n": 100}

    def _next_id() -> UnitId:
        counter["n"] += 1
        return UnitId(counter["n"])

    produced = run_production_tick(p1, m, STANDARD, _next_id)
    assert len(produced) == 1
    army = produced[0]
    assert isinstance(army.standing_order, PatrolPath)

    # Left untouched, the engine's standing-orders phase walks it east.
    apply_standing_orders(p1, m, STANDARD, CombatResolver(), random.Random(0))
    assert army.coord == Coord(1, 0)

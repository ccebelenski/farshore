"""Phase-2 canary tests for `City` and `ProductionState`."""

import pytest

from empire.core.city import City, DefaultOrder, OrderKind, ProductionState
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId
from empire.core.map import ViewMap
from empire.core.player import Player
from empire.core.unit import UnitKind


@pytest.fixture()
def owner() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


# --- City ---------------------------------------------------------------------


def test_neutral_city(owner: Player) -> None:
    del owner
    c = City(id=CityId(1), coord=Coord(5, 5), owner=None)
    assert c.is_neutral() is True


def test_owned_city(owner: Player) -> None:
    c = City(id=CityId(1), coord=Coord(5, 5), owner=owner)
    assert c.is_neutral() is False
    assert c.owner is owner


def test_default_order_is_sentry_when_unset() -> None:
    c = City(id=CityId(1), coord=Coord(0, 0), owner=None)
    assert c.default_order_for(UnitKind.ARMY).kind is OrderKind.SENTRY


def test_default_order_returns_configured_value() -> None:
    c = City(id=CityId(1), coord=Coord(0, 0), owner=None)
    c.default_orders[UnitKind.FIGHTER] = DefaultOrder(OrderKind.ATTACK_NEAREST_ENEMY)
    assert c.default_order_for(UnitKind.FIGHTER).kind is OrderKind.ATTACK_NEAREST_ENEMY
    assert c.default_order_for(UnitKind.ARMY).kind is OrderKind.SENTRY


# --- ProductionState ----------------------------------------------------------


def test_production_state_default_idle() -> None:
    p = ProductionState()
    assert p.building is None
    assert p.work == 0
    assert p.ready() is False


def test_setting_target_from_idle_keeps_the_accumulator() -> None:
    p = ProductionState()
    p.set_target(UnitKind.ARMY)
    assert p.building is UnitKind.ARMY
    assert p.work == 0


def test_setting_same_target_does_nothing() -> None:
    p = ProductionState(building=UnitKind.ARMY, work=3)
    p.set_target(UnitKind.ARMY)
    assert p.work == 3


def test_changing_target_discards_all_work() -> None:
    """Effort toward one unit does not carry to another: a switch dumps it."""
    p = ProductionState(building=UnitKind.BATTLESHIP, work=20)
    p.set_target(UnitKind.ARMY)
    assert p.building is UnitKind.ARMY
    assert p.work == 0


def test_tick_accumulates_work() -> None:
    p = ProductionState(building=UnitKind.ARMY, work=0)
    p.tick()
    p.tick()
    p.tick()
    assert p.work == 3


def test_tick_with_no_target_does_nothing() -> None:
    p = ProductionState()
    p.tick()
    assert p.work == 0


def test_ready_when_work_meets_build_time() -> None:
    """Army has build_time=5."""
    p = ProductionState(building=UnitKind.ARMY, work=5)
    assert p.ready() is True


def test_not_ready_below_build_time() -> None:
    p = ProductionState(building=UnitKind.ARMY, work=4)
    assert p.ready() is False


def test_consume_deducts_build_time() -> None:
    p = ProductionState(building=UnitKind.ARMY, work=7)  # build_time=5
    p.consume()
    assert p.work == 2
    assert p.building is UnitKind.ARMY


def test_consume_at_exact_build_time_leaves_zero_work() -> None:
    """Boundary: when accumulated work exactly equals build_time, one consume
    drops work to 0 — not negative, not positive. The city is then ready to
    start fresh on the same target."""
    p = ProductionState(building=UnitKind.ARMY, work=5)  # build_time=5
    assert p.ready()
    p.consume()
    assert p.work == 0
    assert p.building is UnitKind.ARMY
    assert not p.ready()  # back below threshold


def test_consume_with_no_target_is_safe_noop() -> None:
    """Calling consume() when no production target is set should not raise."""
    p = ProductionState()
    p.consume()
    assert p.work == 0
    assert p.building is None

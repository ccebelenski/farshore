"""Phase-3 canary tests for `TurnPlan` and its components."""

import dataclasses

import pytest

from empire.contracts.turn_plan import (
    ProductionOrder,
    SetOrder,
    TurnPlan,
    UnitMove,
    UnitSentry,
)
from empire.core.coord import Direction
from empire.core.identity import CityId, UnitId
from empire.core.standing_order import Heading, Sentry
from empire.core.unit import UnitKind


def test_empty_turn_plan() -> None:
    p = TurnPlan()
    assert p.production_orders == ()
    assert p.moves == ()
    assert p.sentries == ()
    assert p.set_orders == ()
    assert p.notes == {}


def test_set_orders_carries_standing_orders() -> None:
    p = TurnPlan(
        set_orders=(
            SetOrder(unit_id=UnitId(1), order=Heading(Direction.N)),
            SetOrder(unit_id=UnitId(2), order=Sentry()),
            SetOrder(unit_id=UnitId(3), order=None),  # clear
        ),
    )
    assert len(p.set_orders) == 3
    assert isinstance(p.set_orders[0].order, Heading)
    assert p.set_orders[2].order is None


def test_turn_plan_with_payload() -> None:
    p = TurnPlan(
        production_orders=(ProductionOrder(city_id=CityId(1), target=UnitKind.ARMY),),
        moves=(UnitMove(unit_id=UnitId(2), path=((1, 1), (1, 2))),),
        sentries=(UnitSentry(unit_id=UnitId(3), wake=True),),
        notes={"debug": "test"},
    )
    assert len(p.production_orders) == 1
    assert p.moves[0].unit_id == UnitId(2)
    assert p.sentries[0].wake is True


def test_unit_move_default_is_empty_path() -> None:
    m = UnitMove(unit_id=UnitId(1))
    assert m.path == ()


def test_production_order_can_clear_target() -> None:
    o = ProductionOrder(city_id=CityId(1), target=None)
    assert o.target is None


def test_turn_plan_components_are_frozen() -> None:
    p = TurnPlan()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.production_orders = ()  # type: ignore[misc]


def test_unit_move_is_frozen() -> None:
    m = UnitMove(unit_id=UnitId(1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.unit_id = UnitId(2)  # type: ignore[misc]


def test_production_order_is_hashable() -> None:
    """Frozen dataclasses with hashable fields are hashable; useful for sets."""
    a = ProductionOrder(city_id=CityId(1), target=UnitKind.ARMY)
    b = ProductionOrder(city_id=CityId(1), target=UnitKind.ARMY)
    assert hash(a) == hash(b)
    assert {a, b} == {a}

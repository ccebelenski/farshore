"""Phase-3 canary tests for `TurnPlan` and its components."""

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


def test_default_construction_uses_empty_collections() -> None:
    p = TurnPlan()
    assert p.production_orders == ()
    assert p.moves == ()
    assert p.sentries == ()
    assert p.set_orders == ()
    assert p.notes == {}
    # Component defaults (folded in from their own one-line tests).
    assert UnitMove(unit_id=UnitId(1)).path == ()
    assert ProductionOrder(city_id=CityId(1), target=None).target is None


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

"""Phase-1 canary tests for identity NewTypes."""

from empire.core.identity import CityId, GoalId, PlayerId, TaskForceId, UnitId


def test_newtype_constructors_yield_underlying_int() -> None:
    assert UnitId(1) == 1
    assert CityId(2) == 2
    assert PlayerId(3) == 3
    assert TaskForceId(4) == 4
    assert GoalId(5) == 5


def test_five_distinct_identity_types_exist() -> None:
    assert len({UnitId, CityId, PlayerId, TaskForceId, GoalId}) == 5

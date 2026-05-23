"""Phase-1 canary tests for identity NewTypes.

NewType has no runtime behavior, so there's not much to test at runtime —
pyright is what enforces non-mixing. We do verify that the five types are
distinct symbols (catches an accidental alias where two names point to the
same NewType).
"""

from empire.core.identity import CityId, GoalId, PlayerId, TaskForceId, UnitId


def test_five_distinct_identity_types_exist() -> None:
    assert len({UnitId, CityId, PlayerId, TaskForceId, GoalId}) == 5

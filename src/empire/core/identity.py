"""Strongly-typed identity newtypes.

Each entity in the domain model has an integer id distinguished by type so that
mixing them up (e.g. passing a CityId where a UnitId is expected) is a type
error rather than a silent bug.
"""

from typing import NewType

UnitId = NewType("UnitId", int)
CityId = NewType("CityId", int)
PlayerId = NewType("PlayerId", int)
TaskForceId = NewType("TaskForceId", int)
GoalId = NewType("GoalId", int)

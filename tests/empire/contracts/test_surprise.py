"""Phase-3 canary tests for the `Surprise` tagged union and its variants."""

import dataclasses

import pytest

from empire.contracts.surprise import (
    BlockedBy,
    EnemySighted,
    EscortLost,
    PathBlocked,
    Surprise,
    TargetLost,
    TerrainImpassable,
)
from empire.contracts.world_view import KnownEnemyUnit
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import UnitSnapshot
from empire.core.unit import UnitKind


def _snapshot() -> UnitSnapshot:
    return UnitSnapshot(
        unit_id=UnitId(1),
        kind=UnitKind.ARMY,
        owner_id=PlayerId(2),
        coord=Coord(3, 3),
        hits=1,
    )


def test_enemy_sighted_construction() -> None:
    s = EnemySighted(enemy=KnownEnemyUnit(_snapshot(), seen_at_turn=4), at=Coord(3, 3))
    assert isinstance(s, Surprise)
    assert s.at == Coord(3, 3)


def test_path_blocked_construction() -> None:
    s = PathBlocked(blocked_at=Coord(2, 2), by=BlockedBy.ENEMY_UNIT)
    assert s.by is BlockedBy.ENEMY_UNIT


def test_target_lost_with_city_id() -> None:
    s = TargetLost(target_id=CityId(5))
    assert s.target_id == 5


def test_target_lost_with_unit_id() -> None:
    s = TargetLost(target_id=UnitId(7))
    assert s.target_id == 7


def test_escort_lost_construction() -> None:
    s = EscortLost(escort_id=UnitId(42))
    assert s.escort_id == 42


def test_terrain_impassable_construction() -> None:
    s = TerrainImpassable(at=Coord(1, 1))
    assert s.at == Coord(1, 1)


def test_all_concrete_surprises_inherit_from_marker() -> None:
    for cls in (EnemySighted, PathBlocked, TargetLost, EscortLost, TerrainImpassable):
        assert issubclass(cls, Surprise)


def test_concrete_surprises_are_frozen() -> None:
    s = PathBlocked(blocked_at=Coord(0, 0), by=BlockedBy.TERRAIN)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.blocked_at = Coord(1, 1)  # type: ignore[misc]


def test_blocked_by_enum_values() -> None:
    assert {b.value for b in BlockedBy} == {"own_unit", "enemy_unit", "terrain", "out_of_bounds"}

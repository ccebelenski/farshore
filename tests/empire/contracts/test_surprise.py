"""Phase-3 canary tests for the `Surprise` tagged union and its variants."""

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


def test_target_lost_accepts_both_city_and_unit_ids() -> None:
    """TargetLost.target_id is a union of CityId and UnitId; verify both work
    since this is a real polymorphism point AI code will pattern-match on."""
    assert TargetLost(target_id=CityId(5)).target_id == 5
    assert TargetLost(target_id=UnitId(7)).target_id == 7


def test_every_variant_constructs_and_inherits_from_marker() -> None:
    """One parametrized check that all five variants instantiate AND are
    recognized as Surprise. Replaces five near-identical construction tests.
    """
    enemy = KnownEnemyUnit(_snapshot(), seen_at_turn=4)
    instances: list[Surprise] = [
        EnemySighted(enemy=enemy, at=Coord(3, 3)),
        PathBlocked(blocked_at=Coord(2, 2), by=BlockedBy.ENEMY_UNIT),
        TargetLost(target_id=CityId(5)),
        EscortLost(escort_id=UnitId(42)),
        TerrainImpassable(at=Coord(1, 1)),
    ]
    for s in instances:
        assert isinstance(s, Surprise)


def test_blocked_by_enum_values() -> None:
    """Pinned for save-file / wire-format stability."""
    assert {b.value for b in BlockedBy} == {"own_unit", "enemy_unit", "terrain", "out_of_bounds"}

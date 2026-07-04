"""One immutability contract for the core frozen value types.

Consolidates the per-module `test_*_is_frozen` canaries that each re-checked
`@dataclass(frozen=True)` in isolation. Mutating any of these value types must
raise, so single-owner code can hold them without defensive copying.
"""

import dataclasses
from typing import Any

import pytest

from empire.contracts.surprise import BlockedBy, PathBlocked
from empire.contracts.turn_plan import TurnPlan, UnitMove
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.ruleset import STANDARD, STANDARD_PROFILE
from empire.core.tile import TerrainKind, Tile


@pytest.mark.parametrize(
    ("obj", "attr", "value"),
    [
        (Tile(coord=Coord(0, 0), terrain=TerrainKind.WATER), "terrain", TerrainKind.LAND),
        (STANDARD, "name", "MODERN"),
        (STANDARD_PROFILE, "width", 999),
        (TurnPlan(), "production_orders", ()),
        (UnitMove(unit_id=UnitId(1)), "unit_id", UnitId(2)),
        (PathBlocked(blocked_at=Coord(0, 0), by=BlockedBy.TERRAIN), "blocked_at", Coord(1, 1)),
    ],
    ids=["Tile", "RuleSet", "MapProfile", "TurnPlan", "UnitMove", "PathBlocked"],
)
def test_core_value_types_are_frozen(obj: Any, attr: str, value: Any) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, attr, value)

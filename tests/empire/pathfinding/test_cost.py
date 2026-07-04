"""Phase-7 canary tests for `PathCostProfile` and its presets."""

import pytest

from empire.core.tile import TerrainKind
from empire.pathfinding.cost import AIR, ARMY, SEA, PathCostProfile

_LAND, _WATER, _CITY = TerrainKind.LAND, TerrainKind.WATER, TerrainKind.CITY


@pytest.mark.parametrize(
    ("profile", "terrain", "expected"),
    [
        (ARMY, _LAND, 1), (ARMY, _CITY, 1), (ARMY, _WATER, None),
        (SEA, _WATER, 1), (SEA, _CITY, 1), (SEA, _LAND, None),
        (AIR, _LAND, 1), (AIR, _WATER, 1), (AIR, _CITY, 1),
    ],
)
def test_preset_traversal_costs(
    profile: PathCostProfile, terrain: TerrainKind, expected: int | None
) -> None:
    """Each mover's `cost_for` over every terrain: 1 to enter, None if barred."""
    assert profile.cost_for(terrain) == expected


def test_custom_profile_applies_its_own_terrain_costs() -> None:
    p = PathCostProfile(land_cost=2, water_cost=None, city_cost=3, danger_weight=1.5)
    assert p.cost_for(TerrainKind.LAND) == 2
    assert p.cost_for(TerrainKind.CITY) == 3
    assert p.cost_for(TerrainKind.WATER) is None

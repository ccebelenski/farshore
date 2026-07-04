"""Phase-7 canary tests for `PathCostProfile` and its presets."""

from empire.core.tile import TerrainKind
from empire.pathfinding.cost import AIR, ARMY, SEA, PathCostProfile


def test_army_cannot_traverse_water() -> None:
    assert ARMY.cost_for(TerrainKind.WATER) is None


def test_army_traverses_land_and_city() -> None:
    assert ARMY.cost_for(TerrainKind.LAND) == 1
    assert ARMY.cost_for(TerrainKind.CITY) == 1


def test_sea_cannot_traverse_land() -> None:
    assert SEA.cost_for(TerrainKind.LAND) is None


def test_sea_traverses_water_and_city() -> None:
    assert SEA.cost_for(TerrainKind.WATER) == 1
    assert SEA.cost_for(TerrainKind.CITY) == 1


def test_air_traverses_everything() -> None:
    assert AIR.cost_for(TerrainKind.LAND) == 1
    assert AIR.cost_for(TerrainKind.WATER) == 1
    assert AIR.cost_for(TerrainKind.CITY) == 1


def test_custom_profile_with_danger_weight() -> None:
    p = PathCostProfile(land_cost=2, water_cost=None, city_cost=3, danger_weight=1.5)
    assert p.cost_for(TerrainKind.LAND) == 2
    assert p.cost_for(TerrainKind.CITY) == 3
    assert p.danger_weight == 1.5

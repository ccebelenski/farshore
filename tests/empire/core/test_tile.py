"""Phase-1 canary tests for `TerrainKind`. The `Tile` class itself lands in Phase 2."""

from empire.core.tile import TerrainKind


def test_three_terrain_kinds_exist() -> None:
    assert {k.name for k in TerrainKind} == {"LAND", "WATER", "CITY"}


def test_terrain_kind_values_are_stable_strings() -> None:
    """Save-file representation: short, lowercase, human-readable."""
    assert TerrainKind.LAND.value == "land"
    assert TerrainKind.WATER.value == "water"
    assert TerrainKind.CITY.value == "city"


def test_terrain_kind_lookup_by_value() -> None:
    assert TerrainKind("land") is TerrainKind.LAND

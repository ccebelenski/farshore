"""Phase-1+2 canary tests for `TerrainKind` and `Tile`."""

from empire.core.coord import Coord
from empire.core.tile import TerrainKind, Tile

# --- TerrainKind --------------------------------------------------------------


def test_three_terrain_kinds_exist() -> None:
    assert {k.name for k in TerrainKind} == {"LAND", "WATER", "CITY"}


def test_terrain_kind_values_are_stable_strings() -> None:
    """Save-file representation: short, lowercase, human-readable."""
    assert TerrainKind.LAND.value == "land"
    assert TerrainKind.WATER.value == "water"
    assert TerrainKind.CITY.value == "city"


def test_terrain_kind_lookup_by_value() -> None:
    assert TerrainKind("land") is TerrainKind.LAND


# --- Tile ---------------------------------------------------------------------


def test_tile_constructible_with_defaults() -> None:
    t = Tile(coord=Coord(3, 4), terrain=TerrainKind.LAND)
    assert t.coord == Coord(3, 4)
    assert t.terrain is TerrainKind.LAND
    assert t.city is None
    assert t.on_board is True


def test_tile_is_frozen() -> None:
    import dataclasses

    import pytest

    t = Tile(coord=Coord(0, 0), terrain=TerrainKind.WATER)
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.terrain = TerrainKind.LAND  # type: ignore[misc]


def test_off_board_tile() -> None:
    t = Tile(coord=Coord(0, 0), terrain=TerrainKind.WATER, on_board=False)
    assert t.on_board is False

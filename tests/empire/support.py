"""Shared test-map builders: ASCII rows -> `Map`.

The one rows-to-Map constructor for unit tests and set-piece scenarios
(it used to be copied per test file). Each char: L=land, W=water, C=city.
"""

from __future__ import annotations

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.map import Map
from empire.core.tile import TerrainKind, Tile

_TERRAIN = {"L": TerrainKind.LAND, "W": TerrainKind.WATER, "C": TerrainKind.CITY}


def build_map(rows: list[str], cities: dict[Coord, City] | None = None) -> Map:
    """Build a Map from ASCII `rows`; `cities` places City objects (the tile
    becomes CITY terrain holding that city, overriding the row char)."""
    cities = cities or {}
    height, width = len(rows), len(rows[0])
    tiles: dict[Coord, Tile] = {}
    for y in range(height):
        for x in range(width):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=_TERRAIN[rows[y][x]])
    return Map(width=width, height=height, tiles=tiles)


def land_map(
    width: int, height: int, cities: dict[Coord, City] | None = None
) -> Map:
    """An all-LAND rectangle, with optional cities placed on it."""
    return build_map(["L" * width] * height, cities)

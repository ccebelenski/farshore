"""Builders for canned `WorldView` scenarios used by the intel canary tests.

A scenario is a small ASCII grid:

    "."  land        "~"  water        "C"  city (supply the City via `cities`)

Place units with `place`, then reveal the cells the player can see with
`reveal` (intel only sees visible-or-remembered cells).
"""

from __future__ import annotations

from empire.contracts.world_view import WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import PlayerId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Unit

_TERRAIN = {".": TerrainKind.LAND, "~": TerrainKind.WATER}


def player(pid: int, name: str = "P") -> Player:
    return Player(id=PlayerId(pid), name=name, is_ai=True, view=ViewMap())


def grid_map(rows: list[str], cities: dict[Coord, City] | None = None) -> Map:
    """Build a `Map` from an ASCII grid. `cities` keys must be city cells."""
    cities = cities or {}
    height = len(rows)
    width = len(rows[0])
    tiles: dict[Coord, Tile] = {}
    for y, row in enumerate(rows):
        assert len(row) == width, "ragged grid"
        for x, ch in enumerate(row):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=_TERRAIN[ch])
    return Map(width=width, height=height, tiles=tiles)


def place(real_map: Map, unit: Unit) -> Unit:
    real_map.place_unit(unit, unit.coord)
    return unit


def reveal(p: Player, *coords: Coord) -> None:
    p.view.visible.update(coords)


def reveal_all(p: Player, real_map: Map) -> None:
    """Mark the whole board visible — for terrain-only scenarios."""
    for y in range(real_map.height):
        for x in range(real_map.width):
            p.view.visible.add(Coord(x, y))


def world(real_map: Map, p: Player, turn: int = 1) -> WorldView:
    return WorldView(real_map=real_map, player=p, turn=turn, rules=STANDARD)

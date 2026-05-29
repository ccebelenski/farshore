"""Terrain kinds and the `Tile` value type."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from empire.core.coord import Coord

if TYPE_CHECKING:
    from empire.core.city import City


class TerrainKind(Enum):
    """The three terrain kinds. See `planning/01-game-rules-spec.md` §1.2.

    Values are short strings so save-file representations are stable across
    schema versions and human-readable.
    """

    LAND = "land"
    WATER = "water"
    CITY = "city"


# Terrain a ground unit can stand on, and equivalently the terrain that counts
# as "land" for landmass/coastline reasoning (a city sits on land). Shared so
# mapgen, setup, and the AI's terrain analysis agree on one definition.
LAND_TERRAINS = frozenset({TerrainKind.LAND, TerrainKind.CITY})


@dataclass(frozen=True, slots=True)
class Tile:
    """A single cell of the map.

    Tile holds terrain and (optionally) a `City` reference; unit occupancy is
    tracked by `Map`'s spatial index, not on the tile (see
    `planning/04-class-hierarchy.md` §2).
    """

    coord: Coord
    terrain: TerrainKind
    city: City | None = None
    on_board: bool = True  # False for the unwalkable 1-cell border

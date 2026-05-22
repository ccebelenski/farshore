"""Tile terrain kinds. The `Tile` class itself lands in Phase 2."""

from enum import Enum


class TerrainKind(Enum):
    """The three terrain kinds. See `planning/01-game-rules-spec.md` §1.2.

    Values are short strings so that save-file representations are stable
    across schema versions and human-readable.
    """

    LAND = "land"
    WATER = "water"
    CITY = "city"

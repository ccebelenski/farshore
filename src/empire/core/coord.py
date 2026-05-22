"""Spatial primitives: coordinates and 8-directional movement."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum


class Direction(Enum):
    """Eight-directional movement vectors. Each member's value is (dx, dy)."""

    N = (0, -1)
    NE = (1, -1)
    E = (1, 0)
    SE = (1, 1)
    S = (0, 1)
    SW = (-1, 1)
    W = (-1, 0)
    NW = (-1, -1)

    @property
    def dx(self) -> int:
        return self.value[0]

    @property
    def dy(self) -> int:
        return self.value[1]

    @classmethod
    def offsets(cls) -> Iterator[tuple[int, int]]:
        """Yield all 8 (dx, dy) offsets in enum order."""
        for d in cls:
            yield d.value


@dataclass(frozen=True, slots=True)
class Coord:
    """An (x, y) cell coordinate. Immutable; hashable."""

    x: int
    y: int

    def step(self, d: Direction) -> Coord:
        return Coord(self.x + d.dx, self.y + d.dy)

    def neighbors(self) -> Iterator[Coord]:
        """Yield all 8 adjacent coordinates in `Direction` enum order."""
        for d in Direction:
            yield self.step(d)

    def chebyshev_to(self, other: Coord) -> int:
        """Chebyshev distance. One move per cell; diagonal costs the same as orthogonal."""
        return max(abs(self.x - other.x), abs(self.y - other.y))

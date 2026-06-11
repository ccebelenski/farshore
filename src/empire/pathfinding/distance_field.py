"""`PassabilityGrid` + `DistanceField`: all-targets distances from one flood.

`Pathfinder.find_path` answers one (start, goal) question per call; AI
scoring loops ask many goals about the same start (every army scores every
objective). For a uniform-cost profile, one breadth-first flood from the
unit answers *all* of its objectives — these classes compute that flood once
per unit, so a scoring loop pays O(cells) per unit instead of O(search) per
(unit, target).

Exactness contract (relied on by `BaselineTactical` and verified in
`tests/empire/pathfinding/test_distance_field.py`): for a uniform-cost
profile and any `goal != origin`, `DistanceField(origin, ...).steps_to(goal)`
equals `Pathfinder.find_path(origin, goal, ...).steps`, and is `None` exactly
when `find_path` returns `None`. The flood mirrors `find_path`'s passability
rules: the origin itself need not be passable (`find_path` never checks its
start), every *entered* cell must be — including the goal — out-of-bounds
cells are never entered, and unseen cells (when a `view` is given) are
passable iff `profile.unknown_cost` is not `None`.

`path_to` reconstructs a shortest path by descending the field. Its route is
*a* minimum-step path with deterministic (Direction-enum-order) tie-breaks —
not necessarily the same cells `find_path`'s heap order would pick. Length
and reachability are identical; only equal-length route choice may differ.
"""

from __future__ import annotations

from collections import deque

from empire.core.coord import Coord, Direction
from empire.core.map import Map, ViewMap
from empire.pathfinding.cost import PathCostProfile

_OFFSETS: tuple[tuple[int, int], ...] = tuple(Direction.offsets())


class PassabilityGrid:
    """Which cells a unit with `profile` may enter, as one flat bitmap.

    Computed once per (map, profile, fog snapshot) and shared by every
    `DistanceField` built from it — passability does not depend on the
    flood's origin. Immutable after construction.
    """

    def __init__(
        self,
        real_map: Map,
        profile: PathCostProfile,
        view: ViewMap | None = None,
    ) -> None:
        for cost in (
            profile.land_cost,
            profile.water_cost,
            profile.city_cost,
            profile.unknown_cost,
        ):
            if cost is not None and cost != 1:
                raise ValueError(
                    "DistanceField requires a uniform-cost profile "
                    "(every passable cost == 1); got a non-unit cost. "
                    "Use Pathfinder.find_path for weighted profiles."
                )
        self.width: int = real_map.width
        self.height: int = real_map.height
        unknown_passable = profile.unknown_cost is not None
        flags: list[bool] = []
        for y in range(self.height):
            for x in range(self.width):
                c = Coord(x, y)
                if view is not None and not view.seen(c):
                    flags.append(unknown_passable)
                else:
                    flags.append(profile.cost_for(real_map.terrain_at(c)) is not None)
        self._flags: tuple[bool, ...] = tuple(flags)

    @property
    def flags(self) -> tuple[bool, ...]:
        """Row-major passability bitmap (index = y * width + x)."""
        return self._flags

    def is_passable(self, c: Coord) -> bool:
        if not (0 <= c.x < self.width and 0 <= c.y < self.height):
            return False
        return self._flags[c.y * self.width + c.x]


class DistanceField:
    """Minimum step count from one origin to every cell, via one BFS flood.

    Distances are exact shortest-path step counts under 8-directional
    movement: every cell on a path except the origin must be passable in
    `grid` (matching `find_path`, which never checks its start cell but must
    enter every other cell, the goal included). Immutable after construction.
    """

    def __init__(self, origin: Coord, grid: PassabilityGrid) -> None:
        width = grid.width
        height = grid.height
        dist: list[int] = [-1] * (width * height)
        self._origin: Coord = origin
        self._width: int = width
        self._height: int = height
        self._dist: list[int] = dist
        if not (0 <= origin.x < width and 0 <= origin.y < height):
            return
        flags = grid.flags
        start = origin.y * width + origin.x
        dist[start] = 0
        queue: deque[int] = deque((start,))
        while queue:
            i = queue.popleft()
            next_d = dist[i] + 1
            x = i % width
            y = i // width
            for dx, dy in _OFFSETS:
                nx = x + dx
                ny = y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    j = ny * width + nx
                    if dist[j] < 0 and flags[j]:
                        dist[j] = next_d
                        queue.append(j)

    def steps_to(self, goal: Coord) -> int | None:
        """Shortest step count from the origin to `goal`; `None` if unreachable."""
        if not (0 <= goal.x < self._width and 0 <= goal.y < self._height):
            return None
        d = self._dist[goal.y * self._width + goal.x]
        return None if d < 0 else d

    def path_to(self, goal: Coord) -> tuple[Coord, ...] | None:
        """A shortest path origin → `goal` (inclusive), descending the field.

        `None` iff `goal` is unreachable. Ties between equal-length routes
        break in `Direction` enum order, deterministically.
        """
        d = self.steps_to(goal)
        if d is None:
            return None
        width = self._width
        dist = self._dist
        cells: list[Coord] = [goal]
        current = goal
        while d > 0:
            stepped = False
            for dx, dy in _OFFSETS:
                nx = current.x + dx
                ny = current.y + dy
                if (
                    0 <= nx < width
                    and 0 <= ny < self._height
                    and dist[ny * width + nx] == d - 1
                ):
                    current = Coord(nx, ny)
                    cells.append(current)
                    d -= 1
                    stepped = True
                    break
            # Every flooded cell at d > 0 has a d-1 neighbor by construction.
            assert stepped, "BFS field invariant violated"
        cells.reverse()
        return tuple(cells)

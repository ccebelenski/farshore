"""`HeightFieldMapGenerator`: smoothed height-field map generation.

Algorithm (designed for this project; see
`planning/01-game-rules-spec.md` §9.1 and
`planning/04-class-hierarchy.md` §5):

1. Each interior cell gets a uniform random height in `[0, 1)`.
2. Smooth the field over `smooth_iterations` passes (3x3 box blur,
   in-bounds-only averaging so edges naturally trend toward their
   neighbors' values).
3. Choose a threshold from the sorted interior heights so that, combined
   with the always-water border ring, the total water fraction matches
   `water_ratio`. Cells at-or-above the threshold are LAND; below are
   WATER.
4. The outermost-ring cells are unconditionally off-board water (spec
   §1.1) regardless of the height field.
5. Place cities greedily by shuffling interior LAND cells and accepting
   each one whose Chebyshev distance to every already-placed city is
   `>= min_city_distance`. Stop when `num_cities` placed or the
   shuffled candidates are exhausted.
6. Verify every connected land component that contains cities has at
   least one coastal city (a city adjacent to a water tile). A
   landlocked continent's cities are unconquerable by sea, which
   trivializes the game — fail this attempt if any violation found.
7. If we couldn't place enough cities or any continent is landlocked,
   regenerate the height field and try again, up to
   `max_regen_attempts`. Failure raises `MapGenerationError`.
"""

from __future__ import annotations

import random
from typing import ClassVar

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId
from empire.core.map import Map
from empire.core.ruleset import MapProfile
from empire.core.tile import TerrainKind, Tile
from empire.mapgen.generator import MapGenerationError, MapGenerator


class HeightFieldMapGenerator(MapGenerator):
    """Generates Empire maps via smoothed height-field thresholding."""

    DEFAULT_MAX_ATTEMPTS: ClassVar[int] = 20

    def __init__(self, max_regen_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        if max_regen_attempts < 1:
            raise ValueError("max_regen_attempts must be >= 1")
        self._max_regen_attempts: int = max_regen_attempts
        self._last_regen_count: int = 0

    @property
    def last_regen_count(self) -> int:
        """Number of regenerations the most recent `generate()` required.

        Zero means the first attempt succeeded. Useful for diagnostics and
        regression tests on the generator's reliability.
        """
        return self._last_regen_count

    def generate(self, profile: MapProfile, rng: random.Random) -> tuple[Map, list[City]]:
        self._last_regen_count = 0
        for attempt in range(self._max_regen_attempts):
            terrain_grid, city_coords = self._try_one(profile, rng)
            if (
                len(city_coords) >= profile.num_cities
                and self._all_continents_have_coastal_cities(
                    terrain_grid, city_coords[: profile.num_cities], profile,
                )
            ):
                return self._assemble(terrain_grid, city_coords[: profile.num_cities], profile)
            self._last_regen_count = attempt + 1
        raise MapGenerationError(
            f"Could not produce valid map for profile "
            f"{profile.width}x{profile.height} water={profile.water_ratio}% "
            f"min_distance={profile.min_city_distance} num_cities={profile.num_cities} "
            f"after {self._max_regen_attempts} attempts. Either packing failed "
            f"(not enough room) or every attempt produced a landlocked "
            f"continent. Relax water_ratio, num_cities, or min_city_distance."
        )

    # ---- one attempt ------------------------------------------------------

    def _try_one(
        self, profile: MapProfile, rng: random.Random,
    ) -> tuple[list[list[TerrainKind]], list[Coord]]:
        heights = self._initial_heights(profile, rng)
        for _ in range(profile.smooth_iterations):
            heights = self._smooth(heights, profile.width, profile.height)
        threshold = self._compute_threshold(heights, profile)
        terrain_grid = self._build_terrain_grid(heights, threshold, profile)
        city_coords = self._place_cities(terrain_grid, profile, rng)
        return terrain_grid, city_coords

    # ---- height-field operations ------------------------------------------

    def _initial_heights(self, profile: MapProfile, rng: random.Random) -> list[list[float]]:
        return [
            [rng.random() for _ in range(profile.width)]
            for _ in range(profile.height)
        ]

    def _smooth(
        self, heights: list[list[float]], width: int, height: int,
    ) -> list[list[float]]:
        new: list[list[float]] = [[0.0] * width for _ in range(height)]
        for y in range(height):
            for x in range(width):
                total = 0.0
                count = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < height and 0 <= nx < width:
                            total += heights[ny][nx]
                            count += 1
                new[y][x] = total / count
        return new

    def _compute_threshold(self, heights: list[list[float]], profile: MapProfile) -> float:
        """Pick the height threshold so total water (border + below-threshold
        interior) approximates `water_ratio` of the whole map.
        """
        total_cells = profile.width * profile.height
        border_cells = 2 * profile.width + 2 * (profile.height - 2)
        target_water = int(total_cells * profile.water_ratio / 100)
        interior_water_target = max(0, target_water - border_cells)

        interior_heights: list[float] = sorted(
            heights[y][x]
            for y in range(1, profile.height - 1)
            for x in range(1, profile.width - 1)
        )
        if not interior_heights:
            return 0.0
        idx = max(0, min(len(interior_heights) - 1, interior_water_target))
        return interior_heights[idx]

    def _build_terrain_grid(
        self, heights: list[list[float]], threshold: float, profile: MapProfile,
    ) -> list[list[TerrainKind]]:
        grid: list[list[TerrainKind]] = [
            [TerrainKind.WATER] * profile.width for _ in range(profile.height)
        ]
        for y in range(profile.height):
            for x in range(profile.width):
                # Border is always water (spec §1.1).
                if x == 0 or x == profile.width - 1 or y == 0 or y == profile.height - 1:
                    continue
                if heights[y][x] >= threshold:
                    grid[y][x] = TerrainKind.LAND
        return grid

    # ---- city placement ---------------------------------------------------

    def _place_cities(
        self,
        terrain_grid: list[list[TerrainKind]],
        profile: MapProfile,
        rng: random.Random,
    ) -> list[Coord]:
        """Greedy placement biased toward coastal cells.

        Candidates are shuffled then stable-sorted with coastal cells first.
        This naturally satisfies the "every continent with cities has at
        least one coastal city" invariant — by the time we run out of
        coastal candidates and start placing inland, any continent that
        was going to get cities has already gotten its coastal one.

        Inland cities are still placed for the remaining slots (after
        all coastal candidates have been consumed or are too close to
        existing cities).
        """
        candidates: list[Coord] = [
            Coord(x, y)
            for y in range(1, profile.height - 1)
            for x in range(1, profile.width - 1)
            if terrain_grid[y][x] is TerrainKind.LAND
        ]
        rng.shuffle(candidates)
        # Stable sort: coastal first, inland second. Within each group the
        # random order from shuffle is preserved.
        candidates.sort(
            key=lambda c: 0 if self._is_coastal_land(terrain_grid, c, profile) else 1
        )

        placed: list[Coord] = []
        d = profile.min_city_distance
        for c in candidates:
            if len(placed) >= profile.num_cities:
                break
            if all(c.chebyshev_to(p) >= d for p in placed):
                placed.append(c)
        return placed

    @staticmethod
    def _is_coastal_land(
        terrain_grid: list[list[TerrainKind]],
        coord: Coord,
        profile: MapProfile,
    ) -> bool:
        """True if `coord` is a land cell with at least one water neighbor."""
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = coord.x + dx, coord.y + dy
                if (
                    0 <= nx < profile.width
                    and 0 <= ny < profile.height
                    and terrain_grid[ny][nx] is TerrainKind.WATER
                ):
                    return True
        return False

    # ---- continent / coastal validation ----------------------------------

    def _all_continents_have_coastal_cities(
        self,
        terrain_grid: list[list[TerrainKind]],
        city_coords: list[Coord],
        profile: MapProfile,
    ) -> bool:
        """Every connected land component containing at least one city must
        have at least one city adjacent (8-direction) to a water tile.

        Reasons this matters:
        - Per spec §3.2, sea units may only occupy a CITY tile when it is
          "adjacent to water (treated as a port)." A continent with only
          inland cities literally cannot build or host transports — anyone
          starting there is stranded.
        - Symmetrically, an enemy cannot invade such a continent by sea,
          making its cities unconquerable and the game uninteresting.
        """
        if not city_coords:
            return True

        width, height = profile.width, profile.height

        def in_bounds(x: int, y: int) -> bool:
            return 0 <= x < width and 0 <= y < height

        def component_of(start_x: int, start_y: int) -> set[tuple[int, int]]:
            """BFS the connected LAND component containing (start_x, start_y)."""
            comp: set[tuple[int, int]] = set()
            stack = [(start_x, start_y)]
            while stack:
                x, y = stack.pop()
                if (x, y) in comp or not in_bounds(x, y):
                    continue
                # At this stage city locations are tracked in city_coords;
                # terrain_grid still has LAND/WATER only (CITY is baked in
                # during _build_tiles).
                if terrain_grid[y][x] is not TerrainKind.LAND:
                    continue
                comp.add((x, y))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((x + dx, y + dy))
            return comp

        visited: set[tuple[int, int]] = set()
        for city_coord in city_coords:
            key = (city_coord.x, city_coord.y)
            if key in visited:
                continue
            comp = component_of(city_coord.x, city_coord.y)
            visited.update(comp)

            # Cities in this component.
            cities_in_comp = [c for c in city_coords if (c.x, c.y) in comp]

            # At least one must be coastal.
            has_coastal = False
            for city in cities_in_comp:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = city.x + dx, city.y + dy
                        if in_bounds(nx, ny) and terrain_grid[ny][nx] is TerrainKind.WATER:
                            has_coastal = True
                            break
                    if has_coastal:
                        break
                if has_coastal:
                    break

            if not has_coastal:
                return False

        return True

    # ---- assembly ---------------------------------------------------------

    def _assemble(
        self,
        terrain_grid: list[list[TerrainKind]],
        city_coords: list[Coord],
        profile: MapProfile,
    ) -> tuple[Map, list[City]]:
        cities: list[City] = [
            City(id=CityId(i + 1), coord=c, owner=None)
            for i, c in enumerate(city_coords)
        ]
        cities_by_coord = {c.coord: c for c in cities}
        tiles = self._build_tiles(terrain_grid, cities_by_coord, profile)
        real_map = Map(width=profile.width, height=profile.height, tiles=tiles)
        return real_map, cities

    def _build_tiles(
        self,
        terrain_grid: list[list[TerrainKind]],
        cities_by_coord: dict[Coord, City],
        profile: MapProfile,
    ) -> dict[Coord, Tile]:
        tiles: dict[Coord, Tile] = {}
        for y in range(profile.height):
            for x in range(profile.width):
                c = Coord(x, y)
                on_board = not (
                    x == 0 or x == profile.width - 1 or y == 0 or y == profile.height - 1
                )
                if c in cities_by_coord:
                    tiles[c] = Tile(
                        coord=c, terrain=TerrainKind.CITY,
                        city=cities_by_coord[c], on_board=on_board,
                    )
                else:
                    tiles[c] = Tile(
                        coord=c, terrain=terrain_grid[y][x],
                        city=None, on_board=on_board,
                    )
        return tiles

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
6. If we couldn't place enough cities, regenerate the height field and
   try again, up to `max_regen_attempts`. Failure raises
   `MapGenerationError`.
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
            if len(city_coords) >= profile.num_cities:
                # Only keep the requested count (placement may have produced more
                # only theoretically; it stops at num_cities by construction).
                return self._assemble(terrain_grid, city_coords[: profile.num_cities], profile)
            self._last_regen_count = attempt + 1
        raise MapGenerationError(
            f"Could not place {profile.num_cities} cities for profile "
            f"{profile.width}x{profile.height} water={profile.water_ratio}% "
            f"min_distance={profile.min_city_distance} after "
            f"{self._max_regen_attempts} attempts. Relax water_ratio, "
            f"num_cities, or min_city_distance."
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
        """Greedy random placement: shuffle candidates, accept each one whose
        Chebyshev distance to every already-placed city is `>= min_city_distance`.
        """
        candidates: list[Coord] = [
            Coord(x, y)
            for y in range(1, profile.height - 1)
            for x in range(1, profile.width - 1)
            if terrain_grid[y][x] is TerrainKind.LAND
        ]
        rng.shuffle(candidates)

        placed: list[Coord] = []
        d = profile.min_city_distance
        for c in candidates:
            if len(placed) >= profile.num_cities:
                break
            if all(c.chebyshev_to(p) >= d for p in placed):
                placed.append(c)
        return placed

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

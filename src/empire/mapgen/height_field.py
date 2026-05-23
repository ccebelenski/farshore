"""`HeightFieldMapGenerator`: smoothed height-field map generation.

Algorithm (designed for this project; see
`planning/01-game-rules-spec.md` §9.1 and
`planning/04-class-hierarchy.md` §5):

1. Each interior cell gets a uniform random height in `[0, 1)`.
2. Smooth the field over `smooth_iterations` passes (3x3 box blur).
3. Choose a threshold from the sorted interior heights so that, combined
   with the always-water border ring, the total water fraction matches
   `water_ratio`.
4. The outermost-ring cells are unconditionally off-board water
   (spec §1.1).
5. Compute the **ocean** — water connected (8-direction) to the border.
   Water bodies not connected to the border are *inner seas* (lakes,
   landlocked basins). Cities adjacent only to inner seas can neither
   host transports (no path to the open sea) nor be invaded by sea, so
   they're game-breaking.
6. Filter city placement candidates to land cells in continents that
   touch the ocean. Inner-island continents (landmasses inside inner
   seas) get no cities at all.
7. Place cities greedily by shuffling the filtered candidates and
   accepting each one whose Chebyshev distance to every already-placed
   city is `>= min_city_distance`.
8. Touch up landlocked-within-continent cases: if a continent has cities
   but none are ocean-coastal, relocate one of its cities to an
   ocean-coastal cell within the same continent (which exists by step
   6's filter). Subject to the same min-distance constraint against
   other cities. Surgical — only the offending city moves.
9. As a safety net, verify every continent with cities has at least one
   ocean-coastal city. If touch-up couldn't fix everything (rare:
   coastal candidates blocked by neighbor distances), regenerate the
   height field and try again, up to `max_regen_attempts`.
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
        ocean = _compute_ocean(terrain_grid, profile)
        ocean_accessible_land = _compute_ocean_accessible_land(terrain_grid, ocean, profile)
        city_coords = self._place_cities(terrain_grid, profile, rng, ocean_accessible_land)
        city_coords = self._touch_up_landlocked(terrain_grid, city_coords, profile, ocean)
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
        ocean_accessible_land: set[tuple[int, int]],
    ) -> list[Coord]:
        """Greedy random placement restricted to ocean-accessible continents.

        Inner-island land (continents touching only inner seas, with no
        connection to the open ocean) is filtered out — cities placed
        there couldn't host transports or be reached by sea.
        """
        candidates: list[Coord] = [
            Coord(x, y)
            for y in range(1, profile.height - 1)
            for x in range(1, profile.width - 1)
            if terrain_grid[y][x] is TerrainKind.LAND
            and (x, y) in ocean_accessible_land
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

    # ---- touch-up: relocate landlocked cities to coastal cells -----------

    def _touch_up_landlocked(
        self,
        terrain_grid: list[list[TerrainKind]],
        city_coords: list[Coord],
        profile: MapProfile,
        ocean: set[tuple[int, int]],
    ) -> list[Coord]:
        """If any connected land component has cities but none are
        ocean-coastal, relocate one of that component's cities to an
        ocean-coastal cell on the same component.

        "Ocean-coastal" means adjacent to a water cell that's part of the
        ocean — water connected to the map border. Cities adjacent only
        to inner seas don't count; those continents have no naval access.

        Constraints on the replacement cell:
        - Must be in the same connected land component as the city being
          relocated.
        - Must have an ocean neighbor (8-direction).
        - Must be at least `min_city_distance` from every OTHER placed
          city (excluding the city being moved).
        - Must not coincide with an already-placed city.

        If no valid relocation exists for some component, the city list
        is returned with that component still landlocked.
        `_all_continents_have_coastal_cities` will then catch it and
        trigger regen.

        Determinism: components and candidates are processed in sorted
        order, so the same input produces the same output. No additional
        RNG draws are made.
        """
        if not city_coords:
            return list(city_coords)

        width, height = profile.width, profile.height
        d = profile.min_city_distance

        def in_bounds(x: int, y: int) -> bool:
            return 0 <= x < width and 0 <= y < height

        def component_of(start_x: int, start_y: int) -> set[tuple[int, int]]:
            comp: set[tuple[int, int]] = set()
            stack = [(start_x, start_y)]
            while stack:
                x, y = stack.pop()
                if (x, y) in comp or not in_bounds(x, y):
                    continue
                if terrain_grid[y][x] is not TerrainKind.LAND:
                    continue
                comp.add((x, y))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((x + dx, y + dy))
            return comp

        result: list[Coord] = list(city_coords)
        placed_set: set[Coord] = set(result)
        components_done: set[tuple[int, int]] = set()

        for seed_city in city_coords:
            key = (seed_city.x, seed_city.y)
            if key in components_done:
                continue
            comp = component_of(seed_city.x, seed_city.y)
            components_done.update(comp)

            cities_in_comp = [c for c in result if (c.x, c.y) in comp]
            # If any city in this component is ocean-coastal, nothing to fix.
            if any(_is_ocean_coastal(c, ocean) for c in cities_in_comp):
                continue

            # Find candidate ocean-coastal cells in this component
            # (excluding cells already occupied by a city).
            coastal_candidates = sorted(
                (
                    Coord(x, y)
                    for (x, y) in comp
                    if Coord(x, y) not in placed_set
                    and _is_ocean_coastal(Coord(x, y), ocean)
                ),
                key=lambda c: (c.x, c.y),
            )
            if not coastal_candidates:
                continue  # no ocean-coastal cell available in this component

            # Try each city in this component as the candidate to relocate,
            # in a deterministic order. For each, find the first coastal
            # candidate that respects min_distance against all OTHER cities.
            cities_in_comp_sorted = sorted(cities_in_comp, key=lambda c: (c.x, c.y))
            relocated = False
            for city_to_move in cities_in_comp_sorted:
                other_cities = [c for c in result if c is not city_to_move]
                for coastal in coastal_candidates:
                    if all(coastal.chebyshev_to(o) >= d for o in other_cities):
                        result.remove(city_to_move)
                        placed_set.discard(city_to_move)
                        result.append(coastal)
                        placed_set.add(coastal)
                        relocated = True
                        break
                if relocated:
                    break
            # If no relocation possible, leave as-is; the safety net catches it.

        return result

    # ---- continent / coastal validation ----------------------------------

    def _all_continents_have_coastal_cities(
        self,
        terrain_grid: list[list[TerrainKind]],
        city_coords: list[Coord],
        profile: MapProfile,
    ) -> bool:
        """Every connected land component containing at least one city must
        have at least one **ocean-coastal** city — a city with a neighbor
        in water that's connected to the map border.

        Inner-sea adjacency doesn't count: a city on an inland lake can't
        host transports that reach the open sea.
        """
        if not city_coords:
            return True

        width, height = profile.width, profile.height
        ocean = _compute_ocean(terrain_grid, profile)

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

            cities_in_comp = [c for c in city_coords if (c.x, c.y) in comp]
            if not any(_is_ocean_coastal(c, ocean) for c in cities_in_comp):
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


# -----------------------------------------------------------------------------
# Ocean topology helpers (module-level so they're easy to test/reuse).
# -----------------------------------------------------------------------------


def _compute_ocean(
    terrain_grid: list[list[TerrainKind]],
    profile: MapProfile,
) -> set[tuple[int, int]]:
    """The set of water cells reachable (8-direction) from the map border.

    Water bodies disconnected from the border are *inner seas* and are
    excluded from this set. The border ring itself is always water (per
    spec §1.1), so the BFS seed is every border cell.
    """
    ocean: set[tuple[int, int]] = set()
    stack: list[tuple[int, int]] = []
    for x in range(profile.width):
        stack.append((x, 0))
        stack.append((x, profile.height - 1))
    for y in range(profile.height):
        stack.append((0, y))
        stack.append((profile.width - 1, y))
    while stack:
        x, y = stack.pop()
        if (x, y) in ocean:
            continue
        if not (0 <= x < profile.width and 0 <= y < profile.height):
            continue
        if terrain_grid[y][x] is not TerrainKind.WATER:
            continue
        ocean.add((x, y))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                stack.append((x + dx, y + dy))
    return ocean


def _compute_ocean_accessible_land(
    terrain_grid: list[list[TerrainKind]],
    ocean: set[tuple[int, int]],
    profile: MapProfile,
) -> set[tuple[int, int]]:
    """Set of land cells in continents that touch the ocean.

    A continent (connected LAND component) is ocean-accessible iff at
    least one of its cells has an ocean neighbor. Inner-island
    continents — landmasses sitting inside inner seas, with no
    border-connected water adjacency — are excluded.
    """
    accessible: set[tuple[int, int]] = set()
    visited: set[tuple[int, int]] = set()
    for sy in range(profile.height):
        for sx in range(profile.width):
            if (sx, sy) in visited or terrain_grid[sy][sx] is not TerrainKind.LAND:
                continue
            comp: set[tuple[int, int]] = set()
            stack: list[tuple[int, int]] = [(sx, sy)]
            has_ocean_neighbor = False
            while stack:
                x, y = stack.pop()
                if (x, y) in comp:
                    continue
                if not (0 <= x < profile.width and 0 <= y < profile.height):
                    continue
                if terrain_grid[y][x] is not TerrainKind.LAND:
                    continue
                comp.add((x, y))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if (nx, ny) in ocean:
                            has_ocean_neighbor = True
                        stack.append((nx, ny))
            visited.update(comp)
            if has_ocean_neighbor:
                accessible.update(comp)
    return accessible


def _is_ocean_coastal(coord: Coord, ocean: set[tuple[int, int]]) -> bool:
    """True if `coord` has at least one ocean neighbor (8-direction)."""
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            if (coord.x + dx, coord.y + dy) in ocean:
                return True
    return False

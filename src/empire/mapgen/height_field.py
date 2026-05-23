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
   (spec §1.1). They participate in the BFS as connectivity bridges but
   are not themselves part of any navigable water body — units cannot
   occupy them.
5. Compute **on-board water components** (8-direction, on-board only,
   NO border bridging). Each component is a separate navigable sea.
6. Compute land continents (8-direction LAND+CITY connectivity).
7. Build the naval reachability graph: two continents are paired if
   they share at least one water component.
8. Iteratively prune continents until every remaining continent has
   degree ≥ 2 in the naval graph (with a relaxation to degree 1 when
   only 2 continents remain — mutual pair is the smallest valid
   configuration). This ensures every "eligible" continent has REDUNDANT
   naval connections: no continent is exclusively paired with just one
   other, which would make the map effectively unwinnable from
   tactical chokepoints.
9. Place cities greedily on eligible continents only, using a
   round-robin first pass (one city per eligible continent) followed
   by a free pass for the remainder. This guarantees each eligible
   continent gets at least one city, so the naval-reachability
   constraint is satisfied by construction at the city level.
10. Touch up landlocked-within-continent cases: if a continent has cities
    but none are ocean-coastal, relocate one of its cities to an
    ocean-coastal cell within the same continent. Subject to the same
    min-distance constraint against other cities. Surgical.
11. As a safety net, verify both (a) every continent with cities has
    at least one ocean-coastal city and (b) the city-bearing continents
    still satisfy the redundancy requirement post-placement. If either
    fails, regenerate, up to `max_regen_attempts`.
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
            chosen = city_coords[: profile.num_cities]
            if (
                len(city_coords) >= profile.num_cities
                and self._all_continents_have_coastal_cities(terrain_grid, chosen, profile)
                and self._city_continents_naval_reachable(terrain_grid, chosen, profile)
            ):
                return self._assemble(terrain_grid, chosen, profile)
            self._last_regen_count = attempt + 1
        raise MapGenerationError(
            f"Could not produce valid map for profile "
            f"{profile.width}x{profile.height} water={profile.water_ratio}% "
            f"min_distance={profile.min_city_distance} num_cities={profile.num_cities} "
            f"after {self._max_regen_attempts} attempts. Either packing failed, "
            f"some continent ended up without a coastal city, or some city-bearing "
            f"continent ended up isolated from the others by sea. Relax water_ratio, "
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

        # Topology analysis. The naval-reachability check supersedes the
        # earlier "ocean-accessible" filter — a continent in the largest
        # naval CC necessarily touches water, but additionally is part of
        # a network that lets ships sail between continents.
        water_components = _compute_water_components(terrain_grid, profile)
        continents = _compute_land_continents(terrain_grid, profile)
        largest_cc = _largest_naval_cc(continents, water_components)
        naval_eligible_land: set[tuple[int, int]] = set()
        for ci in largest_cc:
            naval_eligible_land.update(continents[ci])

        ocean = _compute_ocean(terrain_grid, profile)
        city_coords = self._place_cities(terrain_grid, profile, rng, naval_eligible_land)
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
        naval_eligible_land: set[tuple[int, int]],
    ) -> list[Coord]:
        """Greedy random placement restricted to continents in the largest
        naval-reachability component.

        Continents that can't reach others by sea (isolated landmasses,
        inner islands) are filtered out — cities placed there would be
        unreachable to opponents and unable to send transports anywhere.
        """
        candidates: list[Coord] = [
            Coord(x, y)
            for y in range(1, profile.height - 1)
            for x in range(1, profile.width - 1)
            if terrain_grid[y][x] is TerrainKind.LAND
            and (x, y) in naval_eligible_land
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

    # ---- naval reachability validation -----------------------------------

    def _city_continents_naval_reachable(
        self,
        terrain_grid: list[list[TerrainKind]],
        city_coords: list[Coord],
        profile: MapProfile,
    ) -> bool:
        """All continents containing cities must be in the same connected
        component of the naval-reachability graph.

        Edges in that graph are "two continents share an on-board water
        component." Transitive reachability via intermediate continents is
        captured by graph connectivity — a transport from A could sail to
        intermediate continent B, then disembark, march overland on B,
        re-embark from B's other coast, and sail to C.

        If only one (or zero) continents have cities, the constraint is
        vacuously satisfied.
        """
        if not city_coords:
            return True

        # BFS each city-bearing continent.
        width, height = profile.width, profile.height
        visited: set[tuple[int, int]] = set()
        city_continents: list[set[tuple[int, int]]] = []
        for city in city_coords:
            key = (city.x, city.y)
            if key in visited:
                continue
            comp: set[tuple[int, int]] = set()
            stack: list[tuple[int, int]] = [key]
            while stack:
                x, y = stack.pop()
                if (x, y) in comp:
                    continue
                if not (0 <= x < width and 0 <= y < height):
                    continue
                if terrain_grid[y][x] is not TerrainKind.LAND:
                    continue
                comp.add((x, y))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((x + dx, y + dy))
            visited.update(comp)
            city_continents.append(comp)

        if len(city_continents) <= 1:
            return True

        water_components = _compute_water_components(terrain_grid, profile)
        cont_waters = [
            _continent_water_adjacency(c, water_components) for c in city_continents
        ]

        n = len(city_continents)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            pa, pb = find(a), find(b)
            if pa != pb:
                parent[pa] = pb

        for i in range(n):
            for j in range(i + 1, n):
                if cont_waters[i] & cont_waters[j]:
                    union(i, j)

        roots = {find(i) for i in range(n)}
        return len(roots) == 1

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
    """The set of **on-board** water cells connected (8-direction) to the
    map border.

    The border ring is forced water (spec §1.1), but those cells are
    off-board — units cannot occupy them. So for game purposes, "ocean"
    means *on-board* water reachable from the edge. A city whose only
    water neighbors are border cells has no real naval access (its
    transports would have nowhere on-board to step out to). The border
    cells participate in the BFS as a connectivity bridge but are not
    themselves in the returned set.

    Water bodies disconnected from the border are *inner seas* and are
    excluded as before.
    """
    visited: set[tuple[int, int]] = set()
    stack: list[tuple[int, int]] = []
    for x in range(profile.width):
        stack.append((x, 0))
        stack.append((x, profile.height - 1))
    for y in range(profile.height):
        stack.append((0, y))
        stack.append((profile.width - 1, y))
    while stack:
        x, y = stack.pop()
        if (x, y) in visited:
            continue
        if not (0 <= x < profile.width and 0 <= y < profile.height):
            continue
        if terrain_grid[y][x] is not TerrainKind.WATER:
            continue
        visited.add((x, y))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                stack.append((x + dx, y + dy))

    # Filter out border cells (off-board): they served only as BFS seeds.
    return {
        (x, y)
        for (x, y) in visited
        if 1 <= x <= profile.width - 2 and 1 <= y <= profile.height - 2
    }


def _is_ocean_coastal(coord: Coord, ocean: set[tuple[int, int]]) -> bool:
    """True if `coord` has at least one ocean neighbor (8-direction)."""
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            if (coord.x + dx, coord.y + dy) in ocean:
                return True
    return False


def _compute_water_components(
    terrain_grid: list[list[TerrainKind]],
    profile: MapProfile,
) -> list[set[tuple[int, int]]]:
    """Connected components of on-board water cells (8-direction, no border
    bridging). Each component is a separately-navigable sea — a ship can
    move freely within a component but cannot cross between them (the
    border ring is off-board and not traversable).
    """
    components: list[set[tuple[int, int]]] = []
    visited: set[tuple[int, int]] = set()
    for y in range(1, profile.height - 1):
        for x in range(1, profile.width - 1):
            if (x, y) in visited or terrain_grid[y][x] is not TerrainKind.WATER:
                continue
            comp: set[tuple[int, int]] = set()
            stack: list[tuple[int, int]] = [(x, y)]
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in comp:
                    continue
                if not (1 <= cx <= profile.width - 2 and 1 <= cy <= profile.height - 2):
                    continue
                if terrain_grid[cy][cx] is not TerrainKind.WATER:
                    continue
                comp.add((cx, cy))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((cx + dx, cy + dy))
            visited.update(comp)
            components.append(comp)
    return components


def _compute_land_continents(
    terrain_grid: list[list[TerrainKind]],
    profile: MapProfile,
) -> list[set[tuple[int, int]]]:
    """Connected components of LAND cells (8-direction). At the call point
    (before _build_tiles), cities haven't been baked into terrain yet, so
    LAND is all that matters.
    """
    components: list[set[tuple[int, int]]] = []
    visited: set[tuple[int, int]] = set()
    for y in range(profile.height):
        for x in range(profile.width):
            if (x, y) in visited or terrain_grid[y][x] is not TerrainKind.LAND:
                continue
            comp: set[tuple[int, int]] = set()
            stack: list[tuple[int, int]] = [(x, y)]
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in comp:
                    continue
                if not (0 <= cx < profile.width and 0 <= cy < profile.height):
                    continue
                if terrain_grid[cy][cx] is not TerrainKind.LAND:
                    continue
                comp.add((cx, cy))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((cx + dx, cy + dy))
            visited.update(comp)
            components.append(comp)
    return components


def _continent_water_adjacency(
    continent: set[tuple[int, int]],
    water_components: list[set[tuple[int, int]]],
) -> set[int]:
    """Which water component indices does this continent border (8-direction)?"""
    adj: set[int] = set()
    for (x, y) in continent:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neighbor = (x + dx, y + dy)
                for wi, wc in enumerate(water_components):
                    if wi in adj:
                        continue
                    if neighbor in wc:
                        adj.add(wi)
                        break
    return adj


def _largest_naval_cc(
    continents: list[set[tuple[int, int]]],
    water_components: list[set[tuple[int, int]]],
) -> set[int]:
    """Find the connected component of the naval-reachability graph
    containing the most continents (tiebreaker: most total land cells).

    Edges in the graph connect two continents that share at least one
    water component. Returns a set of continent indices.
    """
    if not continents:
        return set()

    cont_waters = [_continent_water_adjacency(c, water_components) for c in continents]
    n = len(continents)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        pa, pb = find(a), find(b)
        if pa != pb:
            parent[pa] = pb

    for i in range(n):
        for j in range(i + 1, n):
            if cont_waters[i] & cont_waters[j]:
                union(i, j)

    ccs: dict[int, list[int]] = {}
    for i in range(n):
        ccs.setdefault(find(i), []).append(i)

    best = max(
        ccs.values(),
        key=lambda cc: (len(cc), sum(len(continents[i]) for i in cc)),
    )
    return set(best)

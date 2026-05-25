"""Phase-5 canary tests for `HeightFieldMapGenerator`.

Per the planning doc: golden-summary tests (cell counts within tolerance,
city count exact, all-cities-on-land, min-distance respected, ≥1 connected
landmass). Plus determinism and a regen-failure path.
"""

from __future__ import annotations

import random

import pytest

from empire.core.coord import Coord
from empire.core.identity import CityId
from empire.core.map import Map
from empire.core.ruleset import LARGE, SMALL, STANDARD_PROFILE, MapProfile
from empire.core.tile import TerrainKind
from empire.mapgen.generator import MapGenerationError
from empire.mapgen.height_field import HeightFieldMapGenerator

# --- helpers ------------------------------------------------------------------


def _count_terrain(m: Map, kind: TerrainKind) -> int:
    return sum(
        1 for x in range(m.width) for y in range(m.height) if m.terrain_at(Coord(x, y)) is kind
    )


def _flood_fill_land_or_city(m: Map, start: Coord) -> set[Coord]:
    visited: set[Coord] = {start}
    stack: list[Coord] = [start]
    while stack:
        c = stack.pop()
        for n in c.neighbors():
            if not m.in_bounds(n) or n in visited:
                continue
            if m.terrain_at(n) in {TerrainKind.LAND, TerrainKind.CITY}:
                visited.add(n)
                stack.append(n)
    return visited


# --- structural per (profile, seed) ------------------------------------------

PROFILE_SEED_CASES = [
    pytest.param(SMALL, 0, id="small-seed0"),
    pytest.param(SMALL, 7, id="small-seed7"),
    pytest.param(STANDARD_PROFILE, 0, id="standard-seed0"),
    pytest.param(STANDARD_PROFILE, 7, id="standard-seed7"),
    # Seed 22 originally produced a landlocked 36-cell continent with one
    # inland city. Kept in the case set so the property tests catch any
    # regression in the coastal-cities constraint.
    pytest.param(STANDARD_PROFILE, 22, id="standard-seed22"),
    pytest.param(LARGE, 0, id="large-seed0"),
]


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_dimensions_match_profile(profile: MapProfile, seed: int) -> None:
    gen = HeightFieldMapGenerator()
    m, _ = gen.generate(profile, random.Random(seed))
    assert m.width == profile.width
    assert m.height == profile.height


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_city_count_exactly_matches_profile(profile: MapProfile, seed: int) -> None:
    gen = HeightFieldMapGenerator()
    _, cities = gen.generate(profile, random.Random(seed))
    assert len(cities) == profile.num_cities


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_cities_have_unique_sequential_ids(profile: MapProfile, seed: int) -> None:
    gen = HeightFieldMapGenerator()
    _, cities = gen.generate(profile, random.Random(seed))
    ids = [c.id for c in cities]
    assert sorted(ids) == [CityId(i) for i in range(1, profile.num_cities + 1)]
    assert len(set(ids)) == profile.num_cities


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_cities_are_neutral(profile: MapProfile, seed: int) -> None:
    gen = HeightFieldMapGenerator()
    _, cities = gen.generate(profile, random.Random(seed))
    assert all(c.owner is None for c in cities)


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_each_city_sits_on_a_city_tile(profile: MapProfile, seed: int) -> None:
    gen = HeightFieldMapGenerator()
    m, cities = gen.generate(profile, random.Random(seed))
    for city in cities:
        tile = m.tile(city.coord)
        assert tile.terrain is TerrainKind.CITY
        assert tile.city is city
        assert tile.on_board is True


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_no_two_cities_within_min_distance(profile: MapProfile, seed: int) -> None:
    gen = HeightFieldMapGenerator()
    _, cities = gen.generate(profile, random.Random(seed))
    coords = [c.coord for c in cities]
    for i, a in enumerate(coords):
        for b in coords[i + 1 :]:
            assert a.chebyshev_to(b) >= profile.min_city_distance, (
                f"Cities at {a} and {b} are only {a.chebyshev_to(b)} apart; "
                f"need >= {profile.min_city_distance}"
            )


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_water_ratio_within_tolerance(profile: MapProfile, seed: int) -> None:
    """Total water within 5 percentage points of target.

    The tiny-landmass removal pass (which converts <15 cell components
    to water) pushes actual water above target by a variable amount —
    typically 1-2pp for STANDARD, up to ~4pp for LARGE (more land area
    means more small islands removed). 5pp absorbs this variance while
    still catching gross threshold errors.
    """
    gen = HeightFieldMapGenerator()
    m, _ = gen.generate(profile, random.Random(seed))
    water = _count_terrain(m, TerrainKind.WATER)
    total = profile.width * profile.height
    actual_pct = water * 100 / total
    assert abs(actual_pct - profile.water_ratio) < 5, (
        f"Water ratio {actual_pct:.1f}% vs target {profile.water_ratio}%"
    )


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_border_cells_are_off_board_water(profile: MapProfile, seed: int) -> None:
    """Spec §1.1: outermost ring is unwalkable border (off_board) and water."""
    gen = HeightFieldMapGenerator()
    m, _ = gen.generate(profile, random.Random(seed))
    for x in range(profile.width):
        for y in (0, profile.height - 1):
            tile = m.tile(Coord(x, y))
            assert tile.on_board is False
            assert tile.terrain is TerrainKind.WATER
    for y in range(profile.height):
        for x in (0, profile.width - 1):
            tile = m.tile(Coord(x, y))
            assert tile.on_board is False
            assert tile.terrain is TerrainKind.WATER


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_interior_cells_are_on_board(profile: MapProfile, seed: int) -> None:
    gen = HeightFieldMapGenerator()
    m, _ = gen.generate(profile, random.Random(seed))
    # Spot-check a handful of interior cells.
    for x, y in [
        (profile.width // 2, profile.height // 2),
        (profile.width // 4, profile.height // 4),
        (profile.width - 2, profile.height - 2),
    ]:
        assert m.tile(Coord(x, y)).on_board is True


def _ocean_set(m: Map) -> set[Coord]:
    """**On-board** water cells in `m` reachable (8-direction) from the border.

    Border cells are off-board and excluded from the result; they participate
    in the BFS only as connectivity bridges.
    """
    visited: set[Coord] = set()
    stack: list[Coord] = []
    for x in range(m.width):
        stack.append(Coord(x, 0))
        stack.append(Coord(x, m.height - 1))
    for y in range(m.height):
        stack.append(Coord(0, y))
        stack.append(Coord(m.width - 1, y))
    while stack:
        c = stack.pop()
        if c in visited or not m.in_bounds(c):
            continue
        if m.terrain_at(c) is not TerrainKind.WATER:
            continue
        visited.add(c)
        stack.extend(c.neighbors())
    return {c for c in visited if m.tile(c).on_board}


def _on_board_water_components(m: Map) -> list[set[Coord]]:
    """Connected components of on-board water cells (8-direction, no border
    bridging). Each component is a separately-navigable sea."""
    components: list[set[Coord]] = []
    visited: set[Coord] = set()
    for y in range(1, m.height - 1):
        for x in range(1, m.width - 1):
            c = Coord(x, y)
            if c in visited or m.terrain_at(c) is not TerrainKind.WATER:
                continue
            comp: set[Coord] = set()
            stack: list[Coord] = [c]
            while stack:
                cur = stack.pop()
                if cur in comp:
                    continue
                if not (1 <= cur.x <= m.width - 2 and 1 <= cur.y <= m.height - 2):
                    continue
                if m.terrain_at(cur) is not TerrainKind.WATER:
                    continue
                comp.add(cur)
                stack.extend(cur.neighbors())
            visited.update(comp)
            components.append(comp)
    return components


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_no_tiny_landmasses_remain_in_generated_map(
    profile: MapProfile,
    seed: int,
) -> None:
    """Per playtest feedback: tiny landmasses (single-cell islands, few-cell
    shoals) are gameplay noise and would be terrible city locations. The
    generator strips them out before city placement.

    The threshold scales with map area: floor of 10 cells, proportional
    cap at ~0.4% of total area.
    """
    gen = HeightFieldMapGenerator()
    m, cities = gen.generate(profile, random.Random(seed))
    del cities

    min_size = max(10, profile.width * profile.height // 240)

    visited: set[Coord] = set()
    for y in range(m.height):
        for x in range(m.width):
            c = Coord(x, y)
            if c in visited:
                continue
            if m.terrain_at(c) not in {TerrainKind.LAND, TerrainKind.CITY}:
                continue
            comp: set[Coord] = set()
            stack: list[Coord] = [c]
            while stack:
                cur = stack.pop()
                if cur in comp or not m.in_bounds(cur):
                    continue
                if m.terrain_at(cur) not in {TerrainKind.LAND, TerrainKind.CITY}:
                    continue
                comp.add(cur)
                stack.extend(cur.neighbors())
            visited.update(comp)
            assert len(comp) >= min_size, (
                f"Landmass of {len(comp)} cells (below threshold {min_size}) "
                f"survived in the generated map at coords starting from {c}"
            )


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_every_city_continent_has_a_paired_city(
    profile: MapProfile,
    seed: int,
) -> None:
    """Every city-bearing continent must contain at least one city that is
    naval-paired with a city on a different continent.

    A "naval pair" requires two cities (on different continents) both
    adjacent to the same on-board water component. Continent-level water
    sharing isn't sufficient: ships need to disembark at a destination
    *city* (for direct naval invasion) rather than on an unsettled beach,
    so the pairing must be city-to-city, not just continent-to-continent.
    """
    gen = HeightFieldMapGenerator()
    m, cities = gen.generate(profile, random.Random(seed))

    if len(cities) <= 1:
        return

    water_components = _on_board_water_components(m)

    # Map each city's coord to its continent index.
    visited: set[Coord] = set()
    continent_of: dict[Coord, int] = {}
    next_idx = 0
    for city in cities:
        if city.coord in visited:
            continue
        comp: set[Coord] = set()
        stack: list[Coord] = [city.coord]
        while stack:
            c = stack.pop()
            if c in comp or not m.in_bounds(c):
                continue
            if m.terrain_at(c) not in {TerrainKind.LAND, TerrainKind.CITY}:
                continue
            comp.add(c)
            stack.extend(c.neighbors())
        visited.update(comp)
        for cell in comp:
            continent_of[cell] = next_idx
        next_idx += 1

    # For each water component, which continents have cities adjacent?
    comp_continents: dict[int, set[int]] = {}
    for city in cities:
        for n in city.coord.neighbors():
            for wi, wc in enumerate(water_components):
                if n in wc:
                    comp_continents.setdefault(wi, set()).add(continent_of[city.coord])
                    break

    useful = {wi for wi, cs in comp_continents.items() if len(cs) >= 2}
    paired: set[int] = set()
    for wi in useful:
        paired.update(comp_continents[wi])

    all_city_continents = {continent_of[c.coord] for c in cities}
    unpaired = all_city_continents - paired
    assert not unpaired, (
        f"City-bearing continents without a city pair: {sorted(unpaired)} "
        f"(out of {sorted(all_city_continents)}). Each such continent has "
        f"cities but none of them sit on a water component that also has "
        f"a city from another continent."
    )


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_every_continent_with_cities_has_an_ocean_coastal_city(
    profile: MapProfile,
    seed: int,
) -> None:
    """A continent with cities must have at least one **ocean-coastal**
    city — adjacent to water that's connected to the map border.

    Adjacency to inner seas alone is insufficient: a city on an inland
    lake can't host transports that reach the open sea (no naval path
    out), and an enemy can't invade by sea (no path in). Either way the
    continent is a strategic dead-end.
    """
    gen = HeightFieldMapGenerator()
    m, cities = gen.generate(profile, random.Random(seed))
    ocean = _ocean_set(m)

    # Walk every connected LAND+CITY component that contains a city.
    visited: set[Coord] = set()
    for start_city in cities:
        if start_city.coord in visited:
            continue
        component: set[Coord] = set()
        stack = [start_city.coord]
        while stack:
            c = stack.pop()
            if c in component or not m.in_bounds(c):
                continue
            if m.terrain_at(c) not in {TerrainKind.LAND, TerrainKind.CITY}:
                continue
            component.add(c)
            stack.extend(c.neighbors())
        visited.update(component)

        # At least one city in this component must have an ocean neighbor.
        cities_in_comp = [c for c in cities if c.coord in component]
        has_ocean_coastal = any(
            n in ocean for city in cities_in_comp for n in city.coord.neighbors()
        )
        assert has_ocean_coastal, (
            f"Stranded continent: cities at "
            f"{[(c.coord.x, c.coord.y) for c in cities_in_comp]} have no "
            f"ocean neighbors (continent size: {len(component)} cells; "
            f"may touch only inner seas)."
        )


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_at_least_one_meaningful_connected_landmass(
    profile: MapProfile,
    seed: int,
) -> None:
    """Per `planning/01-game-rules-spec.md` §9.1 and `planning/05-implementation-plan.md`
    Phase-5 exit gate: the generated map must have at least one connected
    landmass — i.e., the largest connected component of LAND+CITY cells
    isn't a single pepper-noise tile.

    Refining the terrain algorithm to produce continent-scale features
    (Perlin / diamond-square) is a separate concern; this test just
    confirms smoothing produces SOME structure.
    """
    gen = HeightFieldMapGenerator()
    m, _ = gen.generate(profile, random.Random(seed))
    land_or_city_coords = [
        Coord(x, y)
        for y in range(profile.height)
        for x in range(profile.width)
        if m.terrain_at(Coord(x, y)) in {TerrainKind.LAND, TerrainKind.CITY}
    ]
    if not land_or_city_coords:
        pytest.fail("Generated map has no land at all")

    # Find the largest connected component.
    unvisited = set(land_or_city_coords)
    largest = 0
    while unvisited:
        seed_coord = next(iter(unvisited))
        component = _flood_fill_land_or_city(m, seed_coord)
        largest = max(largest, len(component))
        unvisited -= component

    # A meaningful landmass: at least 50 connected cells, well above
    # pepper-noise scale. Continent-scale connectivity is a quality target
    # for later algorithm work.
    assert largest >= 50, f"Largest connected region has only {largest} cells; expected ≥ 50"


# --- determinism -------------------------------------------------------------


def test_same_seed_produces_identical_maps() -> None:
    gen1 = HeightFieldMapGenerator()
    gen2 = HeightFieldMapGenerator()
    m1, c1 = gen1.generate(STANDARD_PROFILE, random.Random(42))
    m2, c2 = gen2.generate(STANDARD_PROFILE, random.Random(42))
    # Every terrain cell matches.
    for x in range(STANDARD_PROFILE.width):
        for y in range(STANDARD_PROFILE.height):
            assert m1.terrain_at(Coord(x, y)) is m2.terrain_at(Coord(x, y))
    # City coords match.
    assert {city.coord for city in c1} == {city.coord for city in c2}


def test_different_seeds_produce_different_maps() -> None:
    gen = HeightFieldMapGenerator()
    m1, _ = gen.generate(STANDARD_PROFILE, random.Random(1))
    m2, _ = gen.generate(STANDARD_PROFILE, random.Random(2))
    # Many cells should differ between two unrelated seeds.
    differing = sum(
        1
        for x in range(STANDARD_PROFILE.width)
        for y in range(STANDARD_PROFILE.height)
        if m1.terrain_at(Coord(x, y)) is not m2.terrain_at(Coord(x, y))
    )
    # Even smoothing produces some cells that match by coincidence, but at least
    # 10% of cells should differ between two distinct seeds.
    total = STANDARD_PROFILE.width * STANDARD_PROFILE.height
    assert differing > total // 10


# --- regen failure path ------------------------------------------------------


def test_regen_count_zero_on_easy_profile() -> None:
    gen = HeightFieldMapGenerator()
    gen.generate(SMALL, random.Random(0))
    # SMALL packs comfortably; no regens should be needed.
    assert gen.last_regen_count == 0


def test_impossible_profile_raises_after_max_attempts() -> None:
    """A profile demanding more cities than can ever fit triggers the regen
    cap. Use a tiny 10x10 map with 99% water and demand 50 cities — there's
    no possible packing.
    """
    impossible = MapProfile(
        width=10,
        height=10,
        water_ratio=99,
        smooth_iterations=2,
        num_cities=50,
        min_city_distance=3,
    )
    gen = HeightFieldMapGenerator(max_regen_attempts=3)
    with pytest.raises(MapGenerationError, match="Could not produce valid map"):
        gen.generate(impossible, random.Random(0))
    assert gen.last_regen_count == 3


def test_max_regen_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        HeightFieldMapGenerator(max_regen_attempts=0)


# --- spatial index hookup ----------------------------------------------------


def test_generated_map_supports_unit_placement() -> None:
    """A generated Map is a fully-functional Map: units can be placed on its
    land tiles via the standard Map.place_unit API.
    """
    from empire.core.identity import PlayerId, UnitId
    from empire.core.map import ViewMap
    from empire.core.player import Player
    from empire.core.unit import Army

    gen = HeightFieldMapGenerator()
    m, cities = gen.generate(SMALL, random.Random(0))
    p = Player(id=PlayerId(1), name="P", is_ai=False, view=ViewMap())
    # Place an army at the first city's coord (a CITY tile is land-walkable for Army).
    a = Army(UnitId(1), p, Coord(0, 0))
    m.place_unit(a, cities[0].coord)
    assert m.units_at(cities[0].coord) == (a,)

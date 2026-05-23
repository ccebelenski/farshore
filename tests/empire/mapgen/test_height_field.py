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
        1 for x in range(m.width) for y in range(m.height)
        if m.terrain_at(Coord(x, y)) is kind
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
        for b in coords[i + 1:]:
            assert a.chebyshev_to(b) >= profile.min_city_distance, (
                f"Cities at {a} and {b} are only {a.chebyshev_to(b)} apart; "
                f"need >= {profile.min_city_distance}"
            )


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_water_ratio_within_tolerance(profile: MapProfile, seed: int) -> None:
    """Total water (border + threshold-derived) within 3 percentage points of target."""
    gen = HeightFieldMapGenerator()
    m, _ = gen.generate(profile, random.Random(seed))
    water = _count_terrain(m, TerrainKind.WATER)
    total = profile.width * profile.height
    actual_pct = water * 100 / total
    assert abs(actual_pct - profile.water_ratio) < 3, (
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


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_every_continent_with_cities_has_a_coastal_city(
    profile: MapProfile, seed: int,
) -> None:
    """A landlocked continent's cities can't host transports (spec §3.2)
    and can't be invaded by sea — anyone starting there is stranded and
    anyone trying to attack can't reach. The generator must regenerate
    until every land component with cities has at least one coastal city.
    """
    gen = HeightFieldMapGenerator()
    m, cities = gen.generate(profile, random.Random(seed))

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

        # Cities in this component.
        cities_in_comp = [c for c in cities if c.coord in component]
        # At least one must have a water neighbor.
        has_coastal = any(
            m.in_bounds(n) and m.terrain_at(n) is TerrainKind.WATER
            for city in cities_in_comp
            for n in city.coord.neighbors()
        )
        assert has_coastal, (
            f"Landlocked continent: cities at "
            f"{[(c.coord.x, c.coord.y) for c in cities_in_comp]} have no "
            f"water neighbors. (Continent size: {len(component)} cells.)"
        )


@pytest.mark.parametrize(("profile", "seed"), PROFILE_SEED_CASES)
def test_at_least_one_meaningful_connected_landmass(
    profile: MapProfile, seed: int,
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
        for y in range(profile.height) for x in range(profile.width)
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
    assert largest >= 50, (
        f"Largest connected region has only {largest} cells; expected ≥ 50"
    )


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
        1 for x in range(STANDARD_PROFILE.width) for y in range(STANDARD_PROFILE.height)
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
        width=10, height=10,
        water_ratio=99, smooth_iterations=2,
        num_cities=50, min_city_distance=3,
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

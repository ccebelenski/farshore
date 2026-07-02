"""Phase 10.10 — capital-selection eligibility (spec §9.2).

`build_game` must place capitals on distinct continents that each have at
least three cities and an ocean-coastal city, rejecting+regenerating maps
that don't qualify.
"""

from __future__ import annotations

import pytest

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId
from empire.core.map import Map
from empire.core.ruleset import SMALL
from empire.core.tile import TerrainKind, Tile
from empire.setup import build_game, capital_eligible_continents
from tests.empire.support import build_map


def _build_map(rows: list[str], city_coords: list[Coord]) -> tuple[Map, list[City]]:
    cities = [City(id=CityId(i + 1), coord=c, owner=None) for i, c in enumerate(city_coords)]
    return build_map(rows, {c.coord: c for c in cities}), cities


# --- eligibility predicate ---------------------------------------------------


def test_continent_with_three_coastal_cities_is_eligible() -> None:
    # One island of land along the top, ocean below; three cities, all coastal.
    m, cities = _build_map(
        ["CLC", "LCL", "WWW"],
        [Coord(0, 0), Coord(2, 0), Coord(1, 1)],
    )
    eligible = capital_eligible_continents(m, cities)
    assert len(eligible) == 1


def test_continent_with_two_cities_is_rejected() -> None:
    # Only two cities on the landmass — below the 3-city minimum.
    m, cities = _build_map(
        ["CLC", "LLL", "WWW"],
        [Coord(0, 0), Coord(2, 0)],
    )
    assert capital_eligible_continents(m, cities) == []


def test_landlocked_continent_is_rejected() -> None:
    # Three cities but no water anywhere — can't host transports.
    m, cities = _build_map(
        ["CLC", "LCL", "LLL"],
        [Coord(0, 0), Coord(2, 0), Coord(1, 1)],
    )
    assert capital_eligible_continents(m, cities) == []


def test_inland_lake_does_not_make_a_city_coastal() -> None:
    # The three cities ring a 1-cell inland lake at (2, 2); the open ocean is
    # the larger water body in columns 5-6, reachable from no city. Touching
    # a lake (not the ocean) must not count as coastal.
    m, cities = _build_map(
        ["LLLLLWW",
         "LCLCLWW",
         "LLWLLWW",
         "LCLLLWW",
         "LLLLLWW"],
        [Coord(1, 1), Coord(3, 1), Coord(1, 3)],
    )
    assert capital_eligible_continents(m, cities) == []


# --- build_game end to end ---------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 7])
def test_build_game_places_capitals_on_distinct_eligible_continents(seed: int) -> None:
    game, players = build_game(SMALL, seed)
    capitals = [c for c in game.map.cities() if c.owner is not None]
    assert len(capitals) == len(players)

    eligible = capital_eligible_continents(game.map, list(game.map.cities()))
    cont_of = {(x, y): i for i, comp in enumerate(eligible) for (x, y) in comp}
    # Every capital sits on an eligible continent...
    cap_continents = [cont_of.get((c.coord.x, c.coord.y)) for c in capitals]
    assert all(idx is not None for idx in cap_continents)
    # ...and on distinct ones.
    assert len(set(cap_continents)) == len(capitals)


def test_build_game_is_deterministic_for_a_seed() -> None:
    g1, _ = build_game(SMALL, 5)
    g2, _ = build_game(SMALL, 5)
    caps1 = sorted((c.coord.x, c.coord.y) for c in g1.map.cities() if c.owner)
    caps2 = sorted((c.coord.x, c.coord.y) for c in g2.map.cities() if c.owner)
    assert caps1 == caps2

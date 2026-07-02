"""Game-construction helper used by validation, TUI, and any other driver.

Pulls a profile from `ruleset`, generates a map, assigns starting capitals on
distinct *capital-eligible* continents, and returns a fully-built `Game` along
with the player list. Controllers are NOT attached here — the caller wires
them based on its own context (BaselineAI vs BaselineAI for validation;
HumanController vs BaselineAI for the TUI).

Capital eligibility (spec §9.2): a continent may host a starting capital iff
it has at least `MIN_CAPITAL_CITIES` cities (the capital plus expansion
targets) and at least one ocean-coastal city (adjacent to the open ocean, so
the player can build and host transports). If fewer than `num_players`
continents qualify, the map is rejected and a fresh one is generated
(deterministically, bounded by `MAX_MAP_ATTEMPTS`).

This module sits above the layered packages (it imports from `core`,
`mapgen`, `combat`); exposed as `empire.setup` for callers that need it.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from empire.combat.resolver import CombatResolver
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD, MapProfile, RuleSet
from empire.core.tile import LAND_TERRAINS, TerrainKind
from empire.core.unit import UnitKind
from empire.mapgen.height_field import HeightFieldMapGenerator

# Spec §9.2: a capital-eligible continent needs the capital plus at least two
# more cities to fuel early expansion.
MIN_CAPITAL_CITIES = 3
# Bound on regeneration attempts before we give up on a profile.
MAX_MAP_ATTEMPTS = 50


def build_game(
    profile: MapProfile,
    seed: int,
    *,
    p1_name: str = "P1",
    p2_name: str = "P2",
    p1_is_ai: bool = False,
    p2_is_ai: bool = True,
    rules: RuleSet = STANDARD,
) -> tuple[Game, list[Player]]:
    """Generate a map, assign capitals on eligible continents, return the game.

    Regenerates the map (deterministically, from a `seed`-derived stream)
    until at least `num_players` continents are capital-eligible, then places
    one capital per player on a distinct eligible continent (largest first).
    Starting cities default to building ARMY.

    Raises `RuntimeError` if no qualifying map turns up within
    `MAX_MAP_ATTEMPTS` (typically a profile too small/dense to host the
    players on separate landmasses).
    """
    players = [
        Player(id=PlayerId(1), name=p1_name, is_ai=p1_is_ai, view=ViewMap(), color="red"),
        Player(id=PlayerId(2), name=p2_name, is_ai=p2_is_ai, view=ViewMap(), color="blue"),
    ]

    gen = HeightFieldMapGenerator()
    master = random.Random(seed)
    for _ in range(MAX_MAP_ATTEMPTS):
        map_seed = master.randrange(2**31)
        real_map, cities = gen.generate(profile, random.Random(map_seed))
        eligible = capital_eligible_continents(real_map, cities)
        if len(eligible) >= len(players):
            _assign_capitals(eligible, cities, players)
            game = Game(
                rules=rules,
                real_map=real_map,
                players=players,
                seed=seed,
                combat_resolver=CombatResolver(),
            )
            return game, players

    raise RuntimeError(
        f"could not generate a map with >= {len(players)} capital-eligible "
        f"continents after {MAX_MAP_ATTEMPTS} attempts (profile "
        f"{profile.width}x{profile.height}, {profile.num_cities} cities — "
        f"likely too small or too dense)"
    )


def capital_eligible_continents(
    real_map: Map, cities: list[City]
) -> list[set[tuple[int, int]]]:
    """Continents that may host a starting capital (spec §9.2), largest first.

    A continent qualifies iff it holds at least `MIN_CAPITAL_CITIES` cities
    and at least one of them is ocean-coastal (adjacent to the open ocean).
    """
    ocean = _open_ocean(real_map)
    eligible: list[set[tuple[int, int]]] = []
    for comp in land_continents(real_map):
        on_continent = [c for c in cities if (c.coord.x, c.coord.y) in comp]
        if len(on_continent) < MIN_CAPITAL_CITIES:
            continue
        if any(_is_ocean_coastal(c, ocean, real_map) for c in on_continent):
            eligible.append(comp)
    eligible.sort(key=len, reverse=True)
    return eligible


def _assign_capitals(
    eligible: list[set[tuple[int, int]]],
    cities: list[City],
    players: list[Player],
) -> None:
    """Place one capital per player on a distinct eligible continent.

    The largest eligible continent goes to the first player, and so on. The
    capital is the first (generation-order) city on that continent; it begins
    building ARMY.
    """
    for player, comp in zip(players, eligible, strict=False):
        capital = next(c for c in cities if (c.coord.x, c.coord.y) in comp)
        capital.owner = player
        capital.production.building = UnitKind.ARMY
        capital.production.work = 0


def land_continents(real_map: Map) -> list[set[tuple[int, int]]]:
    """All connected land/city components (8-connectivity). Public for
    map-analysis helpers and validation harnesses."""
    return _components(real_map, lambda t: t in LAND_TERRAINS)



def _open_ocean(real_map: Map) -> set[tuple[int, int]]:
    """The largest connected body of water — the navigable open ocean.

    Treating the biggest water component as "the ocean" excludes small inland
    lakes, which can't host transports bound for other landmasses.
    """
    water_comps = _components(real_map, lambda t: t is TerrainKind.WATER)
    empty: set[tuple[int, int]] = set()
    return max(water_comps, key=len, default=empty)


def _components(
    real_map: Map, matches: Callable[[TerrainKind], bool]
) -> list[set[tuple[int, int]]]:
    """Flood-fill `real_map` into 8-connected components of matching terrain."""
    visited: set[tuple[int, int]] = set()
    comps: list[set[tuple[int, int]]] = []
    for y in range(real_map.height):
        for x in range(real_map.width):
            if (x, y) in visited or not matches(real_map.terrain_at(Coord(x, y))):
                continue
            comp: set[tuple[int, int]] = set()
            stack = [(x, y)]
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in comp or not real_map.in_bounds(Coord(cx, cy)):
                    continue
                if not matches(real_map.terrain_at(Coord(cx, cy))):
                    continue
                comp.add((cx, cy))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx or dy:
                            stack.append((cx + dx, cy + dy))
            visited |= comp
            comps.append(comp)
    return comps


def _is_ocean_coastal(city: City, ocean: set[tuple[int, int]], real_map: Map) -> bool:
    """True if `city` neighbors the open ocean (spec §9.2)."""
    return any(
        real_map.in_bounds(n) and (n.x, n.y) in ocean for n in city.coord.neighbors()
    )

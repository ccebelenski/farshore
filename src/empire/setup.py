"""Game-construction helper used by validation, TUI, and any other driver.

Pulls a profile from `ruleset`, generates a map, assigns starting capitals on
the largest available continents, and returns a fully-built `Game` along
with the player list. Controllers are NOT attached here — the caller wires
them based on its own context (BaselineAI vs BaselineAI for validation;
HumanController vs BaselineAI for the TUI).

This module sits above the layered packages (it imports from `core`,
`mapgen`, `combat`); kept private (`empire._setup`-ish naming intent) only
in spirit — exposed as `empire.setup` for callers that need it.
"""

from __future__ import annotations

import random

from empire.combat.resolver import CombatResolver
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.map import ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD, MapProfile
from empire.core.tile import TerrainKind
from empire.core.unit import UnitKind
from empire.mapgen.height_field import HeightFieldMapGenerator


def build_game(
    profile: MapProfile,
    seed: int,
    *,
    p1_name: str = "P1",
    p2_name: str = "P2",
    p1_is_ai: bool = False,
    p2_is_ai: bool = True,
) -> tuple[Game, list[Player]]:
    """Generate a map, assign capitals, return a fresh `Game` + player list.

    Capitals are placed on the two largest land continents (one per player),
    a stand-in for the full capital-selection logic from planning/01 §9.2.
    Starting cities default to building ARMY.

    Controllers are not attached here. The returned `Game` already has a
    `CombatResolver` wired and is ready for the caller to attach controllers
    and call `run_turn()`.
    """
    gen = HeightFieldMapGenerator()
    real_map, cities = gen.generate(profile, random.Random(seed))

    players = [
        Player(id=PlayerId(1), name=p1_name, is_ai=p1_is_ai, view=ViewMap(), color="red"),
        Player(id=PlayerId(2), name=p2_name, is_ai=p2_is_ai, view=ViewMap(), color="blue"),
    ]

    visited: set[tuple[int, int]] = set()
    continents: list[tuple[int, set[tuple[int, int]]]] = []
    for y in range(real_map.height):
        for x in range(real_map.width):
            if (x, y) in visited:
                continue
            tile = real_map.tile(Coord(x, y))
            if tile.terrain not in {TerrainKind.LAND, TerrainKind.CITY}:
                continue
            comp: set[tuple[int, int]] = set()
            stack: list[tuple[int, int]] = [(x, y)]
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in comp or not real_map.in_bounds(Coord(cx, cy)):
                    continue
                t = real_map.terrain_at(Coord(cx, cy))
                if t not in {TerrainKind.LAND, TerrainKind.CITY}:
                    continue
                comp.add((cx, cy))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((cx + dx, cy + dy))
            visited.update(comp)
            continents.append((len(comp), comp))
    continents.sort(reverse=True)

    assigned = [False] * len(players)
    for _, comp in continents:
        cities_on = [c for c in cities if (c.coord.x, c.coord.y) in comp]
        if not cities_on:
            continue
        for i, player in enumerate(players):
            if assigned[i]:
                continue
            already_taken = any(
                (other.coord.x, other.coord.y) in comp
                for other in cities
                if other.owner is not None and other.owner is not player
            )
            if already_taken:
                continue
            cities_on[0].owner = player
            cities_on[0].production.building = UnitKind.ARMY
            cities_on[0].production.work = 0
            assigned[i] = True
            break
        if all(assigned):
            break

    game = Game(
        rules=STANDARD,
        real_map=real_map,
        players=players,
        seed=seed,
        combat_resolver=CombatResolver(),
    )
    return game, players

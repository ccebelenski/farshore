"""Fog-of-war derived queries shared by AI personalities.

These operate purely on a `WorldView` (no controller state), so any AI layer
— `BaselineTactical`'s scoring, `SearchAI`'s `PlanFollower` — can share one
definition of "what the player can infer from fog".
"""

from __future__ import annotations

from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.tile import TerrainKind


def terrain_for_view(view: WorldView, c: Coord) -> TerrainKind | None:
    """Terrain at `c` from the player's view: visible→real, remembered→last
    seen, never-seen→None."""
    tile = view.terrain_at(c)
    return tile.terrain if tile is not None else None


def frontier_cells(view: WorldView) -> frozenset[Coord]:
    """All frontier cells on the board: seen, land-walkable, with at least
    one unseen on-board 8-neighbor — the places exploration pushes into.
    One sweep per view; cache the result per planning view, not per unit.
    """
    seen = view.own_player.view.seen
    real_map = view.real_map()
    results: set[Coord] = set()
    for y in range(real_map.height):
        for x in range(real_map.width):
            c = Coord(x, y)
            if not seen(c):
                continue
            terrain = terrain_for_view(view, c)
            if terrain is None or terrain is TerrainKind.WATER:
                continue
            for n in c.neighbors():
                if view.in_bounds(n) and not seen(n):
                    results.add(c)
                    break
    return frozenset(results)

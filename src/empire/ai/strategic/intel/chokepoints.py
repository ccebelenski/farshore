"""Choke-point detection: straits and isthmi that funnel movement.

A purely *local* test: a cell is a choke point when it is pinched between
opposite-terrain cells on one axis. A navigable water cell flanked by land to
east and west is a vertical strait (north-south traffic only); a land cell
flanked by water to north and south is a horizontal isthmus, and so on. This
is deterministic and cheap, and catches the one-cell pinches that matter for
defence without the cost of global connectivity analysis.

Only cells whose terrain *and* whose two flanking cells' terrain are known are
considered — we do not guess choke points through fog. Pure over `WorldView`.
"""

from __future__ import annotations

from empire.ai.strategic.intel.report import ChokeAxis, ChokePoint, ChokePointKind
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.tile import LAND_TERRAINS, TerrainKind


def find_chokepoints(view: WorldView) -> tuple[ChokePoint, ...]:
    """Find every one-cell strait or isthmus among the player's known terrain."""

    def terrain(c: Coord) -> TerrainKind | None:
        tile = view.terrain_at(c)
        return tile.terrain if tile is not None else None

    # Known cells: currently visible plus remembered. Sorted for reproducibility.
    known: set[Coord] = set(view.visible_tiles()) | set(view.remembered_tiles())

    points: list[ChokePoint] = []
    for c in sorted(known, key=lambda p: (p.x, p.y)):
        here = terrain(c)
        if here is None:
            continue
        east = terrain(Coord(c.x + 1, c.y))
        west = terrain(Coord(c.x - 1, c.y))
        north = terrain(Coord(c.x, c.y - 1))
        south = terrain(Coord(c.x, c.y + 1))

        if here is TerrainKind.WATER:
            if east in LAND_TERRAINS and west in LAND_TERRAINS:
                points.append(ChokePoint(c, ChokePointKind.STRAIT, ChokeAxis.VERTICAL))
            elif north in LAND_TERRAINS and south in LAND_TERRAINS:
                points.append(ChokePoint(c, ChokePointKind.STRAIT, ChokeAxis.HORIZONTAL))
        elif here in LAND_TERRAINS:
            if east is TerrainKind.WATER and west is TerrainKind.WATER:
                points.append(ChokePoint(c, ChokePointKind.ISTHMUS, ChokeAxis.VERTICAL))
            elif north is TerrainKind.WATER and south is TerrainKind.WATER:
                points.append(ChokePoint(c, ChokePointKind.ISTHMUS, ChokeAxis.HORIZONTAL))

    return tuple(points)

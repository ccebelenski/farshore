"""Canary tests for choke-point detection (Phase 11)."""

from __future__ import annotations

from empire.ai.strategic.intel.chokepoints import find_chokepoints
from empire.ai.strategic.intel.report import ChokeAxis, ChokePointKind
from empire.core.coord import Coord
from tests.empire.ai.strategic.intel._world import grid_map, player, reveal_all, world


def test_vertical_strait_between_two_lands() -> None:
    """A water column flanked by land east and west is a vertical strait."""
    p1 = player(1)
    rmap = grid_map(
        [
            ".~.",
            ".~.",
            ".~.",
        ]
    )
    reveal_all(p1, rmap)

    points = find_chokepoints(world(rmap, p1))

    straits = {pt.coord for pt in points if pt.kind is ChokePointKind.STRAIT}
    assert Coord(1, 1) in straits
    middle = next(pt for pt in points if pt.coord == Coord(1, 1))
    assert middle.axis is ChokeAxis.VERTICAL


def test_horizontal_isthmus_between_two_waters() -> None:
    """A land cell flanked by water north and south is a horizontal isthmus."""
    p1 = player(1)
    rmap = grid_map(
        [
            "~~~",
            "...",
            "~~~",
        ]
    )
    reveal_all(p1, rmap)

    points = find_chokepoints(world(rmap, p1))

    isthmi = [pt for pt in points if pt.kind is ChokePointKind.ISTHMUS]
    assert any(pt.coord == Coord(1, 1) and pt.axis is ChokeAxis.HORIZONTAL for pt in isthmi)


def test_open_field_has_no_chokepoints() -> None:
    p1 = player(1)
    rmap = grid_map(["....." for _ in range(5)])
    reveal_all(p1, rmap)
    assert find_chokepoints(world(rmap, p1)) == ()


def test_unknown_flanks_are_not_chokepoints() -> None:
    """Detection never fires through fog: a strait cell stays unflagged until
    both flanking cells have been seen."""
    p1 = player(1)
    rmap = grid_map([".~."])
    # Reveal only the water cell, not its land flanks.
    reveal_all_water_only = Coord(1, 0)
    p1.view.visible.add(reveal_all_water_only)

    assert find_chokepoints(world(rmap, p1)) == ()

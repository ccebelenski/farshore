"""Service-level canary tests: assembly and the purity guarantee (Phase 11)."""

from __future__ import annotations

from empire.ai.strategic.intel.service import IntelService
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, UnitId
from empire.core.map import Map
from empire.core.player import Player
from empire.core.unit import Army
from tests.empire.ai.strategic.intel._world import (
    grid_map,
    place,
    player,
    reveal_all,
    world,
)


def _scenario() -> tuple[Map, Player]:
    """A small mixed world with a city, a neutral, an enemy, and a strait."""
    p1 = player(1)
    p2 = player(2, "Enemy")
    mine = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    neutral = City(id=CityId(2), coord=Coord(1, 2), owner=None)
    rmap = grid_map(
        [
            "..~..",
            "..~..",
            "..~..",
        ],
        cities={mine.coord: mine, neutral.coord: neutral},
    )
    place(rmap, Army(UnitId(1), p1, Coord(0, 1)))
    place(rmap, Army(UnitId(2), p2, Coord(4, 1)))
    reveal_all(p1, rmap)
    return rmap, p1


def test_assess_populates_all_four_sections() -> None:
    rmap, p1 = _scenario()
    report = IntelService().assess(world(rmap, p1, turn=7))

    assert report.turn == 7
    assert report.threats  # the enemy army
    assert report.opportunities  # neutral city + enemy unit
    assert report.chokepoints  # the central water strait
    assert report.theaters  # two landmasses split by the strait
    assert len(report.theaters) == 2


def test_same_worldview_yields_equal_report() -> None:
    """Purity: two assessments of the same view are deeply equal."""
    rmap, p1 = _scenario()
    view = world(rmap, p1, turn=7)
    service = IntelService()

    assert service.assess(view) == service.assess(view)


def test_report_is_hashable_and_frozen() -> None:
    """Frozen artifacts: the whole report can be hashed (used as a set member)."""
    rmap, p1 = _scenario()
    report = IntelService().assess(world(rmap, p1))
    # Equal reports hash equally; this also proves every nested field is hashable.
    assert hash(report) == hash(IntelService().assess(world(rmap, p1)))

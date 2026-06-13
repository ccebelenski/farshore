"""`MapView` danger-overlay semantics (hostile artillery range tinge).

The fog contract (playtest request, 2026-06-12): the ring exists for any
*discovered* hostile city (enemy or neutral), but the tinge is only ever
painted on currently *visible* cells — `MapView.danger_cells` carries the
threat picture, `MapWidget` applies the visibility filter.
"""

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId
from empire.core.map import Map, RememberedTile, ViewMap
from empire.core.player import Player
from empire.core.tile import TerrainKind, Tile
from empire.tui.widgets.map_widget import MapView


def _flat_map(width: int = 12, height: int = 8) -> Map:
    tiles = {
        Coord(x, y): Tile(coord=Coord(x, y), terrain=TerrainKind.LAND)
        for x in range(width)
        for y in range(height)
    }
    return Map(width=width, height=height, tiles=tiles)


def _with_city(m: Map, city: City) -> None:
    m._tiles[city.coord] = Tile(  # pyright: ignore[reportPrivateUsage]
        coord=city.coord, terrain=TerrainKind.CITY, city=city
    )


def _viewer() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


def test_discovered_hostile_city_projects_its_ring() -> None:
    m = _flat_map()
    viewer = _viewer()
    _with_city(m, City(id=CityId(1), coord=Coord(5, 4), owner=None))  # neutral
    viewer.view.visible = {Coord(x, y) for x in range(12) for y in range(8)}

    mv = MapView(real_map=m, viewer=viewer, artillery_range=2)
    assert Coord(5, 4) in mv.danger_cells  # the city cell itself
    assert Coord(3, 2) in mv.danger_cells  # corner of the chebyshev-2 ring
    assert Coord(2, 4) not in mv.danger_cells  # chebyshev 3: outside


def test_undiscovered_city_projects_nothing() -> None:
    m = _flat_map()
    viewer = _viewer()
    _with_city(m, City(id=CityId(1), coord=Coord(5, 4), owner=None))
    viewer.view.visible = {Coord(0, 0)}  # city never seen

    mv = MapView(real_map=m, viewer=viewer, artillery_range=2)
    assert not mv.danger_cells


def test_remembered_city_still_projects() -> None:
    """Discovery persists: a city you've seen and lost sight of still has
    known guns — its ring tints whatever you CAN currently see near it."""
    m = _flat_map()
    viewer = _viewer()
    _with_city(m, City(id=CityId(1), coord=Coord(5, 4), owner=None))
    viewer.view.remembered[Coord(5, 4)] = RememberedTile(
        coord=Coord(5, 4), terrain=TerrainKind.CITY, remembered_at=3
    )

    mv = MapView(real_map=m, viewer=viewer, artillery_range=2)
    assert Coord(4, 4) in mv.danger_cells


def test_own_city_and_no_artillery_rules_project_nothing() -> None:
    m = _flat_map()
    viewer = _viewer()
    _with_city(m, City(id=CityId(1), coord=Coord(5, 4), owner=viewer))
    viewer.view.visible = {Coord(x, y) for x in range(12) for y in range(8)}

    assert not MapView(real_map=m, viewer=viewer, artillery_range=2).danger_cells
    # And hostile city under classic rules (range 0): no overlay at all.
    _with_city(m, City(id=CityId(2), coord=Coord(8, 4), owner=None))
    assert not MapView(real_map=m, viewer=viewer, artillery_range=0).danger_cells

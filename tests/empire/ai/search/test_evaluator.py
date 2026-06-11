"""`Evaluator` ordering canaries on hand-built positions (Phase 15.8 Step 1).

The evaluator carries the search's long-term judgment, so what matters is
*ordering*: winning > drawn > losing, more cities > fewer, a city must
outweigh any plausible skirmish gain, and production-in-flight is worth a
fraction of a finished unit.
"""

from empire.ai.search.evaluator import Evaluator
from empire.core.city import City, ProductionState
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, UnitKind

P1 = PlayerId(1)
P2 = PlayerId(2)


def _game(
    p1_cities: int,
    p2_cities: int,
    p1_armies: int = 0,
    p2_armies: int = 0,
    neutral_cities: int = 0,
    p1_city_work: int = 0,
) -> Game:
    """A flat 10x10 land board with the requested asset counts."""
    p1 = Player(id=P1, name="P1", is_ai=True, view=ViewMap(), color="red")
    p2 = Player(id=P2, name="P2", is_ai=True, view=ViewMap(), color="blue")
    tiles: dict[Coord, Tile] = {
        Coord(x, y): Tile(coord=Coord(x, y), terrain=TerrainKind.LAND)
        for x in range(10)
        for y in range(10)
    }

    next_city = 1

    def add_city(owner: Player | None, at: Coord, work: int = 0) -> None:
        nonlocal next_city
        production = ProductionState(
            building=UnitKind.ARMY if owner is not None else None, work=work
        )
        city = City(id=CityId(next_city), coord=at, owner=owner, production=production)
        next_city += 1
        tiles[at] = Tile(coord=at, terrain=TerrainKind.CITY, city=city)

    for i in range(p1_cities):
        add_city(p1, Coord(i, 0), work=p1_city_work)
    for i in range(p2_cities):
        add_city(p2, Coord(i, 9))
    for i in range(neutral_cities):
        add_city(None, Coord(i, 5))

    real_map = Map(width=10, height=10, tiles=tiles)
    next_unit = 1

    def add_army(owner: Player, at: Coord) -> None:
        nonlocal next_unit
        army = Army(UnitId(next_unit), owner, at)
        next_unit += 1
        real_map.place_unit(army, at)

    for i in range(p1_armies):
        add_army(p1, Coord(i, 1))
    for i in range(p2_armies):
        add_army(p2, Coord(i, 8))

    return Game(rules=STANDARD, real_map=real_map, players=[p1, p2], seed=1)


def test_symmetric_position_scores_zero() -> None:
    g = _game(p1_cities=2, p2_cities=2, p1_armies=3, p2_armies=3)
    assert Evaluator().evaluate(g, P1) == 0.0
    assert Evaluator().evaluate(g, P2) == 0.0


def test_won_game_dominates_any_material() -> None:
    won = _game(p1_cities=1, p2_cities=0, p2_armies=50)
    rich = _game(p1_cities=4, p2_cities=1, p1_armies=20)
    e = Evaluator()
    assert e.evaluate(won, P1) > e.evaluate(rich, P1)
    assert e.evaluate(won, P2) < -abs(e.evaluate(rich, P2))


def test_more_cities_beats_fewer() -> None:
    e = Evaluator()
    two = _game(p1_cities=2, p2_cities=2)
    three = _game(p1_cities=3, p2_cities=2)
    assert e.evaluate(three, P1) > e.evaluate(two, P1)


def test_a_city_outweighs_a_handful_of_armies() -> None:
    e = Evaluator()
    city_up = _game(p1_cities=3, p2_cities=2)
    armies_up = _game(p1_cities=2, p2_cities=2, p1_armies=5)
    assert e.evaluate(city_up, P1) > e.evaluate(armies_up, P1)


def test_zero_sum_symmetry() -> None:
    g = _game(p1_cities=3, p2_cities=1, p1_armies=2, p2_armies=4)
    e = Evaluator()
    assert e.evaluate(g, P1) == -e.evaluate(g, P2)


def test_neutral_cities_are_not_assets() -> None:
    e = Evaluator()
    bare = _game(p1_cities=2, p2_cities=2)
    ringed = _game(p1_cities=2, p2_cities=2, neutral_cities=4)
    assert e.evaluate(bare, P1) == e.evaluate(ringed, P1)


def test_production_in_flight_counts_fractionally() -> None:
    e = Evaluator()
    cold = _game(p1_cities=1, p2_cities=2)
    warm = _game(p1_cities=1, p2_cities=2, p1_city_work=4)  # army is 5 work
    gain = e.evaluate(warm, P1) - e.evaluate(cold, P1)
    assert 0.0 < gain < 10.0  # worth something, less than a finished army

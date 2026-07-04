"""`Evaluator` ordering canaries on hand-built positions (Phase 15.8 Step 1).

The evaluator carries the search's long-term judgment, so what matters is
*ordering*: winning > drawn > losing, more cities > fewer, a city must
outweigh any plausible skirmish gain, and production-in-flight is worth a
fraction of a finished unit.
"""

from empire.ai.search.evaluator import Evaluator, EvalWeights
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


# --- §10.2 value-surface terms: opportunity + exploration --------------------

# Isolating weights: each turns on exactly one of the new terms (intel off).
_OPP_ONLY = EvalWeights(intel=0.0, explore_land=0.0, explore_sea=0.0)
_EXPLORE_ONLY = EvalWeights(intel=0.0, opportunity=0.0)


def _see_all(game: Game, pid: PlayerId) -> None:
    p = game.player_by_id(pid)
    assert p is not None
    p.view.visible = {Coord(x, y) for x in range(10) for y in range(10)}


def test_discovered_unowned_city_is_an_opportunity() -> None:
    """A known, reachable, unowned city carries positive value — discovery is a
    draw toward the prize, not the old penalty/zero."""
    e = Evaluator(_OPP_ONLY)
    g = _game(p1_cities=1, p2_cities=1, neutral_cities=1)
    blind = e.evaluate(g, P1)  # empty view: prize unknown → no opportunity
    _see_all(g, P1)
    assert e.evaluate(g, P1) > blind


def test_capturing_beats_loitering_beside_the_prize() -> None:
    """Opportunity must stay below the value of actually owning the city, so the
    search never prefers sitting next to a prize over taking it."""
    e = Evaluator(_OPP_ONLY)
    g = _game(p1_cities=1, p2_cities=1, neutral_cities=1)
    _see_all(g, P1)
    loiter = e.evaluate(g, P1)
    prize = next(c for c in g.map.cities() if c.owner is None)
    prize.owner = g.player_by_id(P1)
    assert e.evaluate(g, P1) > loiter


def test_opportunity_decays_with_distance() -> None:
    """A reachable prize nearer our force is worth more than a farther one."""
    e = Evaluator(_OPP_ONLY)
    near = _game(p1_cities=1, p2_cities=1)
    far = _game(p1_cities=1, p2_cities=1)
    # add one neutral city, near vs far from P1's anchor at (0, 0)
    for g, at in ((near, Coord(2, 0)), (far, Coord(9, 9))):
        city = City(id=CityId(99), coord=at, owner=None,
                    production=ProductionState(building=None, work=0))
        g.map._tiles[at] = Tile(coord=at, terrain=TerrainKind.CITY, city=city)
        _see_all(g, P1)
    assert e.evaluate(near, P1) > e.evaluate(far, P1)


def test_exploration_rewards_information_gained() -> None:
    """Seeing more of the map raises the score — a monotone scouting gradient,
    the opposite of the old perimeter penalty."""
    e = Evaluator(_EXPLORE_ONLY)
    # An army must exist for land reveals to count (the term is force-gated and
    # reachability-gated — seeing ground you can't reach is worth nothing).
    g = _game(p1_cities=1, p2_cities=1, p1_armies=1)
    p = g.player_by_id(P1)
    assert p is not None
    p.view.visible = {Coord(x, y) for x in range(3) for y in range(3)}
    less = e.evaluate(g, P1)
    p.view.visible = {Coord(x, y) for x in range(10) for y in range(10)}
    assert e.evaluate(g, P1) > less

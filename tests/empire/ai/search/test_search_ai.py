"""`SearchAI` + `BeliefBuilder` + `CandidateGenerator` canaries (Step 2).

The decisive validation is the arena; these tests pin the deterministic
plumbing: the belief reflects exactly the fog view, the generator proposes
the expected shapes, and the search prefers an obviously winning plan over
an obviously passive one.
"""

from empire._arena import ARENA_PROFILE, build_land_brawl
from empire.ai.baseline import BaselineAI
from empire.ai.search.ai import SearchAI
from empire.ai.search.belief import BeliefBuilder
from empire.ai.search.generator import CandidateGenerator
from empire.ai.search.plan import Role
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.player import Player
from empire.core.ruleset import FORTIFIED_CITIES
from empire.core.tile import TerrainKind


def _game_at(turn: int) -> tuple[Game, list[Player]]:
    built = build_land_brawl(ARENA_PROFILE, seed=3, rules=FORTIFIED_CITIES)
    assert built is not None
    game, players = built
    for p in players:
        game.attach_controller(p.id, BaselineAI())
    while game.turn < turn and not game.is_over():
        game.run_turn()
    return game, players


def _view(game: Game, player: Player) -> WorldView:
    return WorldView(real_map=game.map, player=player, turn=game.turn, rules=game.rules)


# --- BeliefBuilder -------------------------------------------------------------


def test_belief_contains_exactly_the_known_world() -> None:
    game, players = _game_at(20)
    me = players[0]
    view = _view(game, me)
    belief = BeliefBuilder().build(view)

    # Same dimensions and ruleset.
    assert (belief.map.width, belief.map.height) == (game.map.width, game.map.height)
    assert belief.rules is view.rules

    # Every known city is present with the right owner id; no unknown cities.
    known = {
        c.coord
        for c in (
            view.own_cities + view.known_enemy_cities + view.neutral_cities
        )
    }
    belief_cities = {c.coord for c in belief.map.cities()}
    assert belief_cities == known

    # Own units fully present at their true coords.
    own_coords = {u.coord for u in view.own_units if u.carried_by is None}
    belief_mine = {
        u.coord for u in belief.map.all_units() if u.owner.id == me.id
    }
    assert belief_mine == own_coords

    # Never-seen cells are optimistically plain land.
    seen = me.view.seen
    for y in range(belief.map.height):
        for x in range(belief.map.width):
            c = Coord(x, y)
            if not seen(c):
                assert belief.map.terrain_at(c) is TerrainKind.LAND


def test_belief_is_deterministic_per_turn() -> None:
    game, players = _game_at(20)
    view = _view(game, players[0])
    s = BeliefBuilder()
    a, b = s.build(view), s.build(view)
    assert a.rng.getstate() == b.rng.getstate()  # common random numbers
    assert {u.coord for u in a.map.all_units()} == {
        u.coord for u in b.map.all_units()
    }


# --- CandidateGenerator ---------------------------------------------------------


def test_generator_proposes_assaults_and_baselines() -> None:
    game, players = _game_at(20)
    view = _view(game, players[0])
    plans = CandidateGenerator().generate(view)

    assert 2 <= len(plans) <= 32
    # At least one assault candidate against a known city (turn 20 has some).
    assault_targets = {
        o.target
        for p in plans
        for o in p.objectives
        if o.role is Role.ASSAULT
    }
    known = {
        c.coord for c in (view.known_enemy_cities + view.neutral_cities)
    }
    assert assault_targets and assault_targets <= known
    # The do-nothing baselines are always present.
    assert any(not p.objectives for p in plans)


def test_generator_fist_respects_artillery_range() -> None:
    game, players = _game_at(20)
    view = _view(game, players[0])
    plans = CandidateGenerator().generate(view)
    strengths = {
        o.strength
        for p in plans
        for o in p.objectives
        if o.role is Role.ASSAULT
    }
    fist = view.rules.city_artillery_range + 1  # FORTIFIED: 3
    assert fist in strengths
    assert fist + 2 in strengths  # the over-strength variant


# --- SearchAI end-to-end ---------------------------------------------------------


def test_search_ai_plays_a_turn_with_well_formed_output() -> None:
    game, players = _game_at(20)
    me = players[0]
    ai = SearchAI(horizon=6)
    plan = ai.plan_turn(_view(game, me))
    own_ids = {u.id for u in game.map.all_units() if u.owner is me}
    for move in plan.moves:
        assert move.unit_id in own_ids  # never commands foreign units
        assert move.path  # empty moves are filtered out


def test_search_prefers_capturing_a_defenseless_city() -> None:
    """One army, one adjacent undefended neutral city (STANDARD rules, no
    artillery): every sensible playout captures it. The committed plan must
    send the army at it rather than hold."""
    from empire.core.city import City
    from empire.core.identity import CityId, PlayerId, UnitId
    from empire.core.map import Map, ViewMap
    from empire.core.ruleset import STANDARD
    from empire.core.tile import Tile
    from empire.core.unit import Army

    p1 = Player(id=PlayerId(1), name="P1", is_ai=True, view=ViewMap(), color="red")
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap(), color="blue")
    tiles: dict[Coord, Tile] = {
        Coord(x, y): Tile(coord=Coord(x, y), terrain=TerrainKind.LAND)
        for x in range(8)
        for y in range(8)
    }
    home = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    tiles[home.coord] = Tile(coord=home.coord, terrain=TerrainKind.CITY, city=home)
    prize = City(id=CityId(2), coord=Coord(4, 0), owner=None)
    tiles[prize.coord] = Tile(coord=prize.coord, terrain=TerrainKind.CITY, city=prize)
    real_map = Map(width=8, height=8, tiles=tiles)
    army = Army(UnitId(1), p1, Coord(2, 0))
    real_map.place_unit(army, Coord(2, 0))
    game = Game(rules=STANDARD, real_map=real_map, players=[p1, p2], seed=5)
    p1.view.visible = {Coord(x, y) for x in range(8) for y in range(8)}

    ai = SearchAI(horizon=8)
    turn_plan = ai.plan_turn(_view(game, p1))
    moves = {int(m.unit_id): m.path for m in turn_plan.moves}
    path = moves.get(1)
    assert path is not None, "the army must be sent somewhere"
    assert Coord(*path[0]).chebyshev_to(prize.coord) < Coord(2, 0).chebyshev_to(
        prize.coord
    ), "the army should close on the free city"

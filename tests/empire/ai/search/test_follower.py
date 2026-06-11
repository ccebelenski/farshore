"""`PlanFollower` canaries: deterministic assignment, role movement, §5.4
discipline, surplus policies, production orders (Phase 15.8 Step 1)."""

from empire.ai.search.follower import PlanFollower
from empire.ai.search.plan import Objective, Plan, Role, SurplusPolicy
from empire.contracts.world_view import WorldView
from empire.core.city import City
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


def _flat_game(width: int = 12, height: int = 6) -> tuple[Game, Player, Player]:
    p1 = Player(id=P1, name="P1", is_ai=True, view=ViewMap(), color="red")
    p2 = Player(id=P2, name="P2", is_ai=True, view=ViewMap(), color="blue")
    tiles: dict[Coord, Tile] = {
        Coord(x, y): Tile(coord=Coord(x, y), terrain=TerrainKind.LAND)
        for x in range(width)
        for y in range(height)
    }
    real_map = Map(width=width, height=height, tiles=tiles)
    game = Game(rules=STANDARD, real_map=real_map, players=[p1, p2], seed=1)
    return game, p1, p2


def _see_all(game: Game, player: Player) -> None:
    player.view.visible = {
        Coord(x, y) for x in range(game.map.width) for y in range(game.map.height)
    }


def _add_city(game: Game, owner: Player | None, at: Coord, city_id: int) -> City:
    city = City(id=CityId(city_id), coord=at, owner=owner)
    game.map._tiles[at] = Tile(  # pyright: ignore[reportPrivateUsage]
        coord=at, terrain=TerrainKind.CITY, city=city
    )
    return city


def _add_army(game: Game, owner: Player, at: Coord, unit_id: int) -> Army:
    army = Army(UnitId(unit_id), owner, at)
    game.map.place_unit(army, at)
    return army


def _view(game: Game, player: Player) -> WorldView:
    return WorldView(real_map=game.map, player=player, turn=game.turn, rules=game.rules)


def _moves_by_unit(follower: PlanFollower, view: WorldView) -> dict[int, tuple[tuple[int, int], ...]]:
    plan = follower.plan_turn(view)
    return {int(m.unit_id): m.path for m in plan.moves}


def test_assault_marches_toward_target() -> None:
    game, p1, _ = _flat_game()
    _see_all(game, p1)
    _add_army(game, p1, Coord(0, 0), 1)
    follower = PlanFollower(
        Plan(objectives=(Objective(Coord(11, 0), Role.ASSAULT, 1),))
    )
    moves = _moves_by_unit(follower, _view(game, p1))
    path = moves[1]
    assert len(path) == 1  # one step (army speed 1)
    # The step closes distance to the target (path_to may wiggle between
    # equal-length diagonals, so assert progress, not an exact cell).
    assert Coord(*path[0]).chebyshev_to(Coord(11, 0)) == 10


def test_nearest_armies_assigned_first_then_surplus() -> None:
    game, p1, _ = _flat_game()
    _see_all(game, p1)
    near = _add_army(game, p1, Coord(8, 0), 1)
    far = _add_army(game, p1, Coord(0, 0), 2)
    follower = PlanFollower(
        Plan(
            objectives=(Objective(Coord(11, 0), Role.ASSAULT, 1),),
            surplus=SurplusPolicy.RESERVE,
        )
    )
    moves = _moves_by_unit(follower, _view(game, p1))
    near_path = moves.get(int(near.id))
    assert near_path is not None  # the near army takes the objective
    assert Coord(*near_path[0]).chebyshev_to(Coord(11, 0)) == 2  # and advances
    assert int(far.id) not in moves  # reserve surplus holds (no path emitted)


def test_defend_holds_at_city_edge() -> None:
    game, p1, _ = _flat_game()
    _see_all(game, p1)
    _add_city(game, p1, Coord(5, 0), 1)
    _add_army(game, p1, Coord(6, 0), 1)  # already adjacent
    follower = PlanFollower(
        Plan(objectives=(Objective(Coord(5, 0), Role.DEFEND, 1),))
    )
    moves = _moves_by_unit(follower, _view(game, p1))
    assert int(UnitId(1)) not in moves  # adjacent garrison: hold (no move)


def test_defender_never_parks_on_friendly_city() -> None:
    """§5.4: an army idling ON a friendly city must step off, not sentry."""
    game, p1, _ = _flat_game()
    _see_all(game, p1)
    _add_city(game, p1, Coord(5, 0), 1)
    _add_army(game, p1, Coord(5, 0), 1)  # standing on its own city
    follower = PlanFollower(
        Plan(objectives=(Objective(Coord(5, 0), Role.DEFEND, 1),))
    )
    moves = _moves_by_unit(follower, _view(game, p1))
    path = moves.get(1)
    assert path is not None and len(path) == 1
    assert Coord(*path[0]) != Coord(5, 0)  # stepped off the city


def test_scout_surplus_heads_for_frontier() -> None:
    game, p1, _ = _flat_game()
    # Only the left half is seen: frontier at the seen/unseen boundary.
    p1.view.visible = {Coord(x, y) for x in range(6) for y in range(6)}
    _add_army(game, p1, Coord(0, 0), 1)
    follower = PlanFollower(Plan(objectives=(), surplus=SurplusPolicy.SCOUT))
    moves = _moves_by_unit(follower, _view(game, p1))
    path = moves.get(1)
    assert path is not None and len(path) == 1
    assert path[0][0] > 0  # moving toward the unseen east, not sitting still


def test_idle_cities_get_production_orders() -> None:
    game, p1, _ = _flat_game()
    _see_all(game, p1)
    _add_city(game, p1, Coord(2, 2), 1)
    follower = PlanFollower(Plan.hold())
    plan = follower.plan_turn(_view(game, p1))
    assert len(plan.production_orders) == 1
    order = plan.production_orders[0]
    assert order.city_id == CityId(1)
    assert order.target is UnitKind.ARMY


def test_assignment_is_deterministic() -> None:
    game, p1, _ = _flat_game()
    _see_all(game, p1)
    for i in range(4):
        _add_army(game, p1, Coord(i, 3), i + 1)
    follower = PlanFollower(
        Plan(
            objectives=(
                Objective(Coord(11, 3), Role.ASSAULT, 2),
                Objective(Coord(0, 5), Role.ASSAULT, 2),
            )
        )
    )
    first = _moves_by_unit(follower, _view(game, p1))
    again = _moves_by_unit(follower, _view(game, p1))
    assert first == again

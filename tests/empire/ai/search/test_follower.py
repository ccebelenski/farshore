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


def test_assault_group_stages_outside_artillery_range_then_storms() -> None:
    """Anti-trickle: under FortifiedCities, the lead army holds at the ring
    (range+1) until the whole group is staged; once staged, all enter."""
    from empire.core.ruleset import FORTIFIED_CITIES

    game, p1, p2 = _flat_game(width=14)
    game.rules = FORTIFIED_CITIES  # range 2 -> ring at 3
    _see_all(game, p1)
    _add_city(game, p2, Coord(13, 0), 1)  # enemy city = defended target
    lead = _add_army(game, p1, Coord(10, 0), 1)  # at the ring already (cheb 3)
    trail = _add_army(game, p1, Coord(2, 0), 2)  # far behind
    follower = PlanFollower(
        Plan(objectives=(Objective(Coord(13, 0), Role.ASSAULT, 2),))
    )

    moves = _moves_by_unit(follower, _view(game, p1))
    # Lead holds outside range while the trail catches up...
    assert int(lead.id) not in moves or all(
        Coord(*c).chebyshev_to(Coord(13, 0)) > 2 for c in moves[int(lead.id)]
    )
    # ...and the trail advances.
    assert int(trail.id) in moves

    # Once both are staged, the storm begins: the lead enters artillery range.
    game.map.move_unit(trail, Coord(9, 1))  # cheb 4 = staged
    moves = _moves_by_unit(follower, _view(game, p1))
    lead_path = moves.get(int(lead.id))
    assert lead_path is not None
    assert Coord(*lead_path[0]).chebyshev_to(Coord(13, 0)) <= 2


def test_narrow_causeway_assault_storms_instead_of_freezing() -> None:
    """Terrain-aware storm gate: a fortified city on a single-wide coastal
    causeway cannot fit a whole fist within the ring, so the old 'wait for
    everyone' quorum froze the assault at the ring forever. The quorum is
    capped by the shore's staging capacity — the lead storms with what fits."""
    from empire.core.ruleset import FORTIFIED_CITIES

    p1 = Player(id=P1, name="P1", is_ai=True, view=ViewMap(), color="red")
    p2 = Player(id=P2, name="P2", is_ai=True, view=ViewMap(), color="blue")
    width, height = 11, 5
    tiles: dict[Coord, Tile] = {}
    for x in range(width):
        for y in range(height):
            c = Coord(x, y)
            land = x <= 4 or (y == 2 and x <= 9)  # main block + 1-wide causeway
            tiles[c] = Tile(
                coord=c, terrain=TerrainKind.LAND if land else TerrainKind.WATER
            )
    game = Game(
        rules=FORTIFIED_CITIES,  # range 2 -> ring at 3; only 2 tiles fit the band
        real_map=Map(width=width, height=height, tiles=tiles),
        players=[p1, p2],
        seed=1,
    )
    _see_all(game, p1)
    city = Coord(10, 2)
    _add_city(game, None, city, 1)  # neutral fortified city — it fires too
    # A 5-army fist queued single-file back down the causeway: only (7,2) and
    # (6,2) sit within the ring+1 band; the rest cannot close (one-per-tile).
    lead = _add_army(game, p1, Coord(7, 2), 1)  # cheb 3, at the ring
    for i, x in enumerate((6, 5, 4, 3), start=2):
        _add_army(game, p1, Coord(x, 2), i)
    follower = PlanFollower(
        Plan(objectives=(Objective(city, Role.ASSAULT, 5),))
    )

    moves = _moves_by_unit(follower, _view(game, p1))
    lead_path = moves.get(int(lead.id))
    assert lead_path is not None, "lead army froze at the ring (stall)"
    assert Coord(*lead_path[0]).chebyshev_to(city) <= 2, "lead did not storm the ring"


def test_open_approach_assault_storms_once_the_floor_is_staged() -> None:
    """Regression: on an OPEN approach the old quorum was the WHOLE fist, which
    a straggler that can't pack the ring band leaves permanently unmet — so a
    committed assault held at the ring forever ('backed off for lack of
    force'). The punch-through floor storms once a few are staged; the
    straggler follows."""
    from empire.core.ruleset import FORTIFIED_CITIES

    game, p1, p2 = _flat_game(width=13, height=5)  # all land -> large capacity
    game.rules = FORTIFIED_CITIES  # range 2 -> ring 3
    _see_all(game, p1)
    city = Coord(10, 2)
    _add_city(game, p2, city, 1)
    # Three armies staged at the ring (cheb 3); a fourth stranded far back
    # (cheb 8) that cannot reach the band this turn.
    staged = [
        _add_army(game, p1, Coord(7, 2), 1),
        _add_army(game, p1, Coord(7, 1), 2),
        _add_army(game, p1, Coord(7, 3), 3),
    ]
    _add_army(game, p1, Coord(2, 2), 4)  # the straggler
    follower = PlanFollower(Plan(objectives=(Objective(city, Role.ASSAULT, 4),)))

    moves = _moves_by_unit(follower, _view(game, p1))
    # The three storm — each steps into the fire zone — instead of idling at
    # the ring waiting for a fourth that isn't coming.
    for u in staged:
        path = moves.get(int(u.id))
        assert path, f"staged army #{int(u.id)} idled instead of storming"
        assert Coord(*path[0]).chebyshev_to(city) <= 2


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

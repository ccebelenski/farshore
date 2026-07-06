"""`DoctrineCompiler` steering canaries (integration handshake (a)):
doctrine from a scripted general drives real units toward their own task
force's objective — and nobody else's — through the untouched executor."""

from __future__ import annotations

from empire.ai.general.compiler import DoctrineCompiler
from empire.ai.general.fake import FakeGeneral
from empire.ai.general.registry import TaskForceRegistry
from empire.contracts.doctrine import (
    Briefing,
    ContinueOrder,
    Doctrine,
    FormOrder,
    Objective,
    Verb,
)
from empire.contracts.world_view import WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Unit

CAPTURE_TARGET = Coord(11, 2)
STAGE_TARGET = Coord(0, 5)


def _flat_game(width: int = 12, height: int = 6) -> tuple[Game, Player, Player]:
    p1 = Player(id=PlayerId(1), name="P1", is_ai=True, view=ViewMap(), color="red")
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap(), color="blue")
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


def _staged_board() -> tuple[Game, WorldView, dict[int, Unit]]:
    """Flat land, all seen: an enemy city east, armies #1/#2 west (the strike
    force) and #3 mid-board (the marshaling force). #3 is NEAREST to the
    capture target — an unscoped nearest-first assignment would grab it, so
    the steering assertion below is only satisfiable via TF scoping."""
    game, p1, p2 = _flat_game()
    _see_all(game, p1)
    _add_city(game, p2, CAPTURE_TARGET, 1)
    units = {
        1: _add_army(game, p1, Coord(1, 2), 1),
        2: _add_army(game, p1, Coord(2, 2), 2),
        3: _add_army(game, p1, Coord(6, 2), 3),
    }
    return game, _view(game, p1), units


def _tasked_registry(view: WorldView) -> TaskForceRegistry:
    """One epoch from the scripted general: FORM the strike force (#1 #2 ->
    CAPTURE) and the marshaling force (#3 -> STAGE), applied to an empty
    registry over the live roster."""
    general = FakeGeneral(
        [
            Doctrine(
                turn=1,
                amendments=(
                    FormOrder(
                        tf_id="1",
                        unit_ids=(UnitId(1), UnitId(2)),
                        objective=Objective(Verb.CAPTURE, CAPTURE_TARGET),
                        why="storm the eastern city",
                    ),
                    FormOrder(
                        tf_id="2",
                        unit_ids=(UnitId(3),),
                        objective=Objective(Verb.STAGE, STAGE_TARGET),
                        why="marshal the southwest reserve",
                    ),
                ),
            )
        ]
    )
    doctrine = general.decide(Briefing(turn=1, text=""))
    roster = frozenset(u.id for u in view.own_units)
    registry, refusals = TaskForceRegistry().apply(doctrine, roster)
    assert refusals == ()
    return registry


def test_doctrine_steers_each_task_force_toward_its_own_objective() -> None:
    """THE steering test: TF 1's units close on the ordered capture target;
    TF 2's unit heads for its own marshaling point instead, even though it
    is the nearest army to the capture target."""
    _, view, units = _staged_board()
    registry = _tasked_registry(view)

    plan = DoctrineCompiler().plan_moves(registry, view)
    steps = {int(m.unit_id): Coord(*m.path[-1]) for m in plan.moves}

    # TF 1 (#1, #2): every member closes distance to the CAPTURE target.
    for uid in (1, 2):
        before = units[uid].coord.chebyshev_to(CAPTURE_TARGET)
        assert steps[uid].chebyshev_to(CAPTURE_TARGET) < before
    # TF 2 (#3): closes on ITS objective, and does NOT approach TF 1's —
    # despite being the closest army to the capture target.
    before_stage = units[3].coord.chebyshev_to(STAGE_TARGET)
    assert steps[3].chebyshev_to(STAGE_TARGET) < before_stage
    assert steps[3].chebyshev_to(CAPTURE_TARGET) > units[3].coord.chebyshev_to(CAPTURE_TARGET)
    # The seam emits tasked movement only — production is the BUILD channel's.
    assert plan.production_orders == ()


def test_staged_force_holds_at_its_marshaling_point() -> None:
    """A STAGE tasking marshals: adjacent to the coordinate, the member
    holds (no move emitted) instead of wandering off task."""
    game, p1, _ = _flat_game()
    _see_all(game, p1)
    _add_army(game, p1, Coord(1, 5), 3)  # already beside STAGE_TARGET
    view = _view(game, p1)
    registry, refusals = TaskForceRegistry().apply(
        Doctrine(
            turn=1,
            amendments=(
                FormOrder(
                    tf_id="2",
                    unit_ids=(UnitId(3),),
                    objective=Objective(Verb.STAGE, STAGE_TARGET),
                ),
            ),
        ),
        frozenset({UnitId(3)}),
    )
    assert refusals == ()
    plan = DoctrineCompiler().plan_moves(registry, view)
    assert all(int(m.unit_id) != 3 for m in plan.moves)


def test_plan_moves_is_deterministic() -> None:
    _, view, _ = _staged_board()
    registry = _tasked_registry(view)
    compiler = DoctrineCompiler()
    assert compiler.plan_moves(registry, view) == compiler.plan_moves(registry, view)


def test_fake_general_replays_script_then_goes_quiet() -> None:
    """The stub hands out doctrines in order, then empty (all-CONTINUE)
    epochs stamped with the briefing's turn once the script is spent."""
    first = Doctrine(turn=1, amendments=(ContinueOrder(tf_id="1"),))
    second = Doctrine(turn=2, amendments=())
    general = FakeGeneral([first, second])
    assert general.decide(Briefing(turn=1, text="")) is first
    assert general.decide(Briefing(turn=2, text="")) is second
    quiet = general.decide(Briefing(turn=9, text=""))
    assert quiet == Doctrine(turn=9, amendments=())

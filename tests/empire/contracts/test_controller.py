"""Phase-3 canary tests for the `AIController` Protocol and `NullController`."""

from __future__ import annotations

from empire.contracts.controller import AIController, NullController
from empire.contracts.surprise import BlockedBy, PathBlocked
from empire.contracts.turn_plan import TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile


def _tiny_world() -> WorldView:
    tiles = {Coord(0, 0): Tile(coord=Coord(0, 0), terrain=TerrainKind.LAND)}
    m = Map(width=1, height=1, tiles=tiles)
    p = Player(id=PlayerId(1), name="P", is_ai=True, view=ViewMap())
    return WorldView(real_map=m, player=p, turn=0, rules=STANDARD)


# --- Protocol satisfaction ---------------------------------------------------


def test_null_controller_satisfies_protocol_and_is_named() -> None:
    """NullController satisfies the AIController protocol at runtime and reports
    its name. (Static-type acceptance is enforced by pyright; the negative case
    is below.) Consolidates three overlapping isinstance/name canaries."""
    c = NullController()
    assert isinstance(c, AIController)
    assert c.name() == "Null"


def test_arbitrary_object_does_not_satisfy_protocol() -> None:
    assert not isinstance(object(), AIController)


# --- NullController behavior -------------------------------------------------


def test_null_controller_plan_turn_returns_empty_plan() -> None:
    wv = _tiny_world()
    plan = NullController().plan_turn(wv)
    assert isinstance(plan, TurnPlan)
    assert plan.production_orders == ()
    assert plan.moves == ()
    assert plan.sentries == ()


def test_null_controller_revise_move_returns_empty_move() -> None:
    wv = _tiny_world()
    surprise = PathBlocked(blocked_at=Coord(0, 0), by=BlockedBy.TERRAIN)
    move = NullController().revise_move(UnitId(1), surprise, wv)
    assert isinstance(move, UnitMove)
    assert move.unit_id == UnitId(1)
    assert move.path == ()  # empty path = stay put

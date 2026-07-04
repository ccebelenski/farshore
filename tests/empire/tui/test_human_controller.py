"""`HumanController` unit tests.

Headline contract being asserted: `revise_move` returns an empty
`UnitMove` (path == ()) — "stop this turn" — and never produces a
sentry order. Auto-sentry-on-surprise is the explicit anti-rule in
`feedback_no_auto_sentry_on_surprise` memory; this test locks it.
"""

from __future__ import annotations

from empire.contracts.surprise import (
    BlockedBy,
    EnemySighted,
    EscortLost,
    PathBlocked,
    TargetLost,
    TerrainImpassable,
)
from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove
from empire.contracts.world_view import KnownEnemyUnit, WorldView
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, UnitSnapshot, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import UnitKind
from empire.tui.human_controller import HumanController


def _tiny_view() -> WorldView:
    tiles = {Coord(0, 0): Tile(coord=Coord(0, 0), terrain=TerrainKind.LAND)}
    real_map = Map(width=1, height=1, tiles=tiles)
    player = Player(id=PlayerId(1), name="P", is_ai=False, view=ViewMap())
    return WorldView(real_map=real_map, player=player, turn=1, rules=STANDARD)


def test_plan_turn_with_no_pending_returns_empty_plan() -> None:
    """Engine asking before TUI has staged anything: return a do-nothing plan."""
    plan = HumanController().plan_turn(_tiny_view())
    assert plan.production_orders == ()
    assert plan.moves == ()
    assert plan.sentries == ()


def test_set_plan_is_returned_once_then_cleared() -> None:
    """`set_plan` stages a plan; `plan_turn` returns it and clears the slot
    so the next call doesn't accidentally replay the previous turn's plan.
    """
    ctrl = HumanController()
    staged = TurnPlan(
        production_orders=(ProductionOrder(city_id=CityId(7), target=UnitKind.ARMY),),
    )
    ctrl.set_plan(staged)

    first = ctrl.plan_turn(_tiny_view())
    assert first is staged

    second = ctrl.plan_turn(_tiny_view())
    assert second.production_orders == ()
    assert second.moves == ()


# --- The headline contract: revise_move stops, never sentries ---------------


def _all_surprise_variants() -> list[object]:
    """One instance of every concrete `Surprise` subclass."""
    enemy_snap = UnitSnapshot(
        unit_id=UnitId(99),
        kind=UnitKind.ARMY,
        owner_id=PlayerId(2),
        coord=Coord(0, 0),
        hits=1,
    )
    return [
        EnemySighted(enemy=KnownEnemyUnit(snapshot=enemy_snap, seen_at_turn=1), at=Coord(0, 0)),
        PathBlocked(blocked_at=Coord(0, 0), by=BlockedBy.TERRAIN),
        PathBlocked(blocked_at=Coord(0, 0), by=BlockedBy.ENEMY_UNIT),
        PathBlocked(blocked_at=Coord(0, 0), by=BlockedBy.OWN_UNIT),
        PathBlocked(blocked_at=Coord(0, 0), by=BlockedBy.OUT_OF_BOUNDS),
        TargetLost(target_id=UnitId(42)),
        EscortLost(escort_id=UnitId(42)),
        TerrainImpassable(at=Coord(0, 0)),
    ]


def test_revise_move_returns_empty_path_for_every_surprise_variant() -> None:
    """The "stop this turn, never sentry" rule.

    Each Surprise subtype must produce a `UnitMove(unit_id=uid, path=())`
    — that's the engine signal for "no further movement this turn."
    """
    ctrl = HumanController()
    uid = UnitId(5)
    view = _tiny_view()
    for surprise in _all_surprise_variants():
        out = ctrl.revise_move(uid, surprise, view)  # type: ignore[arg-type]
        assert isinstance(out, UnitMove)
        assert out.unit_id == uid
        assert out.path == ()


def test_revise_move_does_not_mutate_pending_plan() -> None:
    """A surprise during execution shouldn't clobber a separately-staged
    plan — `set_plan` is for the *next* `plan_turn`, not this turn's
    in-flight revisions.
    """
    ctrl = HumanController()
    staged = TurnPlan(moves=(UnitMove(unit_id=UnitId(1), path=((1, 0),)),))
    ctrl.set_plan(staged)
    ctrl.revise_move(
        UnitId(1),
        PathBlocked(blocked_at=Coord(0, 0), by=BlockedBy.TERRAIN),
        _tiny_view(),
    )
    # The pending plan should still be there for the next plan_turn call.
    assert ctrl.plan_turn(_tiny_view()) is staged

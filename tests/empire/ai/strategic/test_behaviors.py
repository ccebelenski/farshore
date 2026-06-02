"""Phase 14 — behaviors: registry coverage, revise no-crash, per-behavior scenarios."""

from __future__ import annotations

import pytest

from empire.ai.strategic.behaviors import behavior_for
from empire.ai.strategic.behaviors.base import Behavior, DefaultBehavior
from empire.ai.strategic.goals import CaptureCityGoal, Goal
from empire.ai.strategic.operational import Role, TaskForce, TaskForceState
from empire.ai.strategic.tactical import TacticalExecutor
from empire.contracts.surprise import (
    BlockedBy,
    EnemySighted,
    EscortLost,
    PathBlocked,
    Surprise,
    TargetLost,
    TerrainImpassable,
)
from empire.contracts.turn_plan import UnitMove
from empire.contracts.world_view import KnownEnemyUnit
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, GoalId, PlayerId, TaskForceId, UnitId
from empire.core.map import UnitSnapshot
from empire.core.unit import Army, Destroyer, Fighter, Transport, UnitKind
from tests.empire.ai.strategic.intel._world import (
    grid_map,
    place,
    player,
    reveal_all,
    world,
)


def _dummy_goal() -> Goal:
    return CaptureCityGoal(
        id=GoalId(1),
        priority=0.5,
        estimated_duration=3,
        target_city_id=CityId(1),
        target_coord=Coord(3, 1),
    )


def _force(target: Coord, unit_id: UnitId, role: Role) -> TaskForce:
    return TaskForce(
        id=TaskForceId(1),
        goal=_dummy_goal(),
        unit_ids=[unit_id],
        role_assignments={unit_id: role},
        target=target,
        state=TaskForceState.EN_ROUTE,
        created_turn=1,
    )


# --- coverage ----------------------------------------------------------------


@pytest.mark.parametrize("kind", list(UnitKind))
@pytest.mark.parametrize("role", list(Role))
def test_every_kind_role_pair_resolves_to_a_behavior(
    kind: UnitKind, role: Role
) -> None:
    assert isinstance(behavior_for(kind, role), Behavior)


# --- revise never crashes ----------------------------------------------------


def _all_surprises() -> list[Surprise]:
    snap = UnitSnapshot(
        unit_id=UnitId(99),
        kind=UnitKind.ARMY,
        owner_id=PlayerId(2),
        coord=Coord(2, 2),
        hits=1,
    )
    return [
        EnemySighted(enemy=KnownEnemyUnit(snapshot=snap, seen_at_turn=1), at=Coord(2, 2)),
        PathBlocked(blocked_at=Coord(1, 1), by=BlockedBy.ENEMY_UNIT),
        TargetLost(target_id=CityId(7)),
        EscortLost(escort_id=UnitId(3)),
        TerrainImpassable(at=Coord(1, 1)),
    ]


@pytest.mark.parametrize("surprise", _all_surprises(), ids=lambda s: type(s).__name__)
def test_revise_handles_every_surprise_without_crashing(surprise: Surprise) -> None:
    p1 = player(1)
    rmap = grid_map(["......" for _ in range(4)])
    army = place(rmap, Army(UnitId(1), p1, Coord(1, 1)))
    reveal_all(p1, rmap)
    view = world(rmap, p1)
    force = _force(Coord(4, 1), army.id, Role.ASSAULT)

    for kind in UnitKind:
        for role in Role:
            behavior = behavior_for(kind, role)
            move = behavior.revise(army, surprise, view, force)
            assert isinstance(move, UnitMove)
    # DefaultBehavior too.
    assert isinstance(DefaultBehavior().revise(army, surprise, view, None), UnitMove)


# --- one scenario per major behavior -----------------------------------------


def _first_step(move: UnitMove) -> Coord | None:
    return None if not move.path else Coord(move.path[0][0], move.path[0][1])


def test_army_assault_steps_toward_target_city() -> None:
    p1 = player(1)
    p2 = player(2, "Enemy")
    enemy_city = City(id=CityId(1), coord=Coord(3, 1), owner=p2)
    rmap = grid_map(["......" for _ in range(3)], cities={Coord(3, 1): enemy_city})
    army = place(rmap, Army(UnitId(1), p1, Coord(1, 1)))
    reveal_all(p1, rmap)
    force = _force(Coord(3, 1), army.id, Role.ASSAULT)

    move = behavior_for(UnitKind.ARMY, Role.ASSAULT).next_move(army, world(rmap, p1), force)

    step = _first_step(move)
    assert step is not None
    # Closes the distance to the city (any of the equal-cost diagonals is fine).
    assert step.chebyshev_to(Coord(3, 1)) < army.coord.chebyshev_to(Coord(3, 1))


def test_army_garrison_holds_when_adjacent_to_city() -> None:
    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(2, 1), owner=p1)
    rmap = grid_map(["......" for _ in range(3)], cities={Coord(2, 1): city})
    army = place(rmap, Army(UnitId(1), p1, Coord(2, 2)))  # adjacent
    reveal_all(p1, rmap)
    force = _force(Coord(2, 1), army.id, Role.GARRISON)

    move = behavior_for(UnitKind.ARMY, Role.GARRISON).next_move(army, world(rmap, p1), force)

    assert move.path == ()  # holds station


def test_idle_army_on_friendly_city_steps_off_to_avoid_disband() -> None:
    """The dominant production leak: an idle army that sentries on its home city
    is disbanded at turn-end (§5.4). idle_step must move it off the city."""
    from empire.ai.strategic.behaviors.base import idle_step

    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(2, 1), owner=p1)
    rmap = grid_map(["......" for _ in range(3)], cities={Coord(2, 1): city})
    army = place(rmap, Army(UnitId(1), p1, Coord(2, 1)))  # standing ON its city
    reveal_all(p1, rmap)

    move = idle_step(army, world(rmap, p1))

    step = _first_step(move)
    assert step is not None, "must step off, not sentry on the city"
    assert step != Coord(2, 1)
    # The destination is a real, enterable land cell, not another city.
    assert step.chebyshev_to(Coord(2, 1)) == 1


def test_idle_army_off_city_just_sentries() -> None:
    """Off a friendly city there is nothing to flee — idle_step holds."""
    from empire.ai.strategic.behaviors.base import idle_step

    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(2, 1), owner=p1)
    rmap = grid_map(["......" for _ in range(3)], cities={Coord(2, 1): city})
    army = place(rmap, Army(UnitId(1), p1, Coord(0, 0)))  # not on the city
    reveal_all(p1, rmap)

    assert idle_step(army, world(rmap, p1)).path == ()


def test_hunt_with_nothing_to_explore_steps_off_city() -> None:
    """HuntBehavior's 'nothing to explore' fallback used to sentry on the city
    (the leak). With everything revealed there is no frontier, so it must use
    idle_step and vacate the city."""
    from empire.ai.strategic.behaviors.base import HuntBehavior

    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(2, 1), owner=p1)
    rmap = grid_map(["......" for _ in range(3)], cities={Coord(2, 1): city})
    army = place(rmap, Army(UnitId(1), p1, Coord(2, 1)))
    reveal_all(p1, rmap)  # fully explored → nearest_frontier is None

    move = HuntBehavior().next_move(army, world(rmap, p1), None)

    assert move.path != (), "must vacate the city, not sentry on it"
    assert _first_step(move) != Coord(2, 1)


def test_transport_ferry_sails_toward_target() -> None:
    p1 = player(1)
    rmap = grid_map(["~~~~~~" for _ in range(3)])
    transport = place(rmap, Transport(UnitId(1), p1, Coord(0, 1)))
    reveal_all(p1, rmap)
    force = _force(Coord(5, 1), transport.id, Role.TRANSPORT)

    move = behavior_for(UnitKind.TRANSPORT, Role.TRANSPORT).next_move(
        transport, world(rmap, p1), force
    )

    step = _first_step(move)
    assert step is not None and step.x > transport.coord.x  # heading east


def test_fighter_low_on_fuel_returns_to_friendly_city() -> None:
    p1 = player(1)
    home = City(id=CityId(1), coord=Coord(0, 1), owner=p1)
    rmap = grid_map(["......" for _ in range(3)], cities={Coord(0, 1): home})
    fighter = place(rmap, Fighter(UnitId(1), p1, Coord(3, 1)))
    fighter.range = 1  # almost dry — can't reach a far target and get home
    reveal_all(p1, rmap)
    force = _force(Coord(5, 1), fighter.id, Role.ASSAULT)

    move = behavior_for(UnitKind.FIGHTER, Role.ASSAULT).next_move(
        fighter, world(rmap, p1), force
    )

    step = _first_step(move)
    assert step is not None and step.x < fighter.coord.x  # heading home (west)


def test_ship_escort_closes_on_its_transport() -> None:
    p1 = player(1)
    rmap = grid_map(["~~~~~~" for _ in range(3)])
    transport = place(rmap, Transport(UnitId(1), p1, Coord(5, 1)))
    destroyer = place(rmap, Destroyer(UnitId(2), p1, Coord(0, 1)))
    reveal_all(p1, rmap)
    force = TaskForce(
        id=TaskForceId(1),
        goal=_dummy_goal(),
        unit_ids=[transport.id, destroyer.id],
        role_assignments={transport.id: Role.TRANSPORT, destroyer.id: Role.ESCORT},
        target=Coord(5, 1),
        state=TaskForceState.EN_ROUTE,
        created_turn=1,
    )

    move = behavior_for(UnitKind.DESTROYER, Role.ESCORT).next_move(
        destroyer, world(rmap, p1), force
    )

    step = _first_step(move)
    assert step is not None and step.x > destroyer.coord.x  # moving toward the transport


# --- executor ----------------------------------------------------------------


def test_executor_emits_one_move_per_board_unit() -> None:
    p1 = player(1)
    rmap = grid_map(["......" for _ in range(3)])
    a1 = place(rmap, Army(UnitId(1), p1, Coord(1, 1)))
    a2 = place(rmap, Army(UnitId(2), p1, Coord(1, 2)))
    reveal_all(p1, rmap)
    force = TaskForce(
        id=TaskForceId(1),
        goal=_dummy_goal(),
        unit_ids=[a1.id],
        role_assignments={a1.id: Role.ASSAULT},
        target=Coord(4, 1),
        state=TaskForceState.EN_ROUTE,
        created_turn=1,
    )

    moves = TacticalExecutor().plan_moves([force], world(rmap, p1))

    assert {m.unit_id for m in moves} == {a1.id, a2.id}  # a2 idle → DefaultBehavior

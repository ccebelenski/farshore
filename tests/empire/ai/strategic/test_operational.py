"""Phase 13 — OperationalPlanner: force-matching, reaping, TF serialization."""

from __future__ import annotations

from empire.ai.strategic.goals import (
    CaptureCityGoal,
    ForceComposition,
    ProjectPowerGoal,
)
from empire.ai.strategic.memory import AIMemory
from empire.ai.strategic.operational import (
    OperationalPlanner,
    Role,
    TaskForce,
    TaskForceState,
)
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, GoalId, TaskForceId, UnitId
from empire.core.unit import Army, Transport, UnitKind
from tests.empire.ai.strategic.intel._world import grid_map, place, player, world


def _capture_goal(gid: int, city_id: int, coord: Coord) -> CaptureCityGoal:
    return CaptureCityGoal(
        id=GoalId(gid),
        priority=0.5,
        estimated_duration=3,
        target_city_id=CityId(city_id),
        target_coord=coord,
    )


# --- force matching ----------------------------------------------------------


def test_force_matching_pulls_exactly_the_required_units() -> None:
    p1 = player(1)
    rmap = grid_map(["~~~~~~", "......"])
    # Idle pool: 4 armies + 2 transports; the goal needs 3 armies + 1 transport.
    armies = [place(rmap, Army(UnitId(i), p1, Coord(i, 1))) for i in range(1, 5)]
    t1 = place(rmap, Transport(UnitId(10), p1, Coord(0, 0)))
    t2 = place(rmap, Transport(UnitId(11), p1, Coord(1, 0)))
    goal = ProjectPowerGoal(
        id=GoalId(1),
        priority=0.5,
        estimated_duration=10,
        target_region=(Coord(5, 0),),
        force_composition=ForceComposition.of({UnitKind.ARMY: 3, UnitKind.TRANSPORT: 1}),
        transport_count=1,
    )
    memory = AIMemory()

    plan = OperationalPlanner().plan([goal], world(rmap, p1), memory)

    assert len(plan.task_forces) == 1
    tf = plan.task_forces[0]
    # Exactly the 3 lowest-id armies + the 1 lowest-id transport.
    assert set(tf.unit_ids) == {a.id for a in armies[:3]} | {t1.id}
    assert t2.id not in tf.unit_ids
    assert armies[3].id not in tf.unit_ids
    assert tf.state is TaskForceState.EN_ROUTE  # fully crewed
    assert tf.role_assignments[t1.id] is Role.TRANSPORT
    assert tf.role_assignments[armies[0].id] is Role.ASSAULT


def test_short_force_is_forming_and_requests_production() -> None:
    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    rmap = grid_map(["C....."], cities={Coord(0, 0): city})
    goal = _capture_goal(1, 2, Coord(4, 0))  # needs 1 army; none idle
    memory = AIMemory()

    plan = OperationalPlanner().plan([goal], world(rmap, p1), memory)

    tf = plan.task_forces[0]
    assert tf.state is TaskForceState.FORMING
    assert tf.unit_ids == []
    # The idle city is told to build the missing Army.
    assert any(
        o.city_id == city.id and o.target is UnitKind.ARMY
        for o in plan.production_orders
    )


# --- continuity: existing forces keep their units ----------------------------


def test_existing_force_is_not_rebuilt_and_keeps_its_units() -> None:
    p1 = player(1)
    rmap = grid_map(["......"])
    army = place(rmap, Army(UnitId(1), p1, Coord(0, 0)))
    goal = _capture_goal(1, 2, Coord(4, 0))
    planner = OperationalPlanner()
    memory = AIMemory()

    first = planner.plan([goal], world(rmap, p1, turn=1), memory)
    tf_id = first.task_forces[0].id
    assert first.task_forces[0].unit_ids == [army.id]

    # Same goal next turn → same TF (not a duplicate), same unit.
    second = planner.plan([goal], world(rmap, p1, turn=2), memory)
    assert len(second.task_forces) == 1
    assert second.task_forces[0].id == tf_id


# --- reaper ------------------------------------------------------------------


def _terminal_tf(state: TaskForceState, terminal_since: int) -> TaskForce:
    return TaskForce(
        id=TaskForceId(99),
        goal=_capture_goal(50, 7, Coord(3, 3)),
        unit_ids=[],
        role_assignments={},
        target=Coord(3, 3),
        state=state,
        created_turn=0,
        terminal_since=terminal_since,
    )


def test_terminal_force_is_reaped_after_its_grace_turn() -> None:
    p1 = player(1)
    rmap = grid_map(["....."])
    planner = OperationalPlanner()

    # Became COMPLETE last turn (grace) → still present this turn...
    mem = AIMemory(task_forces=[_terminal_tf(TaskForceState.COMPLETE, terminal_since=5)])
    kept = planner.plan([], world(rmap, p1, turn=5), mem)
    assert len(kept.task_forces) == 1

    # ...and reaped the following turn.
    reaped = planner.plan([], world(rmap, p1, turn=6), mem)
    assert reaped.task_forces == []


def test_disbanded_force_is_also_reaped() -> None:
    p1 = player(1)
    rmap = grid_map(["....."])
    mem = AIMemory(
        task_forces=[_terminal_tf(TaskForceState.DISBANDED, terminal_since=3)]
    )
    out = OperationalPlanner().plan([], world(rmap, p1, turn=4), mem)
    assert out.task_forces == []


def test_force_completes_when_its_goal_is_achieved() -> None:
    p1 = player(1)
    captured = City(id=CityId(2), coord=Coord(4, 0), owner=p1)  # now ours
    rmap = grid_map(["....C"], cities={Coord(4, 0): captured})
    army = place(rmap, Army(UnitId(1), p1, Coord(3, 0)))
    goal = _capture_goal(1, 2, Coord(4, 0))
    existing = TaskForce(
        id=TaskForceId(1),
        goal=goal,
        unit_ids=[army.id],
        role_assignments={army.id: Role.ASSAULT},
        target=Coord(4, 0),
        state=TaskForceState.EN_ROUTE,
        created_turn=1,
    )
    mem = AIMemory(task_forces=[existing], next_task_force_id=2)

    out = OperationalPlanner().plan([goal], world(rmap, p1, turn=2), mem)

    assert out.task_forces[0].state is TaskForceState.COMPLETE
    assert out.task_forces[0].terminal_since == 2


# --- serialization -----------------------------------------------------------


def test_task_force_round_trips_through_json() -> None:
    goal = ProjectPowerGoal(
        id=GoalId(1),
        priority=0.5,
        estimated_duration=10,
        target_region=(Coord(9, 9), Coord(9, 8)),
        force_composition=ForceComposition.of({UnitKind.ARMY: 2, UnitKind.TRANSPORT: 1}),
        transport_count=1,
    )
    tf = TaskForce(
        id=TaskForceId(4),
        goal=goal,
        unit_ids=[UnitId(1), UnitId(2)],
        role_assignments={UnitId(1): Role.ASSAULT, UnitId(2): Role.TRANSPORT},
        target=Coord(9, 9),
        state=TaskForceState.EN_ROUTE,
        created_turn=3,
        rendezvous=Coord(5, 5),
        terminal_since=None,
    )

    import json

    restored = TaskForce.from_dict(json.loads(json.dumps(tf.to_dict())))
    assert restored == tf

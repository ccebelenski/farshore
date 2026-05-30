"""Phase 15 — StrategicAI controller wiring + AIMemory round-trip."""

from __future__ import annotations

import json

from empire.ai.strategic.ai import StrategicAI
from empire.ai.strategic.goals import CaptureCityGoal, ForceComposition
from empire.ai.strategic.memory import AIMemory
from empire.ai.strategic.operational import Role, TaskForce, TaskForceState
from empire.contracts.controller import AIController
from empire.contracts.surprise import BlockedBy, PathBlocked
from empire.contracts.turn_plan import TurnPlan
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, GoalId, TaskForceId, UnitId
from empire.core.unit import Army, UnitKind
from tests.empire.ai.strategic.intel._world import (
    grid_map,
    place,
    player,
    reveal_all,
    world,
)


def _scenario():
    p1 = player(1)
    own = City(id=CityId(1), coord=Coord(1, 1), owner=p1)
    neutral = City(id=CityId(2), coord=Coord(5, 1), owner=None)
    rmap = grid_map(
        ["......." for _ in range(3)],
        cities={Coord(1, 1): own, Coord(5, 1): neutral},
    )
    place(rmap, Army(UnitId(10), p1, Coord(2, 1)))
    reveal_all(p1, rmap)
    return rmap, p1


def test_strategic_ai_satisfies_the_controller_protocol() -> None:
    assert isinstance(StrategicAI(), AIController)
    assert StrategicAI().name() == "Strategic"


def test_plan_turn_produces_moves_and_production() -> None:
    rmap, p1 = _scenario()
    plan = StrategicAI().plan_turn(world(rmap, p1))

    assert isinstance(plan, TurnPlan)
    # The army has a reachable neutral to take → it gets a move.
    assert any(m.unit_id == UnitId(10) and m.path for m in plan.moves)
    # The idle own city is tasked to build something.
    assert any(o.city_id == CityId(1) for o in plan.production_orders)


def test_memory_persists_task_forces_across_turns() -> None:
    rmap, p1 = _scenario()
    ai = StrategicAI()

    ai.plan_turn(world(rmap, p1, turn=1))
    first = list(ai.memory.task_forces)
    assert first  # a capture force was assembled

    ai.plan_turn(world(rmap, p1, turn=2))
    # Same goal next turn → the same force survives (not duplicated).
    assert [tf.id for tf in ai.memory.task_forces] == [tf.id for tf in first]


def test_revise_move_is_total() -> None:
    rmap, p1 = _scenario()
    ai = StrategicAI()
    ai.plan_turn(world(rmap, p1))
    move = ai.revise_move(
        UnitId(10),
        PathBlocked(blocked_at=Coord(3, 1), by=BlockedBy.ENEMY_UNIT),
        world(rmap, p1),
    )
    assert move.unit_id == UnitId(10)


def test_ai_memory_round_trips_through_json() -> None:
    goal = CaptureCityGoal(
        id=GoalId(1),
        priority=0.5,
        estimated_duration=3,
        target_city_id=CityId(2),
        target_coord=Coord(5, 1),
    )
    tf = TaskForce(
        id=TaskForceId(1),
        goal=goal,
        unit_ids=[UnitId(10)],
        role_assignments={UnitId(10): Role.ASSAULT},
        target=Coord(5, 1),
        state=TaskForceState.EN_ROUTE,
        created_turn=2,
        rendezvous=Coord(1, 1),
    )
    memory = AIMemory(last_goals=(goal,), task_forces=[tf], next_task_force_id=2)

    restored = AIMemory.from_dict(json.loads(json.dumps(memory.to_dict())))

    assert restored.last_goals == memory.last_goals
    assert restored.task_forces == memory.task_forces
    assert restored.next_task_force_id == 2


def test_force_composition_survives_a_project_power_memory_round_trip() -> None:
    from empire.ai.strategic.goals import ProjectPowerGoal

    goal = ProjectPowerGoal(
        id=GoalId(3),
        priority=0.5,
        estimated_duration=10,
        target_region=(Coord(9, 9),),
        force_composition=ForceComposition.of({UnitKind.ARMY: 2, UnitKind.TRANSPORT: 1}),
        transport_count=1,
    )
    memory = AIMemory(last_goals=(goal,))
    restored = AIMemory.from_dict(json.loads(json.dumps(memory.to_dict())))
    assert restored.last_goals == memory.last_goals

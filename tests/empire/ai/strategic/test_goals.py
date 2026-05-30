"""Phase 12 — Goal construction, serialization, and progress signals."""

from __future__ import annotations

import pytest

from empire.ai.strategic.goals import (
    BuildForcesGoal,
    CaptureCityGoal,
    DefendCityGoal,
    DenyContinentGoal,
    ExploreAreaGoal,
    ForceComposition,
    Goal,
    ProjectPowerGoal,
    ResourceBudget,
    goal_from_dict,
)
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, GoalId, UnitId
from empire.core.unit import Army, UnitKind
from tests.empire.ai.strategic.intel._world import grid_map, place, player, reveal, world

_ARMY2 = ForceComposition.of({UnitKind.ARMY: 2})


def _all_goals() -> list[Goal]:
    return [
        CaptureCityGoal(
            id=GoalId(1),
            priority=0.7,
            estimated_duration=3,
            target_city_id=CityId(5),
            target_coord=Coord(4, 2),
        ),
        DefendCityGoal(
            id=GoalId(2),
            priority=0.9,
            estimated_duration=2,
            budget=ResourceBudget(city_ids=(CityId(1),), production_slots=2),
            target_city_id=CityId(1),
            target_coord=Coord(1, 1),
            garrison_size_needed=2,
        ),
        ExploreAreaGoal(
            id=GoalId(3),
            priority=0.2,
            estimated_duration=5,
            target_region=(Coord(0, 0), Coord(1, 0)),
        ),
        ProjectPowerGoal(
            id=GoalId(4),
            priority=0.5,
            estimated_duration=10,
            target_region=(Coord(9, 9),),
            force_composition=_ARMY2,
            transport_count=1,
        ),
        DenyContinentGoal(
            id=GoalId(5),
            priority=0.6,
            estimated_duration=6,
            target_region=(Coord(2, 2),),
            enemy_city_ids=(CityId(8),),
            neutral_city_ids=(CityId(9),),
        ),
        BuildForcesGoal(
            id=GoalId(6),
            priority=0.1,
            estimated_duration=5,
            force_composition_target=_ARMY2,
        ),
    ]


@pytest.mark.parametrize("goal", _all_goals(), ids=lambda g: g.kind.value)
def test_goal_roundtrips_through_dict(goal: Goal) -> None:
    assert goal_from_dict(goal.to_dict()) == goal


def test_rank_is_priority_over_duration() -> None:
    g = CaptureCityGoal(
        id=GoalId(1),
        priority=0.6,
        estimated_duration=3,
        target_city_id=CityId(1),
        target_coord=Coord(0, 0),
    )
    assert g.rank() == pytest.approx(0.2)


def test_force_composition_is_normalized_and_hashable() -> None:
    a = ForceComposition.of({UnitKind.TRANSPORT: 1, UnitKind.ARMY: 2, UnitKind.FIGHTER: 0})
    b = ForceComposition.of({UnitKind.ARMY: 2, UnitKind.TRANSPORT: 1})
    assert a == b
    assert hash(a) == hash(b)
    assert a.count(UnitKind.FIGHTER) == 0  # zero entries dropped
    assert a.total() == 3


def test_capture_progress_is_one_once_city_is_owned() -> None:
    p1 = player(1)
    owned = City(id=CityId(5), coord=Coord(4, 2), owner=p1)
    rmap = grid_map(["." * 6 for _ in range(4)], cities={Coord(4, 2): owned})
    reveal(p1, Coord(4, 2))
    goal = CaptureCityGoal(
        id=GoalId(1),
        priority=0.7,
        estimated_duration=3,
        target_city_id=CityId(5),
        target_coord=Coord(4, 2),
    )
    assert goal.progress_signal(world(rmap, p1)) == 1.0


def test_defend_progress_scales_with_nearby_garrison() -> None:
    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(2, 2), owner=p1)
    rmap = grid_map(["." * 6 for _ in range(6)], cities={Coord(2, 2): city})
    place(rmap, Army(UnitId(1), p1, Coord(2, 3)))  # adjacent
    reveal(p1, Coord(2, 2))
    goal = DefendCityGoal(
        id=GoalId(2),
        priority=0.9,
        estimated_duration=2,
        target_city_id=CityId(1),
        target_coord=Coord(2, 2),
        garrison_size_needed=2,
    )
    assert goal.progress_signal(world(rmap, p1)) == pytest.approx(0.5)  # 1 of 2

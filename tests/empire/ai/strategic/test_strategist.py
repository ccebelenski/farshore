"""Phase 12 — DeterministicStrategist canned scenarios.

Each scenario builds a `WorldView`, runs the real `IntelService` over it, then
asserts the strategist emits (or withholds) the expected goal type.
"""

from __future__ import annotations

from empire.ai.strategic.feasibility import FeasibilityOracle
from empire.ai.strategic.goals import CaptureCityGoal, DefendCityGoal, Goal
from empire.ai.strategic.intel.service import IntelService
from empire.ai.strategic.memory import AIMemory
from empire.ai.strategic.strategist import DeterministicStrategist
from empire.contracts.world_view import WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, UnitId
from empire.core.unit import Army
from tests.empire.ai.strategic.intel._world import (
    grid_map,
    place,
    player,
    reveal_all,
    world,
)


def _strategist() -> DeterministicStrategist:
    return DeterministicStrategist(FeasibilityOracle())


def _plan(view: WorldView) -> list[Goal]:
    intel = IntelService().assess(view)
    return _strategist().plan(intel, AIMemory(), view)


def test_threatened_defensible_city_yields_defend_goal() -> None:
    p1 = player(1)
    p2 = player(2, "Enemy")
    city = City(id=CityId(1), coord=Coord(3, 1), owner=p1)
    rmap = grid_map(["......." for _ in range(3)], cities={Coord(3, 1): city})
    place(rmap, Army(UnitId(10), p1, Coord(3, 2)))  # garrison nearby → defensible
    place(rmap, Army(UnitId(20), p2, Coord(5, 1)))  # enemy 2 cells from the city
    reveal_all(p1, rmap)

    goals = _plan(world(rmap, p1))

    assert any(
        isinstance(g, DefendCityGoal) and g.target_city_id == city.id for g in goals
    )


def test_surplus_and_reachable_neutral_yields_capture_goal() -> None:
    p1 = player(1)
    own = City(id=CityId(1), coord=Coord(1, 1), owner=p1)
    neutral = City(id=CityId(2), coord=Coord(4, 1), owner=None)
    rmap = grid_map(
        ["......." for _ in range(3)],
        cities={Coord(1, 1): own, Coord(4, 1): neutral},
    )
    place(rmap, Army(UnitId(10), p1, Coord(2, 1)))
    reveal_all(p1, rmap)

    goals = _plan(world(rmap, p1))

    assert any(
        isinstance(g, CaptureCityGoal) and g.target_city_id == neutral.id
        for g in goals
    )


def test_unreachable_neutral_is_filtered_by_the_oracle() -> None:
    """A visible neutral on a separate island is an Opportunity, but the
    reachability check stops the strategist from proposing its capture."""
    p1 = player(1)
    own = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    neutral = City(id=CityId(2), coord=Coord(4, 0), owner=None)
    # own-city, land, water, water, neutral-city — an Army can't cross the gap.
    rmap = grid_map(["..~~."], cities={Coord(0, 0): own, Coord(4, 0): neutral})
    place(rmap, Army(UnitId(10), p1, Coord(1, 0)))
    reveal_all(p1, rmap)

    goals = _plan(world(rmap, p1))

    assert not any(
        isinstance(g, CaptureCityGoal) and g.target_city_id == neutral.id
        for g in goals
    )


def test_plan_is_pure() -> None:
    p1 = player(1)
    own = City(id=CityId(1), coord=Coord(1, 1), owner=p1)
    neutral = City(id=CityId(2), coord=Coord(4, 1), owner=None)
    rmap = grid_map(
        ["......." for _ in range(3)],
        cities={Coord(1, 1): own, Coord(4, 1): neutral},
    )
    place(rmap, Army(UnitId(10), p1, Coord(2, 1)))
    reveal_all(p1, rmap)
    view = world(rmap, p1)
    intel = IntelService().assess(view)
    strat = _strategist()

    assert strat.plan(intel, AIMemory(), view) == strat.plan(intel, AIMemory(), view)

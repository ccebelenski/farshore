"""`StrategicAI`: the layered `AIController` (see `planning/03-ai-design.md` §3).

Each turn it runs the four-stage pipeline against the live `WorldView`:
intel assessment → goal selection → task-force assembly → per-unit movement,
bundling the result into a `TurnPlan`. Cross-turn state (the strategist's
goals and the operational layer's task forces) persists in an `AIMemory` held
for the lifetime of this controller — one instance per player.

`revise_move` routes a mid-turn surprise to the unit's behavior for a single
corrective step, against the frozen task-force picture from this turn's plan
(the reactivity boundary in §1).
"""

from __future__ import annotations

from empire.ai.strategic.feasibility import FeasibilityOracle
from empire.ai.strategic.intel.service import IntelService
from empire.ai.strategic.memory import AIMemory
from empire.ai.strategic.operational import OperationalPlanner, TaskForce
from empire.ai.strategic.strategist import DeterministicStrategist, Strategist
from empire.ai.strategic.tactical import TacticalExecutor
from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.identity import UnitId
from empire.core.unit import UnitKind


class StrategicAI:
    """The deterministic four-layer strategic controller."""

    def __init__(
        self,
        strategist: Strategist | None = None,
        default_build: UnitKind = UnitKind.ARMY,
    ) -> None:
        self._intel = IntelService()
        self._strategist: Strategist = strategist or DeterministicStrategist(
            FeasibilityOracle()
        )
        self._operational = OperationalPlanner()
        self._tactical = TacticalExecutor()
        self._memory = AIMemory()
        self._default_build = default_build
        self._forces: list[TaskForce] = []

    @property
    def memory(self) -> AIMemory:
        return self._memory

    def name(self) -> str:
        return "Strategic"

    def plan_turn(self, view: WorldView) -> TurnPlan:
        intel = self._intel.assess(view)
        goals = self._strategist.plan(intel, self._memory, view)
        operational = self._operational.plan(goals, view, self._memory)
        moves = self._tactical.plan_moves(operational.task_forces, view)
        self._forces = operational.task_forces

        return TurnPlan(
            production_orders=self._production_orders(operational.production_orders, view),
            moves=tuple(m for m in moves if m.path),
        )

    def revise_move(
        self, unit_id: UnitId, surprise: Surprise, view: WorldView
    ) -> UnitMove:
        unit = next((u for u in view.own_units if u.id == unit_id), None)
        if unit is None:
            return UnitMove(unit_id=unit_id)
        return self._tactical.revise_move(unit, surprise, view, self._forces)

    def _production_orders(
        self, requested: list[ProductionOrder], view: WorldView
    ) -> tuple[ProductionOrder, ...]:
        """The operational layer's requests, plus a default build for any idle
        city it didn't already task — so no city sits silent."""
        ordered = {o.city_id for o in requested}
        orders = list(requested)
        for city in view.own_cities:
            if city.id not in ordered and city.production.building is None:
                orders.append(
                    ProductionOrder(city_id=city.id, target=self._default_build)
                )
        return tuple(orders)

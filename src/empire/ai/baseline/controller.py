"""`BaselineAI`: the per-unit greedy `AIController`.

Satisfies `empire.contracts.controller.AIController` structurally. For each
own unit, dispatches to `BaselineTactical.decide` and bundles the results
into a `TurnPlan`. For each idle owned city, issues a production order so
captured cities don't sit silent.

Mid-turn revisions (`revise_move`) re-run the unit's decision against the
live `WorldView` — there's no behavior state to preserve.
"""

from __future__ import annotations

from empire.ai.baseline.tactical import BaselineTactical
from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.identity import UnitId
from empire.core.unit import UnitKind


class BaselineAI:
    """Greedy per-unit baseline. Dispatches to `BaselineTactical` per unit."""

    def __init__(self, default_build: UnitKind = UnitKind.ARMY) -> None:
        self._tactical: BaselineTactical = BaselineTactical()
        self._default_build: UnitKind = default_build

    def name(self) -> str:
        return "Baseline"

    def plan_turn(self, view: WorldView) -> TurnPlan:
        moves: list[UnitMove] = []
        for unit in view.own_units:
            move = self._tactical.decide(unit, view)
            if move.path:
                moves.append(move)

        production_orders: list[ProductionOrder] = []
        for city in view.own_cities:
            if city.production.building is None:
                production_orders.append(
                    ProductionOrder(city_id=city.id, target=self._default_build),
                )

        return TurnPlan(
            production_orders=tuple(production_orders),
            moves=tuple(moves),
        )

    def revise_move(
        self,
        unit_id: UnitId,
        surprise: Surprise,
        view: WorldView,
    ) -> UnitMove:
        del surprise  # No behavior state — just replan against live view.
        for unit in view.own_units:
            if unit.id == unit_id:
                return self._tactical.decide(unit, view)
        return UnitMove(unit_id=unit_id)

"""`PlanFollower`: the controller that executes one `Plan`.

It runs in two places — inside playouts (driving the searcher's side of a
simulated future) and as the real-turn executor for the plan the search
commits — so the engine sees exactly the behavior the search scored.

Assignment is recomputed from scratch every turn, deterministically:
objectives take their `strength` nearest armies in plan-priority order
(ties break on unit registry order), the rest follow the surplus policy.
Recomputing — rather than remembering — is what keeps an objective-level
plan executable over a playout horizon where units die and new ones appear.

The follower is immutable: one instance per plan. `SearchAI` builds a fresh
follower for each candidate playout and for each committed turn.
"""

from __future__ import annotations

from empire.ai.search.plan import Objective, Plan, Role, SurplusPolicy
from empire.ai.strategic.behaviors.base import idle_step
from empire.ai.vision import frontier_cells
from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.unit import Unit, UnitKind
from empire.pathfinding.cost import ARMY as ARMY_COST_PROFILE
from empire.pathfinding.distance_field import DistanceField, PassabilityGrid


class PlanFollower:
    """Executes one frozen `Plan`; satisfies `AIController` structurally."""

    def __init__(self, plan: Plan) -> None:
        self._plan: Plan = plan

    @property
    def plan(self) -> Plan:
        return self._plan

    def name(self) -> str:
        return "PlanFollower"

    # ---- AIController ------------------------------------------------------

    def plan_turn(self, view: WorldView) -> TurnPlan:
        moves = [m for m in self._decide_all(view).values() if m.path]
        production = tuple(
            ProductionOrder(city_id=c.id, target=self._plan.production)
            for c in view.own_cities
            if c.production.building is None
        )
        return TurnPlan(production_orders=production, moves=tuple(moves))

    def revise_move(
        self, unit_id: UnitId, surprise: Surprise, view: WorldView
    ) -> UnitMove:
        del surprise  # plan-driven: re-decide against the live view
        return self._decide_all(view).get(unit_id, UnitMove(unit_id=unit_id))

    # ---- decision core -----------------------------------------------------

    def _decide_all(self, view: WorldView) -> dict[UnitId, UnitMove]:
        """Every army's move this turn, by deterministic re-assignment."""
        armies = [
            u
            for u in view.own_units
            if u.kind is UnitKind.ARMY and u.carried_by is None
        ]
        if not armies:
            return {}
        grid = PassabilityGrid(
            view.real_map(), ARMY_COST_PROFILE, view.own_player.view
        )
        fields = {u.id: DistanceField(u.coord, grid) for u in armies}

        assignment = self._assign(armies, fields)
        surplus_scouts = self._plan.surplus is SurplusPolicy.SCOUT and any(
            u.id not in assignment for u in armies
        )
        frontier = frontier_cells(view) if surplus_scouts else frozenset[Coord]()
        moves: dict[UnitId, UnitMove] = {}
        for unit in armies:
            objective = assignment.get(unit.id)
            if objective is not None:
                moves[unit.id] = self._objective_move(
                    unit, objective, fields[unit.id], view
                )
            else:
                moves[unit.id] = self._surplus_move(
                    unit, fields[unit.id], frontier, view
                )
        return moves

    def _assign(
        self,
        armies: list[Unit],
        fields: dict[UnitId, DistanceField],
    ) -> dict[UnitId, Objective]:
        """Objectives pick their `strength` nearest reachable armies, in plan
        order. Ties break on the armies' registry order (stable, serialized)."""
        assignment: dict[UnitId, Objective] = {}
        unassigned: list[Unit] = list(armies)
        for objective in self._plan.objectives:
            if objective.strength <= 0:
                continue
            ranked = sorted(
                (
                    (steps, index, unit)
                    for index, unit in enumerate(unassigned)
                    if (steps := fields[unit.id].steps_to(objective.target))
                    is not None
                ),
                key=lambda entry: (entry[0], entry[1]),
            )
            chosen = [unit for _, _, unit in ranked[: objective.strength]]
            for unit in chosen:
                assignment[unit.id] = objective
            unassigned = [u for u in unassigned if u.id not in assignment]
        return assignment

    def _objective_move(
        self,
        unit: Unit,
        objective: Objective,
        field: DistanceField,
        view: WorldView,
    ) -> UnitMove:
        if objective.role is Role.DEFEND and unit.coord.chebyshev_to(
            objective.target
        ) <= 1:
            # Garrison holds at the city's edge — never on it (§5.4).
            return idle_step(unit, view)
        return self._advance(unit, objective.target, field, view)

    def _surplus_move(
        self,
        unit: Unit,
        field: DistanceField,
        frontier: frozenset[Coord],
        view: WorldView,
    ) -> UnitMove:
        if self._plan.surplus is SurplusPolicy.RESERVE:
            return idle_step(unit, view)
        # SCOUT: push toward the nearest reachable frontier cell.
        target = self._nearest_frontier(field, frontier)
        if target is None:
            return idle_step(unit, view)
        return self._advance(unit, target, field, view)

    def _advance(
        self, unit: Unit, target: Coord, field: DistanceField, view: WorldView
    ) -> UnitMove:
        """Step along the unit's field toward `target`; §5.4-safe fallback."""
        cells = field.path_to(target)
        if cells is None or len(cells) < 2:
            return idle_step(unit, view)
        budget = unit.moves_this_turn()
        steps = cells[1 : 1 + budget]
        return UnitMove(unit_id=unit.id, path=tuple((c.x, c.y) for c in steps))

    def _nearest_frontier(
        self, field: DistanceField, frontier: frozenset[Coord]
    ) -> Coord | None:
        """The reachable frontier cell fewest steps from the unit; ties break
        on (y, x) for determinism."""
        best: tuple[int, int, int] | None = None
        best_cell: Coord | None = None
        for cell in frontier:
            steps = field.steps_to(cell)
            if steps is None or steps == 0:
                continue
            key = (steps, cell.y, cell.x)
            if best is None or key < best:
                best, best_cell = key, cell
        return best_cell

"""`OperationalPlanner` + `TaskForce`: turn the strategist's goals into
concrete groups of units with assigned roles (see `planning/03-ai-design.md`
§3.3).

A `TaskForce` is mutable operational state that lives across turns in
`AIMemory` — it forms, moves, engages, and is reaped once terminal. The
planner each turn: reaps terminal forces past their grace turn, updates
surviving forces' state, and assembles new forces for goals that don't have
one yet, pulling from the idle unit pool and requesting production for any
shortfall.

Note (deviation from §3.3's `units: list[Unit]`): a `TaskForce` stores unit
*ids*, not live `Unit` refs — cleaner to serialize and avoids holding stale
references across turns. The tactical executor (Phase 14) resolves ids against
the live map.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from empire.ai.strategic.goals import (
    BuildForcesGoal,
    CaptureCityGoal,
    DefendCityGoal,
    DenyContinentGoal,
    ExploreAreaGoal,
    ForceComposition,
    Goal,
    ProjectPowerGoal,
    goal_from_dict,
)
from empire.contracts.turn_plan import ProductionOrder
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import TaskForceId, UnitId
from empire.core.unit import UnitKind

if TYPE_CHECKING:
    from empire.ai.strategic.memory import AIMemory

# Terminal task forces linger one turn (debug/telemetry grace) before reaping.
_REAP_GRACE = 1
_ARMY = UnitKind.ARMY
_TRANSPORT = UnitKind.TRANSPORT
_WARSHIPS = frozenset(
    {UnitKind.PATROL, UnitKind.DESTROYER, UnitKind.SUBMARINE, UnitKind.BATTLESHIP}
)


class Role(Enum):
    """A unit's job within its task force."""

    ASSAULT = "assault"
    SCOUT = "scout"
    ESCORT = "escort"
    TRANSPORT = "transport"
    GARRISON = "garrison"


class TaskForceState(Enum):
    FORMING = "forming"  # still gathering / building its units
    EN_ROUTE = "en_route"  # full strength, moving to the objective
    ENGAGED = "engaged"  # in contact at the objective
    COMPLETE = "complete"  # goal achieved
    DISBANDED = "disbanded"  # goal abandoned / force destroyed


@dataclass
class TaskForce:
    """A group of units pursuing one goal. Mutable; evolves across turns."""

    id: TaskForceId
    goal: Goal
    unit_ids: list[UnitId]
    role_assignments: dict[UnitId, Role]
    target: Coord
    state: TaskForceState
    created_turn: int
    rendezvous: Coord | None = None
    terminal_since: int | None = None

    def is_terminal(self) -> bool:
        return self.state in (TaskForceState.COMPLETE, TaskForceState.DISBANDED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": int(self.id),
            "goal": self.goal.to_dict(),
            "unit_ids": [int(u) for u in self.unit_ids],
            "role_assignments": [
                [int(u), r.value] for u, r in self.role_assignments.items()
            ],
            "target": [self.target.x, self.target.y],
            "state": self.state.value,
            "created_turn": self.created_turn,
            "rendezvous": (
                None if self.rendezvous is None else [self.rendezvous.x, self.rendezvous.y]
            ),
            "terminal_since": self.terminal_since,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskForce:
        rv = data.get("rendezvous")
        return cls(
            id=TaskForceId(int(data["id"])),
            goal=goal_from_dict(data["goal"]),
            unit_ids=[UnitId(int(u)) for u in data["unit_ids"]],
            role_assignments={
                UnitId(int(u)): Role(r) for u, r in data["role_assignments"]
            },
            target=Coord(int(data["target"][0]), int(data["target"][1])),
            state=TaskForceState(data["state"]),
            created_turn=int(data["created_turn"]),
            rendezvous=None if rv is None else Coord(int(rv[0]), int(rv[1])),
            terminal_since=data.get("terminal_since"),
        )


@dataclass
class OperationalPlan:
    """The planner's output for a turn: the live task forces plus any
    production the forming ones need."""

    task_forces: list[TaskForce]
    production_orders: list[ProductionOrder]


class OperationalPlanner:
    """Assembles and maintains `TaskForce`s for the strategist's goals."""

    def plan(
        self, goals: list[Goal], view: WorldView, memory: AIMemory
    ) -> OperationalPlan:
        turn = view.turn
        live_unit_ids = {u.id for u in view.own_units}

        survivors = self._reap_and_update(memory.task_forces, view, turn, live_unit_ids)
        claimed = {uid for tf in survivors for uid in tf.unit_ids}
        served = {tf.goal.id for tf in survivors if not tf.is_terminal()}
        idle = self._idle_pool(view, claimed)

        for goal in goals:
            if goal.id in served:
                continue
            survivors.append(self._assemble(goal, idle, view, turn, memory))

        memory.task_forces = survivors
        orders = self._production_requests(survivors, view)
        return OperationalPlan(task_forces=survivors, production_orders=orders)

    # -- maintenance ----------------------------------------------------------

    def _reap_and_update(
        self,
        existing: list[TaskForce],
        view: WorldView,
        turn: int,
        live_unit_ids: set[UnitId],
    ) -> list[TaskForce]:
        survivors: list[TaskForce] = []
        for tf in existing:
            if tf.is_terminal():
                if (
                    tf.terminal_since is not None
                    and turn - tf.terminal_since >= _REAP_GRACE
                ):
                    continue  # reaped after its grace turn
                survivors.append(tf)
                continue
            # Drop units that died since last turn.
            tf.unit_ids = [u for u in tf.unit_ids if u in live_unit_ids]
            tf.role_assignments = {
                u: r for u, r in tf.role_assignments.items() if u in live_unit_ids
            }
            if tf.goal.progress_signal(view) >= 1.0:
                tf.state = TaskForceState.COMPLETE
                tf.terminal_since = turn
            elif not tf.unit_ids:
                tf.state = TaskForceState.DISBANDED
                tf.terminal_since = turn
            survivors.append(tf)
        return survivors

    def _idle_pool(
        self, view: WorldView, claimed: set[UnitId]
    ) -> dict[UnitKind, list[UnitId]]:
        pool: dict[UnitKind, list[UnitId]] = {}
        for unit in sorted(view.own_units, key=lambda u: int(u.id)):
            if unit.id in claimed or unit.is_aboard():
                continue
            pool.setdefault(unit.kind, []).append(unit.id)
        return pool

    # -- assembly -------------------------------------------------------------

    def _assemble(
        self,
        goal: Goal,
        idle: dict[UnitKind, list[UnitId]],
        view: WorldView,
        turn: int,
        memory: AIMemory,
    ) -> TaskForce:
        composition = _required_composition(goal)
        unit_ids: list[UnitId] = []
        roles: dict[UnitId, Role] = {}
        short = False
        for kind, needed in composition.entries:
            pool = idle.get(kind, [])
            taken = pool[:needed]
            del pool[:needed]  # consume so two forces don't claim the same unit
            if len(taken) < needed:
                short = True
            for uid in taken:
                unit_ids.append(uid)
                roles[uid] = _role_for(goal, kind)

        full = not short
        target = _goal_target(goal, view)
        tf = TaskForce(
            id=TaskForceId(memory.next_task_force_id),
            goal=goal,
            unit_ids=unit_ids,
            role_assignments=roles,
            target=target,
            state=TaskForceState.EN_ROUTE if full else TaskForceState.FORMING,
            created_turn=turn,
            rendezvous=_rendezvous(target, view),
        )
        memory.next_task_force_id += 1
        return tf

    def _production_requests(
        self, task_forces: list[TaskForce], view: WorldView
    ) -> list[ProductionOrder]:
        """Ask idle cities to build the kinds that forming forces still lack."""
        free_cities = sorted(
            (c for c in view.own_cities if c.production.building is None),
            key=lambda c: int(c.id),
        )
        wanted: list[UnitKind] = []
        for tf in task_forces:
            if tf.state is not TaskForceState.FORMING:
                continue
            for kind, needed in _required_composition(tf.goal).entries:
                owned = sum(1 for uid in tf.unit_ids if _unit_kind(view, uid) is kind)
                wanted.extend([kind] * max(0, needed - owned))

        return [
            ProductionOrder(city_id=city.id, target=kind)
            for city, kind in zip(free_cities, wanted, strict=False)
        ]


# -- goal → operational requirements ------------------------------------------


def _required_composition(goal: Goal) -> ForceComposition:
    if isinstance(goal, CaptureCityGoal):
        return ForceComposition.of({_ARMY: 1})
    if isinstance(goal, DefendCityGoal):
        return ForceComposition.of({_ARMY: max(1, goal.garrison_size_needed)})
    if isinstance(goal, ProjectPowerGoal):
        counts = dict(goal.force_composition.entries)
        counts[_TRANSPORT] = max(counts.get(_TRANSPORT, 0), goal.transport_count)
        return ForceComposition.of(counts)
    if isinstance(goal, DenyContinentGoal):
        return ForceComposition.of({_ARMY: 2})
    if isinstance(goal, ExploreAreaGoal):
        return ForceComposition.of({_ARMY: 1})
    if isinstance(goal, BuildForcesGoal):
        return goal.force_composition_target
    return ForceComposition()


def _role_for(goal: Goal, kind: UnitKind) -> Role:
    if isinstance(goal, DefendCityGoal):
        return Role.GARRISON
    if isinstance(goal, ExploreAreaGoal):
        return Role.SCOUT
    if kind is _TRANSPORT:
        return Role.TRANSPORT
    if kind in _WARSHIPS:
        return Role.ESCORT
    return Role.ASSAULT


def _goal_target(goal: Goal, view: WorldView) -> Coord:
    if isinstance(goal, (CaptureCityGoal, DefendCityGoal)):
        return goal.target_coord
    if (
        isinstance(goal, (ExploreAreaGoal, ProjectPowerGoal, DenyContinentGoal))
        and goal.target_region
    ):
        return goal.target_region[0]
    # BuildForcesGoal and empty-region fallbacks: rally at our first city.
    cities = sorted(view.own_cities, key=lambda c: int(c.id))
    return cities[0].coord if cities else Coord(0, 0)


def _rendezvous(target: Coord, view: WorldView) -> Coord | None:
    """A safe gathering point: the friendly city nearest the objective."""
    cities = view.own_cities
    if not cities:
        return None
    return min(cities, key=lambda c: c.coord.chebyshev_to(target)).coord


def _unit_kind(view: WorldView, unit_id: UnitId) -> UnitKind | None:
    for unit in view.own_units:
        if unit.id == unit_id:
            return unit.kind
    return None

"""`DoctrineCompiler`: the seam that turns taskings into executor work.

Each `TaskForce` compiles into a scoped planning problem — THESE units,
THIS objective — executed by the untouched `PlanFollower`. The scoping
mechanism is a member-filtered `WorldView`: the follower (and the naval/air
planners it drives) discover their units through `view.own_units`, so a
view that reports only the task force's members makes every existing
movement rule — assignment, danger-aware routing, mass-before-storm —
operate strictly within TF boundaries. The general owns what/with-whom;
the staff's tactics survive untouched inside each tasking.

MECHANISM CHOICE (and the roads not taken):

- CHOSEN — one `PlanFollower` per task force, each fed a single-objective
  `Plan` and a `WorldView` subclass that filters `own_units` to the TF's
  members. Zero changes to `empire.ai.search`; the follower cannot assign a
  unit it cannot see, so a TF's units pursue that TF's objective and no
  other. Merging is trivial because membership is disjoint (a registry
  invariant).
- REJECTED — one combined multi-objective `Plan` for a single follower:
  its assignment is nearest-first and membership-blind, so units would
  cross task-force lines; that is precisely what tasking forbids.
- REJECTED — a members filter hooked into `PlanFollower` itself: touches
  the executor that the search tests pin down, to reproduce a scoping the
  view interface already expresses.
- REJECTED — scoping constraints in `CandidateGenerator`: the generator
  proposes plans, it does not bind units — wrong layer for membership.

Verb compilation (minimal viable — the first handshake steers land forces):
CAPTURE -> ASSAULT, DEFEND -> DEFEND, STAGE -> DEFEND at the marshaling
coordinate (the defend movement rule IS "advance, then hold beside the
target, never on it"). SCOUT/PATROL compile to the surplus-scout posture
(members range to the fog frontier); compass-direction bias and the
amphibious CAPTURE->INVADE upgrade are later steps, not this seam.

The compiled result covers TASKED MOVEMENT only: production belongs to
`BuildDirective`s and the unassigned pool stays the executor's business —
this seam never emits production orders, and units outside every task
force are simply absent from its output.
"""

from __future__ import annotations

from dataclasses import dataclass

from empire.ai.general.registry import TaskForceRegistry
from empire.ai.search.follower import PlanFollower
from empire.ai.search.plan import Objective as PlanObjective
from empire.ai.search.plan import Plan, Role, SurplusPolicy
from empire.contracts.doctrine import TaskForce, TaskForceId, Verb
from empire.contracts.turn_plan import TurnPlan, UnitMove, UnloadOrder
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.unit import Unit

_ROLE_OF_VERB: dict[Verb, Role] = {
    Verb.CAPTURE: Role.ASSAULT,
    Verb.DEFEND: Role.DEFEND,
    Verb.STAGE: Role.DEFEND,  # marshal: close on the coordinate and hold beside it
}


class _TaskForceView(WorldView):
    """A live view scoped to one task force: identical world, but
    `own_units` reports only the members — the follower plans with the
    task force's hands and nobody else's."""

    def __init__(self, base: WorldView, members: frozenset[UnitId]) -> None:
        super().__init__(
            real_map=base.real_map(),
            player=base.own_player,
            turn=base.turn,
            rules=base.rules,
        )
        self._members = members

    @property
    def own_units(self) -> list[Unit]:
        return [u for u in super().own_units if u.id in self._members]


@dataclass(frozen=True, slots=True)
class TaskForceScope:
    """One task force's compiled planning scope: these units, this plan."""

    tf_id: TaskForceId
    members: frozenset[UnitId]
    plan: Plan


class DoctrineCompiler:
    """Compiles a `TaskForceRegistry` into per-task-force executor scopes."""

    def compile(self, registry: TaskForceRegistry) -> tuple[TaskForceScope, ...]:
        """One scope per standing task force, in formation order."""
        return tuple(
            TaskForceScope(tf_id=tf.tf_id, members=tf.members, plan=self._plan_for(tf))
            for tf in registry.forces
        )

    def plan_moves(self, registry: TaskForceRegistry, view: WorldView) -> TurnPlan:
        """One planning step for every task force: run each scope's follower
        against the member-filtered view and merge the movement. Deterministic
        (formation order x the follower's own determinism); membership is
        disjoint, so the merge cannot conflict."""
        moves: list[UnitMove] = []
        unloads: list[UnloadOrder] = []
        for scope in self.compile(registry):
            scoped = _TaskForceView(view, scope.members)
            turn_plan = PlanFollower(scope.plan).plan_turn(scoped)
            moves.extend(m for m in turn_plan.moves if m.unit_id in scope.members)
            unloads.extend(u for u in turn_plan.unloads if u.cargo_id in scope.members)
        return TurnPlan(moves=tuple(moves), unloads=tuple(unloads))

    # ---- verb -> plan ----------------------------------------------------------

    def _plan_for(self, tf: TaskForce) -> Plan:
        """Render one task force's objective as a `Plan` the follower executes.

        Surplus is RESERVE for targeted verbs — a member the objective cannot
        use (e.g. a warship in a land assault) holds rather than wandering off
        task. Strength is the full membership: a task force commits everything
        it has to its one job."""
        verb = tf.objective.verb
        target = tf.objective.target
        if verb in _ROLE_OF_VERB and isinstance(target, Coord):
            objective = PlanObjective(target, _ROLE_OF_VERB[verb], len(tf.members))
            return Plan(objectives=(objective,), surplus=SurplusPolicy.RESERVE)
        # SCOUT/PATROL (either target form) range the fog frontier via the
        # surplus policy; a coordinate verb with a compass target is validator
        # garbage — stay total and hold rather than guess.
        if verb in (Verb.SCOUT, Verb.PATROL):
            return Plan(objectives=(), surplus=SurplusPolicy.SCOUT)
        return Plan(objectives=(), surplus=SurplusPolicy.RESERVE)

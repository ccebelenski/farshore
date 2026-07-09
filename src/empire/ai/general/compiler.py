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

Verb compilation: CAPTURE -> ASSAULT when every land member can walk to the
target, CAPTURE -> INVADE when any cannot (or the force is all-lift/afloat) —
the follower's naval machinery then stages, ferries, and lands the fist.
The landmass question is asked fog-honestly, on the same fog-masked army
grid the follower routes with (seen terrain is authoritative, unseen cells
optimistically walkable), never on real-map truth the player hasn't earned.
INVADE is chosen whenever ANY land member lacks a walking route because the
executor treats it as a superset of ASSAULT: land-reachable members storm
overland exactly as they would under ASSAULT (`_STORM_ROLES`), and only the
cut-off remainder rides the transports — so a mid-operation force with a
landed first wave keeps both its beachhead assault and its follow-on waves.
DEFEND -> DEFEND, STAGE -> DEFEND at the marshaling coordinate (the defend
movement rule IS "advance, then hold beside the target, never on it").
SCOUT/PATROL compile to the surplus-scout posture (members range to the fog
frontier); compass-direction bias is a later step, not this seam.

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
from empire.core.unit import Unit, UnitKind
from empire.pathfinding.cost import ARMY as ARMY_COST_PROFILE
from empire.pathfinding.distance_field import DistanceField, PassabilityGrid

_ROLE_OF_VERB: dict[Verb, Role] = {
    Verb.CAPTURE: Role.ASSAULT,  # upgraded to INVADE when the target is overseas
    Verb.DEFEND: Role.DEFEND,
    Verb.STAGE: Role.DEFEND,  # marshal: close on the coordinate and hold beside it
}


# --- shared CAPTURE geometry (one source of truth for role + the briefing note) --
# The ASSAULT/INVADE decision and the "stranded amphibious" briefing hint MUST
# agree, so both are computed from these helpers over the SAME fog-honest army
# grid the follower routes with — the hint can never contradict what the
# executor actually does.


def _tf_members(tf: TaskForce, view: WorldView) -> list[Unit]:
    return [u for u in view.own_units if u.id in tf.members]


def _ashore_armies(members: list[Unit]) -> list[Unit]:
    """Landed armies (not aboard) — the troops a CAPTURE must walk onto the city."""
    return [u for u in members if u.kind is UnitKind.ARMY and u.carried_by is None]


def _has_lift(members: list[Unit]) -> bool:
    """A transport in the force, or an army already aboard one: the force can
    ferry itself across water."""
    return any(
        u.kind is UnitKind.TRANSPORT or u.carried_by is not None for u in members
    )


def _all_can_walk(units: list[Unit], target: Coord, view: WorldView) -> bool:
    """Every unit can reach `target` over the FOG-HONEST army grid — seen terrain
    (current or remembered) authoritative, unseen cells optimistically walkable —
    never real-map truth the player has not earned."""
    reach = DistanceField(
        target,
        PassabilityGrid(view.real_map(), ARMY_COST_PROFILE, view.own_player.view),
    )
    return all(reach.steps_to(u.coord) is not None for u in units)


def capture_is_stranded(tf: TaskForce, view: WorldView) -> bool:
    """True when a CAPTURE tasking is a *stranded amphibious assault*: its target
    is across water (fog-honestly — some member army cannot walk there over SEEN
    terrain) AND the force carries no transport to lift them.

    Shares the compiler's own reachability + lift tests, so it can never disagree
    with the ASSAULT/INVADE decision the executor acts on. Fog-honest by
    construction: unseen cells are optimistically walkable, so it fires only on
    water the player has actually seen — it reveals no map truth the general has
    not earned, only restates what its own board already shows."""
    objective = tf.objective
    if objective.verb is not Verb.CAPTURE or not isinstance(objective.target, Coord):
        return False
    members = _tf_members(tf, view)
    if _has_lift(members):
        return False  # can ferry itself — not stranded
    ashore = _ashore_armies(members)
    return bool(ashore) and not _all_can_walk(ashore, objective.target, view)


class TaskForceView(WorldView):
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

    def compile(
        self, registry: TaskForceRegistry, view: WorldView
    ) -> tuple[TaskForceScope, ...]:
        """One scope per standing task force, in formation order. The view is
        read (fog-honestly) only to settle CAPTURE's land-vs-amphibious shape;
        no scope ever sees another force's units through it."""
        return tuple(
            TaskForceScope(
                tf_id=tf.tf_id, members=tf.members, plan=self._plan_for(tf, view)
            )
            for tf in registry.forces
        )

    def plan_moves(self, registry: TaskForceRegistry, view: WorldView) -> TurnPlan:
        """One planning step for every task force: run each scope's follower
        against the member-filtered view and merge the movement. Deterministic
        (formation order x the follower's own determinism); membership is
        disjoint, so the merge cannot conflict."""
        moves: list[UnitMove] = []
        unloads: list[UnloadOrder] = []
        for scope in self.compile(registry, view):
            scoped = TaskForceView(view, scope.members)
            turn_plan = PlanFollower(scope.plan).plan_turn(scoped)
            moves.extend(m for m in turn_plan.moves if m.unit_id in scope.members)
            unloads.extend(u for u in turn_plan.unloads if u.cargo_id in scope.members)
        return TurnPlan(moves=tuple(moves), unloads=tuple(unloads))

    # ---- verb -> plan ----------------------------------------------------------

    def _plan_for(self, tf: TaskForce, view: WorldView) -> Plan:
        """Render one task force's objective as a `Plan` the follower executes.

        Surplus is RESERVE for targeted verbs — a member the objective cannot
        use (e.g. a warship in a land assault) holds rather than wandering off
        task. Strength is the full membership: a task force commits everything
        it has to its one job."""
        verb = tf.objective.verb
        target = tf.objective.target
        if verb in _ROLE_OF_VERB and isinstance(target, Coord):
            role = _ROLE_OF_VERB[verb]
            if verb is Verb.CAPTURE:
                role = self._capture_role(tf, target, view)
            objective = PlanObjective(target, role, len(tf.members))
            return Plan(objectives=(objective,), surplus=SurplusPolicy.RESERVE)
        # SCOUT/PATROL (either target form) range the fog frontier via the
        # surplus policy; a coordinate verb with a compass target is validator
        # garbage — stay total and hold rather than guess.
        if verb in (Verb.SCOUT, Verb.PATROL):
            return Plan(objectives=(), surplus=SurplusPolicy.SCOUT)
        return Plan(objectives=(), surplus=SurplusPolicy.RESERVE)

    def _capture_role(self, tf: TaskForce, target: Coord, view: WorldView) -> Role:
        """ASSAULT or INVADE for a CAPTURE tasking (module docstring, "Verb
        compilation").

        Fog-honest landmass test: the target's land reach is flooded over the
        same fog-masked army grid the follower routes with — seen terrain
        (current or remembered) decides, unseen cells stay optimistically
        walkable — so the compiler never reads map truth the player lacks.
        ASSAULT only when EVERY ashore member can walk to the target; any
        cut-off member (or an all-lift/afloat force) makes it INVADE so the
        naval machinery moves the fist. A force with neither ground troops
        nor lift has nothing to land — plain ASSAULT lets it hold on task."""
        members = _tf_members(tf, view)
        ashore = _ashore_armies(members)
        if ashore:
            return Role.ASSAULT if _all_can_walk(ashore, target, view) else Role.INVADE
        return Role.INVADE if _has_lift(members) else Role.ASSAULT

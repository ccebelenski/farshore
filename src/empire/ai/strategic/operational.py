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

from empire.ai.strategic.campaign import (
    ABANDON_COOLDOWN,
    ABANDON_FLOOR,
    COMMIT_THRESHOLD,
    PREP_DEADLINE,
    estimate_success,
)
from empire.ai.strategic.goals import (
    BuildForcesGoal,
    CaptureCityGoal,
    DefendCityGoal,
    DenyContinentGoal,
    ExploreAreaGoal,
    ForceComposition,
    Goal,
    GoalKind,
    ProjectPowerGoal,
    goal_from_dict,
)
from empire.ai.strategic.intel.opportunities import (
    ENEMY_CAPTURE_PROBABILITY,
    NEUTRAL_CAPTURE_PROBABILITY,
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
# How many armies an offensive (capture/deny) force wants by default — enough to
# take a city AND survive the counter, given the §5.4 capture-disband tax. A v1
# knob; tuned against the land-brawl arena.
ATTACK_FORCE_SIZE = 3
_ARMY = UnitKind.ARMY


def _fortified_fist(rules: object) -> int:
    """Armies a capture force needs to run a city's artillery gauntlet (spec
    §4.7). The city fires once per round over ~`range` approach rounds, so it
    can stop at most `range` armies — `range + 1` guarantees one lands. With
    the default range 2 that is 3, matching `ATTACK_FORCE_SIZE`. Reads the
    optional `city_artillery_range` the RuleSet carries; 0 → no artillery, so
    the default fist applies.

    This is the concentration governor: a capture force stays FORMING (pulling
    in armies, drawing production) until it reaches the fist, so force massES on
    the objective instead of trickling in to be shot down. `rules` typed loosely
    to avoid threading the concrete RuleSet through every composition call."""
    artillery_range = getattr(rules, "city_artillery_range", 0)
    if artillery_range > 0:
        return max(ATTACK_FORCE_SIZE, artillery_range + 1)
    return ATTACK_FORCE_SIZE


def _overstrength_extra(rules: object) -> int:
    """Surplus armies an offensive fist may absorb *beyond* its minimum, for
    gauntlet/counter-attack redundancy (Phase 15.7 step 2). Scales with the
    artillery gauntlet — a city stops ≈`range` armies on the approach, so carry
    that many spares to still land the fist — and is at least 1 otherwise (a
    captured city's §5.4 disband tax + the inevitable counter cost a body)."""
    return max(1, getattr(rules, "city_artillery_range", 0))
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
        # Scrap forces whose objective is hopeless even at best case, freeing
        # their units to redirect and putting the target in cooldown.
        self._abandon_hopeless(survivors, view, turn, memory)
        # Match forces to goals by *content* signature, not the strategist's
        # per-turn goal id (which is reallocated each plan). A goal that already
        # has a live force just refreshes that force's goal reference.
        by_signature = {
            _goal_signature(tf.goal): tf for tf in survivors if not tf.is_terminal()
        }
        claimed = {uid for tf in survivors for uid in tf.unit_ids}
        idle = self._idle_pool(view, claimed)

        for goal in goals:
            if self._in_cooldown(goal, turn, memory):
                continue
            signature = _goal_signature(goal)
            existing = by_signature.get(signature)
            if existing is not None:
                existing.goal = goal
                continue
            new_force = self._assemble(goal, idle, view, turn, memory)
            survivors.append(new_force)
            by_signature[signature] = new_force

        # Concentration: pour leftover idle units into the highest-priority
        # under-strength forces, so new production massES into one fist instead
        # of spawning scattered hunters.
        self._reinforce(survivors, idle, view, turn)

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
            elif not tf.unit_ids and tf.state is not TaskForceState.FORMING:
                # Had a force and lost it all. A FORMING force legitimately has
                # no units yet — it's still awaiting production, not disbanded.
                tf.state = TaskForceState.DISBANDED
                tf.terminal_since = turn
            survivors.append(tf)
        return survivors

    def _abandon_hopeless(
        self,
        forces: list[TaskForce],
        view: WorldView,
        turn: int,
        memory: AIMemory,
    ) -> None:
        """Scrap any capture force whose objective is doomed *even at best case*
        — a full fist that had already arrived (`formation_turns=0`) still can't
        clear `ABANDON_FLOOR`. Best-case so the test is a property of the target
        and the board, not of how this particular muster is going: a hopeless
        siege is hopeless no matter how it formed. Disbanding it frees the units
        to redirect (next turn, after the reap) and cools the target down so the
        greedy strategist can't re-propose it straight back into assembly.

        Absolute and independent of any rival objective, so a sticky incumbent
        can never suppress it and per-turn odds jitter can never trigger it."""
        fist = _fortified_fist(view.rules)
        for tf in forces:
            if tf.is_terminal() or not isinstance(tf.goal, CaptureCityGoal):
                continue
            best_case = _campaign_p(
                tf.goal, view, assault_size=fist, formation_turns=0
            )
            if best_case < ABANDON_FLOOR:
                tf.state = TaskForceState.DISBANDED
                tf.terminal_since = turn
                memory.abandoned_targets[int(tf.goal.target_city_id)] = turn

    def _in_cooldown(self, goal: Goal, turn: int, memory: AIMemory) -> bool:
        """True while a just-abandoned capture target is still cooling down, so
        the planner declines to re-assemble a force for it (anti-thrash). Expired
        entries are pruned in passing so the map stays bounded."""
        if not isinstance(goal, CaptureCityGoal):
            return False
        cid = int(goal.target_city_id)
        since = memory.abandoned_targets.get(cid)
        if since is None:
            return False
        if turn - since >= ABANDON_COOLDOWN:
            del memory.abandoned_targets[cid]
            return False
        return True

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
        composition = _required_composition(goal, view)
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

        del short  # the launch gate (in `_reinforce`) decides promotion
        target = _goal_target(goal, view)
        # Always born FORMING; `_reinforce` runs the single launch gate this
        # same turn, so even a fully-crewed fist must clear the odds threshold
        # (or hit the deadline) before it marches — no force bypasses the gate.
        tf = TaskForce(
            id=TaskForceId(memory.next_task_force_id),
            goal=goal,
            unit_ids=unit_ids,
            role_assignments=roles,
            target=target,
            state=TaskForceState.FORMING,
            created_turn=turn,
            rendezvous=_rendezvous(target, view),
        )
        memory.next_task_force_id += 1
        return tf

    def _reinforce(
        self,
        forces: list[TaskForce],
        idle: dict[UnitKind, list[UnitId]],
        view: WorldView,
        turn: int,
    ) -> None:
        """Distribute leftover idle units into under-strength forces, highest
        value-per-turn first, then run the single FORMING→EN_ROUTE launch gate.

        A force launches when EITHER:
        * the prep deadline has passed and it has at least one unit to commit
          (`PREP_DEADLINE`) — the 'go with what you have' anti-stalemate rule
          that stops a force mustering forever; OR
        * it is at full strength AND its odds clear `COMMIT_THRESHOLD` — the
          combined-arms launch decision (Phase 15.7): mass the fist, then strike
          only when `estimate_success` says the moment is good enough. A full
          fist below threshold holds (waiting for the board to improve) until
          the deadline forces its hand.

        An empty force keeps forming; there is nothing to send. Non-combat goals
        have `_campaign_p == 1.0`, so they launch the instant they are full,
        exactly as before."""
        active = sorted(
            (tf for tf in forces if not tf.is_terminal()),
            key=lambda tf: tf.goal.rank(),
            reverse=True,
        )
        for tf in active:
            short = False
            for kind, needed in _required_composition(tf.goal, view).entries:
                have = sum(1 for uid in tf.unit_ids if _unit_kind(view, uid) is kind)
                pool = idle.get(kind, [])
                while have < needed and pool:
                    uid = pool.pop(0)
                    tf.unit_ids.append(uid)
                    tf.role_assignments[uid] = _role_for(tf.goal, kind)
                    have += 1
                if have < needed:
                    short = True
            if tf.state is TaskForceState.FORMING:
                formation_turns = turn - tf.created_turn
                deadline_passed = formation_turns >= PREP_DEADLINE and bool(tf.unit_ids)
                if deadline_passed:
                    tf.state = TaskForceState.EN_ROUTE
                elif not short:
                    army_n = sum(
                        1 for uid in tf.unit_ids if _unit_kind(view, uid) is _ARMY
                    )
                    p = _campaign_p(
                        tf.goal,
                        view,
                        assault_size=army_n,
                        formation_turns=formation_turns,
                    )
                    if p >= COMMIT_THRESHOLD:
                        tf.state = TaskForceState.EN_ROUTE

        self._overstrength(active, idle, view)

    def _overstrength(
        self,
        active: list[TaskForce],
        idle: dict[UnitKind, list[UnitId]],
        view: WorldView,
    ) -> None:
        """Pour any *still*-idle armies into the offensive fists, highest rank
        first, up to an over-strength cap (Phase 15.7 step 2). Runs only after
        every force has its minimum, so it spends genuine surplus — muscle that
        would otherwise wander in Hunt mode is far better spent thickening a real
        assault: extra armies are gauntlet redundancy (lose some to city
        artillery on the approach, still land the fist) and counter-attack
        insurance. Bounded per force so force still *concentrates* rather than
        all piling onto the single top front; the remainder is left for the
        strategist's exploration outlet (step 2b) to claim."""
        extra = _overstrength_extra(view.rules)
        if extra <= 0:
            return
        pool = idle.get(_ARMY, [])
        cap = _fortified_fist(view.rules) + extra
        for tf in active:  # already rank-sorted, strongest objective first
            if tf.is_terminal() or not _is_offensive(tf.goal):
                continue
            have = sum(1 for uid in tf.unit_ids if _unit_kind(view, uid) is _ARMY)
            while have < cap and pool:
                uid = pool.pop(0)
                tf.unit_ids.append(uid)
                tf.role_assignments[uid] = _role_for(tf.goal, _ARMY)
                have += 1

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
            for kind, needed in _required_composition(tf.goal, view).entries:
                owned = sum(1 for uid in tf.unit_ids if _unit_kind(view, uid) is kind)
                wanted.extend([kind] * max(0, needed - owned))

        return [
            ProductionOrder(city_id=city.id, target=kind)
            for city, kind in zip(free_cities, wanted, strict=False)
        ]


# -- goal → operational requirements ------------------------------------------


def _goal_signature(goal: Goal) -> tuple[object, ...]:
    """A stable identity for a goal's *intent*, independent of its per-turn id
    — so the same objective keeps the same task force across turns."""
    if isinstance(goal, (CaptureCityGoal, DefendCityGoal)):
        return (goal.kind, int(goal.target_city_id))
    if isinstance(goal, (ExploreAreaGoal, ProjectPowerGoal, DenyContinentGoal)):
        return (goal.kind, goal.target_region)
    return (GoalKind.BUILD_FORCES,)  # at most one build-forces force


def _is_offensive(goal: Goal) -> bool:
    """A goal whose fist is armies sent to take/clear a city — the kind that
    benefits from over-strength redundancy. Capture and deny both size to
    `_fortified_fist`; everything else has a fixed or composition-set need."""
    return isinstance(goal, (CaptureCityGoal, DenyContinentGoal))


def _required_composition(goal: Goal, view: WorldView) -> ForceComposition:
    """The force a goal needs. Capture/deny forces size to the artillery fist
    under FortifiedCities (concentrate to break the gauntlet), a single army
    otherwise — so the operational layer's FORMING→EN_ROUTE gate becomes the
    'mass before you commit' governor without the strategist vetoing attacks."""
    if isinstance(goal, CaptureCityGoal):
        return ForceComposition.of({_ARMY: _fortified_fist(view.rules)})
    if isinstance(goal, DefendCityGoal):
        return ForceComposition.of({_ARMY: max(1, goal.garrison_size_needed)})
    if isinstance(goal, ProjectPowerGoal):
        counts = dict(goal.force_composition.entries)
        counts[_TRANSPORT] = max(counts.get(_TRANSPORT, 0), goal.transport_count)
        return ForceComposition.of(counts)
    if isinstance(goal, DenyContinentGoal):
        return ForceComposition.of({_ARMY: _fortified_fist(view.rules)})
    if isinstance(goal, ExploreAreaGoal):
        return ForceComposition.of({_ARMY: 1})
    if isinstance(goal, BuildForcesGoal):
        return goal.force_composition_target
    return ForceComposition()


def _field_odds(goal: CaptureCityGoal, view: WorldView) -> float:
    """The intel-prior chance of beating a capture target's mobile defenders
    (`Opportunity.success_probability`, derived here from the same canonical
    priors): a neutral city always falls to an army that reaches it; an enemy
    (or no-longer-visible) city carries the garrisoned prior. The artillery
    gauntlet and time-pressure factors are layered on by `estimate_success`."""
    cid = goal.target_city_id
    if any(c.id == cid for c in view.neutral_cities):
        return NEUTRAL_CAPTURE_PROBABILITY
    return ENEMY_CAPTURE_PROBABILITY


def _campaign_p(
    goal: Goal, view: WorldView, *, assault_size: int, formation_turns: int
) -> float:
    """P(success) for a force's launch/abandon decision (Phase 15.7). Only a
    city assault is gated on combat odds; every other goal returns 1.0 so the
    launch gate reduces to 'full or deadline' for non-combat forces."""
    if not isinstance(goal, CaptureCityGoal):
        return 1.0
    return estimate_success(
        field_odds=_field_odds(goal, view),
        assault_size=assault_size,
        formation_turns=formation_turns,
        my_city_count=len(view.own_cities),
        enemy_city_count=len(view.known_enemy_cities),
        # Surprise inference is deferred to Phase 15.7 step 2; assume spotted
        # (no surprise bonus) — the conservative side.
        any_unit_spotted=True,
        rules=view.rules,
    )


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

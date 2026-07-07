"""`LlmGeneralController`: the in-game general — doctrine over the staff.

Satisfies the `AIController` Protocol by COMPOSITION: it owns a `PortfolioAI`
executor (the staff), the engine-owned `TaskForceRegistry`, and the merged
epoch pipeline (briefing → client → validator → registry → compiler). Every
turn it prunes the registry against the living roster; on epoch turns (every
`cadence` turns) it runs one doctrine epoch; then it plans the turn as:

  compiled doctrine movement for TASKED units (one `PlanFollower` per task
  force over a member-filtered `TaskForceView`)
+ the executor's plan over the COMPLEMENT (a `TaskForceView` of every
  unassigned unit — production and the reserve stay fully the staff's),
  with standing `BuildDirective`s overriding production city-by-city.

FAIL-SAFE SEMANTICS (the competence floor, non-negotiable): ANY failure on
the general's path — client exception, undelivered answer, a doctrine with
zero accepted amendments, apply errors, or an unexpected exception anywhere —
degrades THAT TURN to the pure executor plan, records the reason (surfaced
via `last_failure`/`failure_log` for the TUI), and tries again at the next
epoch. The game cannot hang or crash because of the general; at worst it is
exactly `PortfolioAI`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

from empire.ai.general.briefing import BriefingRenderer
from empire.ai.general.client import ChatAnswer
from empire.ai.general.compiler import DoctrineCompiler, TaskForceView
from empire.ai.general.ledger import LedgerReport, TaskForceLedger
from empire.ai.general.primer import PRIMER
from empire.ai.general.registry import TaskForceRegistry
from empire.ai.general.trace import EpochTraceWriter
from empire.ai.general.validator import DoctrineValidator, ValidationContext
from empire.ai.search.follower import PlanFollower
from empire.ai.search.portfolio import PortfolioAI
from empire.contracts.doctrine import BuildDirective, Doctrine, Refusal, TaskForce, TaskForceId
from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove, UnloadOrder
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.unit import UnitKind

# Strategic tempo: one doctrine epoch every this-many turns (planning/08
# "Cadence"). Between epochs the standing registry keeps steering.
EPOCH_CADENCE = 8


class GeneralClient(Protocol):
    """What the controller needs from a chat client: one delivery call.

    `ChatClient` satisfies this; tests substitute scripted stubs. Transport
    failures are expected to raise (any exception degrades the epoch)."""

    def complete(self, prompt: str, *, seed: int) -> ChatAnswer: ...


class LlmGeneralController:
    """PortfolioAI wrapped in a doctrine epoch loop; satisfies `AIController`."""

    def __init__(
        self,
        client: GeneralClient,
        executor: PortfolioAI | None = None,
        cadence: int = EPOCH_CADENCE,
        seed: int = 1,
    ) -> None:
        self._client = client
        self._executor = executor if executor is not None else PortfolioAI()
        self._cadence = max(1, cadence)
        self._seed = seed
        self._registry = TaskForceRegistry()
        self._renderer = BriefingRenderer()
        self._validator = DoctrineValidator()
        self._compiler = DoctrineCompiler()
        # Standing BUILD directives (coord -> kind): a directive holds until a
        # later one replaces it or the city is lost — "no BUILD line means the
        # city keeps its current build" must survive the executor's per-turn
        # production orders, so the override is standing state, not one-shot.
        self._builds: dict[Coord, UnitKind] = {}
        # First plan_turn is always an epoch (turn numbers start at 1).
        self._next_epoch_turn = 0
        # This turn's tasked-unit followers (for revise_move): unit -> its
        # task force's follower + membership (the revise view's scope).
        self._scoped: dict[UnitId, tuple[PlanFollower, frozenset[UnitId]]] = {}
        # The complement the executor planned with this turn; None when the
        # whole turn was the executor's (degraded or no standing forces).
        self._complement: frozenset[UnitId] | None = None
        self._last_failure: str | None = None
        self._failure_log: list[str] = []
        self._last_refusals: tuple[Refusal, ...] = ()
        # Event-sourced briefing history; wired post-construction by the
        # layer that owns the game's bus (see GameLauncher._wire_general).
        self._ledger: TaskForceLedger | None = None
        self._trace: EpochTraceWriter | None = None

    def attach_ledger(self, ledger: TaskForceLedger) -> None:
        """Give the controller its event ledger. The caller owns the bus and
        has already attached the ledger to it; the controller only consumes
        reports at briefing time and books refusals back."""
        self._ledger = ledger

    def attach_trace(self, trace: EpochTraceWriter) -> None:
        """Give the controller its war diary: every epoch attempt — success
        or failure — is appended as one JSONL record for post-game analysis
        and the fine-tuning corpus."""
        self._trace = trace

    # ---- observability (for the TUI) -------------------------------------------

    @property
    def last_failure(self) -> str | None:
        """Why the most recent epoch degraded to the executor; None after a
        clean epoch."""
        return self._last_failure

    @property
    def failure_log(self) -> tuple[str, ...]:
        """Every recorded degradation reason, in order."""
        return tuple(self._failure_log)

    @property
    def last_refusals(self) -> tuple[Refusal, ...]:
        """The latest epoch's refused orders (validator + registry) — the
        cannot-comply channel, kept for the future briefing ledger."""
        return self._last_refusals

    @property
    def registry(self) -> TaskForceRegistry:
        """The standing task forces (engine-owned tasking state)."""
        return self._registry

    # ---- AIController ---------------------------------------------------------

    def name(self) -> str:
        return "General"

    def plan_turn(self, view: WorldView) -> TurnPlan:
        try:
            return self._plan_with_doctrine(view)
        except Exception as exc:  # the competence floor
            self._fail(f"t{view.turn}: unexpected error in the general layer: {exc!r}")
            return self._executor_only(view)

    def revise_move(
        self,
        unit_id: UnitId,
        surprise: Surprise,
        view: WorldView,
    ) -> UnitMove:
        """Tasked units revise inside their task force's scope; everyone else
        revises with the executor over the complement it planned with. Total:
        a failure here means "stay put", never a crash."""
        try:
            scoped = self._scoped.get(unit_id)
            if scoped is not None:
                follower, members = scoped
                return follower.revise_move(unit_id, surprise, TaskForceView(view, members))
            if self._complement is not None:
                view = TaskForceView(view, self._complement)
            return self._executor.revise_move(unit_id, surprise, view)
        except Exception as exc:  # the competence floor
            self._fail(f"t{view.turn}: revise_move #{int(unit_id)} failed: {exc!r}")
            return UnitMove(unit_id=unit_id)

    # ---- the per-turn path -----------------------------------------------------

    def _plan_with_doctrine(self, view: WorldView) -> TurnPlan:
        roster = frozenset(u.id for u in view.own_units)
        # Engine bookkeeping only the engine can do: attrition prunes task
        # forces; lost cities drop their standing build directives.
        self._registry = self._registry.prune(roster)
        owned = {c.coord for c in view.own_cities}
        self._builds = {c: k for c, k in self._builds.items() if c in owned}

        if view.turn >= self._next_epoch_turn:
            self._next_epoch_turn = view.turn + self._cadence
            if not self._run_epoch(view, roster):
                # Failed epoch: THIS turn is pure executor; retry next epoch.
                return self._executor_only(view)
        return self._merged_plan(view)

    def _executor_only(self, view: WorldView) -> TurnPlan:
        """The degraded turn: exactly what pure `PortfolioAI` would do —
        full view, no doctrine movement, no build overrides."""
        self._scoped = {}
        self._complement = None
        return self._executor.plan_turn(view)

    def _merged_plan(self, view: WorldView) -> TurnPlan:
        """Doctrine movement for tasked units + the executor's plan for the
        complement, with standing BUILD directives applied over production."""
        if not self._registry.forces:
            # No standing tasking: the executor plans everything, unfiltered
            # (standing BUILD directives still apply — this is a healthy turn).
            self._scoped = {}
            self._complement = None
            return self._with_build_overrides(self._executor.plan_turn(view), view)

        scoped: dict[UnitId, tuple[PlanFollower, frozenset[UnitId]]] = {}
        moves: list[UnitMove] = []
        unloads: list[UnloadOrder] = []
        # Mirrors DoctrineCompiler.plan_moves, but retains each scope's
        # follower so revise_move stays inside task-force boundaries.
        for scope in self._compiler.compile(self._registry, view):
            follower = PlanFollower(scope.plan)
            tf_plan = follower.plan_turn(TaskForceView(view, scope.members))
            moves.extend(m for m in tf_plan.moves if m.unit_id in scope.members)
            unloads.extend(u for u in tf_plan.unloads if u.cargo_id in scope.members)
            for member in scope.members:
                scoped[member] = (follower, scope.members)
        self._scoped = scoped

        complement = frozenset(u.id for u in view.own_units) - self._registry.assigned()
        self._complement = complement
        exec_plan = self._executor.plan_turn(TaskForceView(view, complement))
        moves.extend(m for m in exec_plan.moves if m.unit_id in complement)
        unloads.extend(u for u in exec_plan.unloads if u.cargo_id in complement)
        merged = TurnPlan(
            # Production is entirely the executor's channel (TF followers'
            # production orders are discarded, exactly as in plan_moves)...
            production_orders=exec_plan.production_orders,
            moves=tuple(moves),
            unloads=tuple(unloads),
            sentries=tuple(s for s in exec_plan.sentries if s.unit_id in complement),
            set_orders=tuple(s for s in exec_plan.set_orders if s.unit_id in complement),
            notes=exec_plan.notes,
        )
        # ...except where the general issued a BUILD directive.
        return self._with_build_overrides(merged, view)

    def _with_build_overrides(self, plan: TurnPlan, view: WorldView) -> TurnPlan:
        """Standing `BuildDirective`s override production for THEIR cities;
        every other city stays fully the executor's."""
        if not self._builds:
            return plan
        directed = {c.id for c in view.own_cities if c.coord in self._builds}
        orders = [o for o in plan.production_orders if o.city_id not in directed]
        for city in view.own_cities:
            kind = self._builds.get(city.coord)
            # Same no-op guard as the follower: re-targeting an identical
            # build must not look like a switch (§5.2 discards work).
            if kind is None or city.production.building is kind:
                continue
            orders.append(ProductionOrder(city_id=city.id, target=kind))
        return TurnPlan(
            production_orders=tuple(orders),
            moves=plan.moves,
            unloads=plan.unloads,
            sentries=plan.sentries,
            set_orders=plan.set_orders,
            notes=plan.notes,
        )

    # ---- the epoch --------------------------------------------------------------

    def _run_epoch(self, view: WorldView, roster: frozenset[UnitId]) -> bool:
        """One doctrine epoch: briefing → client → validate → apply. True on
        a clean apply; False (with the reason recorded) on ANY failure —
        the registry is left exactly as it stood. Unexpected exceptions
        propagate to plan_turn's floor, which degrades identically."""
        task_forces = {tf.tf_id: tf for tf in self._registry.forces}
        # Collect the ledger ONCE: the per-TF lines drive CURRENT TASKINGS and
        # the general lines drive FLEET DISPATCHES (the events the briefing
        # previously never showed — unassigned losses, deliveries, orphaned
        # refusals). `collect` is read-only; `_book_epoch` resets afterwards.
        report = self._collect_ledger()
        live_events, general_events = self._route_ledger(report, task_forces)
        briefing = self._renderer.render(
            view, task_forces, live_events, view.turn, general_events
        )
        record: dict[str, object] = {"turn": view.turn, "briefing": briefing.text}
        try:
            answer = self._client.complete(
                PRIMER + "\n" + briefing.text, seed=self._seed + view.turn
            )
        except Exception as exc:  # one seam, one failure path
            self._fail(f"t{view.turn}: general unavailable: {exc}")
            self._write_trace(record, failure=self._last_failure)
            return False
        record |= {
            "answer": answer.text,
            "model": answer.model,
            "finish": answer.finish_reason,
            "attempts": answer.attempts,
        }
        if not answer.delivered:
            self._fail(
                f"t{view.turn}: answer not delivered (finish={answer.finish_reason}, "
                f"{answer.attempts} attempts)"
            )
            self._write_trace(record, failure=self._last_failure)
            return False
        validation = self._validator.validate(
            answer.text, self._context(view, task_forces, briefing.markers)
        )
        if not validation.doctrine.amendments:
            self._last_refusals = validation.refusals
            self._fail(f"t{view.turn}: no amendment accepted ({len(validation.refusals)} refused)")
            self._book_epoch()
            self._write_trace(record, failure=self._last_failure)
            return False
        registry, apply_refusals = self._registry.apply(validation.doctrine, roster)
        self._registry = registry
        self._last_refusals = validation.refusals + apply_refusals
        self._apply_builds(validation.doctrine)
        self._last_failure = None
        self._book_epoch()
        record["amendments"] = [repr(a) for a in validation.doctrine.amendments]
        self._write_trace(record, failure=None)
        return True

    def _write_trace(self, record: dict[str, object], failure: str | None) -> None:
        if self._trace is None:
            return
        record["failure"] = failure
        record["refusals"] = [
            {"order": r.order_text, "reason": r.reason} for r in self._last_refusals
        ]
        record["registry"] = [
            f"TF-{tf.tf_id}: {sorted(int(u) for u in tf.members)} "
            f"{tf.objective.verb.value} {tf.objective.target} | {tf.why}"
            for tf in self._registry.forces
        ]
        self._trace.write(record)

    def _book_epoch(self) -> None:
        """Close the ledger's books for the epoch whose lines the briefing
        just consumed, then replay this epoch's refusals so they appear in
        the NEXT briefing's ledger. Only called when a briefing was actually
        rendered and validated — a client failure keeps the books open."""
        if self._ledger is None:
            return
        self._ledger.reset()
        self._ledger.note_refusals(self._last_refusals)

    def _collect_ledger(self) -> LedgerReport:
        """This epoch's factual lines from the event ledger — both the per-TF
        `by_task_force` (CURRENT TASKINGS) and the `general` section (FLEET
        DISPATCHES). Empty when unwired (refusals still retained on
        `last_refusals`). Collected once per epoch; `collect` is read-only."""
        if self._ledger is None:
            return LedgerReport(by_task_force={}, general=())
        return self._ledger.collect()

    def _route_ledger(
        self,
        report: LedgerReport,
        task_forces: Mapping[TaskForceId, TaskForce],
    ) -> tuple[Mapping[TaskForceId, tuple[str, ...]], tuple[str, ...]]:
        """Split the ledger's per-TF lines by whether the force still stands.

        A force still in the registry keeps its lines in CURRENT TASKINGS
        (the renderer's `since:` block, keyed by tf id). A force pruned out
        between epochs — its last member lost, so `_taskings_lines` renders no
        block for it — would otherwise drop its booked lines on the floor; they
        fall through to FLEET DISPATCHES instead, each attributed
        `TF-<id> (dissolved): <line>` so the turn stamp still reads naturally.
        The general section and the orphaned lines are merged and stable-sorted
        by embedded turn so the fleet ledger reads in temporal order."""
        live: dict[TaskForceId, tuple[str, ...]] = {}
        orphaned: list[str] = []
        for tf_id, lines in report.by_task_force.items():
            if tf_id in task_forces:
                live[tf_id] = tuple(lines)
            else:
                orphaned += [f"TF-{tf_id} (dissolved): {line}" for line in lines]
        if not orphaned:
            return live, tuple(report.general)
        merged = sorted((*report.general, *orphaned), key=self._embedded_turn)
        return live, tuple(merged)

    @staticmethod
    def _embedded_turn(line: str) -> int:
        """The turn a ledger line was stamped with (its leading `t<n>:`, after
        any `TF-<id> (dissolved):` prefix). Zero if none — never on real lines,
        which the ledger always turn-stamps; only a defensive floor."""
        match = re.search(r"t(\d+):", line)
        return int(match.group(1)) if match is not None else 0

    def _context(
        self,
        view: WorldView,
        task_forces: Mapping[TaskForceId, TaskForce],
        markers: Mapping[str, UnitId],
    ) -> ValidationContext:
        """Registry-side facts for the validator. `markers` is the exact
        assignment the general just read (`Briefing.markers`), never a
        re-derivation."""
        real = view.real_map()
        roster = frozenset(u.id for u in view.own_units)
        return ValidationContext(
            turn=view.turn,
            board_width=real.width,
            board_height=real.height,
            task_forces={tf_id: tf.members for tf_id, tf in task_forces.items()},
            unassigned=self._registry.unassigned(roster),
            markers=markers,
            owned_cities=frozenset(c.coord for c in view.own_cities),
        )

    def _apply_builds(self, doctrine: Doctrine) -> None:
        for amendment in doctrine.amendments:
            if isinstance(amendment, BuildDirective):
                self._builds[amendment.city] = amendment.kind

    def _fail(self, reason: str) -> None:
        self._last_failure = reason
        self._failure_log.append(reason)

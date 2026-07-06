"""`TaskForceRegistry`: the engine-owned authoritative tasking state.

Task forces are persistent engine objects — the general amends them, the
engine owns them (planning/08 "TASKING CONTINUITY"). The registry is the one
source of truth for "what is TF-2": a frozen mapping of task-force records
that is REPLACED, never mutated, by `apply` (a doctrine's amendments) and
`prune` (attrition — the bookkeeping only the engine can do).

Totality contract: `apply` assumes the validator already normalized the
doctrine, but garbage in must produce `Refusal`s, never exceptions — the
cannot-comply channel feeds the next briefing's ledger, so every rejected
order carries its reason as text. Board feasibility (e.g. an army reinforcing
a force on another landmass) is delegated to a caller-supplied predicate over
the proposed record, keeping the registry map-agnostic.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from empire.contracts.doctrine import (
    Amendment,
    BuildDirective,
    Compass,
    ContinueOrder,
    DisbandOrder,
    Doctrine,
    FormOrder,
    Refusal,
    ReinforceOrder,
    RetaskOrder,
    TaskForce,
    TaskForceId,
)
from empire.core.coord import Coord
from empire.core.identity import UnitId

FeasibilityCheck = Callable[[TaskForce], str | None]
"""Board-feasibility oracle over a PROPOSED record: `None` if the amended
task force can actually operate (members can reach/serve the objective),
else the refusal reason. The registry itself knows no map."""


def _always_feasible(_: TaskForce) -> str | None:
    return None


def _render_target(target: Coord | Compass) -> str:
    if isinstance(target, Compass):
        return target.value
    return f"({target.x}, {target.y})"


def _render(amendment: Amendment) -> str:
    """A compact one-line rendering of the order, for the refusal ledger."""
    match amendment:
        case ContinueOrder(tf_id=tf):
            return f"TF {tf}: CONTINUE"
        case ReinforceOrder(tf_id=tf, unit_ids=ids):
            units = " ".join(f"#{u}" for u in ids)
            return f"TF {tf}: REINFORCE UNITS {units}"
        case RetaskOrder(tf_id=tf, objective=obj, adding=ids):
            text = f"TF {tf}: RETASK {obj.verb.value} {_render_target(obj.target)}"
            if ids:
                text += " ADDING " + " ".join(f"#{u}" for u in ids)
            return text
        case DisbandOrder(tf_id=tf):
            return f"TF {tf}: DISBAND"
        case FormOrder(tf_id=tf, unit_ids=ids, objective=obj):
            units = " ".join(f"#{u}" for u in ids)
            return f"FORM TF {tf}: UNITS {units} | {obj.verb.value} {_render_target(obj.target)}"
        case BuildDirective(city=city, kind=kind):
            return f"BUILD {kind.value} AT ({city.x}, {city.y})"
    return repr(amendment)  # unreachable for the closed union; stay total


@dataclass(frozen=True, slots=True)
class TaskForceRegistry:
    """Authoritative tasking state: `TaskForce` records in formation order.

    Invariants (maintained by `apply`/`prune`, assumed everywhere else):
    task-force ids are unique, and no unit belongs to two task forces.
    """

    forces: tuple[TaskForce, ...] = ()

    # ---- queries -------------------------------------------------------------

    def get(self, tf_id: TaskForceId) -> TaskForce | None:
        """The record for `tf_id`, or `None` if no such task force stands."""
        for tf in self.forces:
            if tf.tf_id == tf_id:
                return tf
        return None

    def as_mapping(self) -> Mapping[TaskForceId, TaskForce]:
        """The forces as a `{tf_id: record}` view, in formation order — the
        shape `BriefingRenderer.render` takes."""
        return {tf.tf_id: tf for tf in self.forces}

    def assigned(self) -> frozenset[UnitId]:
        """Every unit currently a member of any task force."""
        return frozenset(u for tf in self.forces for u in tf.members)

    def unassigned(self, roster: Collection[UnitId]) -> frozenset[UnitId]:
        """The UNASSIGNED pool: the living roster minus all members. New
        production lands here and is surfaced in the next briefing."""
        return frozenset(roster) - self.assigned()

    # ---- amendment application -------------------------------------------------

    def apply(
        self,
        doctrine: Doctrine,
        roster: Collection[UnitId],
        feasible: FeasibilityCheck = _always_feasible,
    ) -> tuple[TaskForceRegistry, tuple[Refusal, ...]]:
        """Apply one epoch's amendments in order; return the NEW registry plus
        every refusal.

        CONTINUE keeps a force as it stands; REINFORCE adds unassigned units,
        objective unchanged; RETASK swaps the objective and commits `adding`
        in the same act (the launch pair); DISBAND releases survivors to the
        pool (a later FORM in the same doctrine may re-home them); FORM
        creates a new force from unassigned units. A refused order leaves the
        registry exactly as it was — refused loudly, never silently
        reinterpreted. `BuildDirective`s are not tasking and pass through
        untouched (the compiler resolves them)."""
        living = frozenset(roster)
        state: dict[TaskForceId, TaskForce] = {tf.tf_id: tf for tf in self.forces}
        taken: set[UnitId] = {u for tf in self.forces for u in tf.members}
        refusals: list[Refusal] = []

        def refuse(amendment: Amendment, reason: str) -> None:
            refusals.append(Refusal(order_text=_render(amendment), reason=reason))

        def recruits(
            amendment: Amendment, ids: tuple[UnitId, ...], into: frozenset[UnitId]
        ) -> frozenset[UnitId] | None:
            """Validate units being committed to a force whose current members
            are `into`; `None` (after a refusal) if any are unavailable."""
            missing = [u for u in ids if u not in living]
            if missing:
                refuse(amendment, "no such unit: " + " ".join(f"#{u}" for u in missing))
                return None
            poached = [u for u in ids if u in taken and u not in into]
            if poached:
                refuse(
                    amendment,
                    "already assigned to another task force: "
                    + " ".join(f"#{u}" for u in poached),
                )
                return None
            return frozenset(ids)

        def install(amendment: Amendment, proposed: TaskForce) -> None:
            """Feasibility-gate `proposed`, then commit it as the new record."""
            reason = feasible(proposed)
            if reason is not None:
                refuse(amendment, reason)
                return
            previous = state.get(proposed.tf_id)
            if previous is not None:
                taken.difference_update(previous.members)
            state[proposed.tf_id] = proposed
            taken.update(proposed.members)

        for amendment in doctrine.amendments:
            match amendment:
                case BuildDirective():
                    continue  # production channel; not registry business
                case ContinueOrder(tf_id=tf_id):
                    if tf_id not in state:
                        refuse(amendment, f"no such task force: TF {tf_id}")
                case ReinforceOrder(tf_id=tf_id, unit_ids=ids):
                    tf = state.get(tf_id)
                    if tf is None:
                        refuse(amendment, f"no such task force: TF {tf_id}")
                        continue
                    if not ids:
                        refuse(amendment, "REINFORCE names no units")
                        continue
                    added = recruits(amendment, ids, tf.members)
                    if added is None:
                        continue
                    install(
                        amendment,
                        TaskForce(
                            tf_id=tf.tf_id,
                            members=tf.members | added,
                            objective=tf.objective,
                            why=tf.why,
                            formed_turn=tf.formed_turn,
                        ),
                    )
                case RetaskOrder(tf_id=tf_id, objective=objective, adding=ids, why=why):
                    tf = state.get(tf_id)
                    if tf is None:
                        refuse(amendment, f"no such task force: TF {tf_id}")
                        continue
                    added = recruits(amendment, ids, tf.members)
                    if added is None:
                        continue
                    install(
                        amendment,
                        TaskForce(
                            tf_id=tf.tf_id,
                            members=tf.members | added,
                            objective=objective,
                            why=why,  # the general's words at RETASK time
                            formed_turn=tf.formed_turn,
                        ),
                    )
                case DisbandOrder(tf_id=tf_id):
                    tf = state.pop(tf_id, None)
                    if tf is None:
                        refuse(amendment, f"no such task force: TF {tf_id}")
                        continue
                    taken.difference_update(tf.members)  # released to UNASSIGNED
                case FormOrder(tf_id=tf_id, unit_ids=ids, objective=objective, why=why):
                    if tf_id in state:
                        refuse(amendment, f"task force already exists: TF {tf_id}")
                        continue
                    if not ids:
                        refuse(amendment, "FORM names no units")
                        continue
                    members = recruits(amendment, ids, frozenset())
                    if members is None:
                        continue
                    install(
                        amendment,
                        TaskForce(
                            tf_id=tf_id,
                            members=members,
                            objective=objective,
                            why=why,
                            formed_turn=doctrine.turn,
                        ),
                    )
                case _:
                    refuse(amendment, "unrecognized amendment")

        # Formation order: surviving forces keep their old position, new ones
        # append in creation order (dict insertion order carries both).
        return TaskForceRegistry(forces=tuple(state.values())), tuple(refusals)

    # ---- attrition ------------------------------------------------------------

    def prune(self, living_unit_ids: Collection[UnitId]) -> TaskForceRegistry:
        """Engine bookkeeping after combat: dead members drop out of their
        rosters; a task force with nobody left dissolves. The ledger (not the
        registry) tells the general what it lost."""
        living = frozenset(living_unit_ids)
        survivors: list[TaskForce] = []
        for tf in self.forces:
            remaining = tf.members & living
            if not remaining:
                continue  # dissolved by attrition
            if remaining == tf.members:
                survivors.append(tf)
            else:
                survivors.append(
                    TaskForce(
                        tf_id=tf.tf_id,
                        members=remaining,
                        objective=tf.objective,
                        why=tf.why,
                        formed_turn=tf.formed_turn,
                    )
                )
        return TaskForceRegistry(forces=tuple(survivors))

"""`TurnPlan` and its components: the declarative output of `AIController.plan_turn`.

A `TurnPlan` is the AI's intent for the turn. The engine applies it; the AI
does not have side-effecting authority. All members are frozen at emission
time. See `planning/03-ai-design.md` §1 for design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from empire.core.identity import CityId, UnitId
from empire.core.standing_order import StandingOrder
from empire.core.unit import UnitKind


@dataclass(frozen=True, slots=True)
class UnitMove:
    """A unit's intended movement for this turn.

    `path` is an ordered tuple of coordinates the unit intends to enter, in
    order. An empty path is a no-op (the unit stays put — equivalent to a
    sentry for this turn). The engine resolves the path one cell at a time
    and may abort early if a surprise invalidates a step (see
    `planning/03-ai-design.md` §1 reactivity boundary).
    """

    unit_id: UnitId
    path: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class ProductionOrder:
    """An order to set a city's production target.

    `target = None` clears the production (city goes idle). Setting a target
    different from the city's current build incurs the change penalty per
    `planning/01-game-rules-spec.md` §5.2; the engine applies that, not the
    AI.
    """

    city_id: CityId
    target: UnitKind | None


@dataclass(frozen=True, slots=True)
class UnloadOrder:
    """Unload one aboard cargo unit onto an adjacent cell (spec §3.4).

    `cargo_id` is the aboard unit to land; `to` is the destination cell
    (must be adjacent to its carrier). The engine resolves the landing like
    a normal step — an army can storm ashore into an enemy/neutral city.
    Applied after `moves`, so a carrier can sail adjacent to the target this
    turn and then disembark.
    """

    cargo_id: UnitId
    to: tuple[int, int]


@dataclass(frozen=True, slots=True)
class UnitSentry:
    """A sentry/wake command for a unit.

    `wake=False` puts the unit on sentry (skipped during movement phase
    unless an enemy is sighted nearby). `wake=True` clears sentry status.
    """

    unit_id: UnitId
    wake: bool = False


@dataclass(frozen=True, slots=True)
class SetOrder:
    """Declaratively set (or clear) a unit's standing order.

    `order = None` clears any current standing order. Otherwise the unit's
    `standing_order` is set to the supplied `Heading`, `PatrolPath`, or
    `Sentry`. The set takes effect at the end of this turn's plan
    application; the engine's standing-orders step on the *next* turn is
    when the order actually drives movement.
    """

    unit_id: UnitId
    order: StandingOrder | None


def _empty_notes() -> dict[str, object]:
    return {}


@dataclass(frozen=True, slots=True)
class TurnPlan:
    """An AI's declarative intent for the current turn.

    The engine validates and applies the plan; ill-formed plans (unit that
    doesn't exist, target city not owned, etc.) are reported back so the AI
    can be improved. Phase 3 ships the data type; the engine that consumes
    it lands in Phase 8.
    """

    production_orders: tuple[ProductionOrder, ...] = ()
    moves: tuple[UnitMove, ...] = ()
    unloads: tuple[UnloadOrder, ...] = ()
    sentries: tuple[UnitSentry, ...] = ()
    set_orders: tuple[SetOrder, ...] = ()
    # Debug/telemetry. Free-form. Not consumed by the engine; useful for AI
    # introspection and replay tooling.
    notes: dict[str, object] = field(default_factory=_empty_notes)

"""Doctrine contracts: the strategic general's order language (contract v7).

Shared types between the general layer (LLM or stub), the validator/compiler,
and the briefing renderer. The general reads a `Briefing` and answers with
amendment lines; the validator parses them into a `Doctrine`; the compiler
turns that into per-task-force planning scopes for the executor. A `why` is
recorded and replayed in later briefings but never executed — officers
execute the VERB, not the reason (see planning/08-llm-general.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.unit import UnitKind

TaskForceId = str
"""Normalized upper-case task-force label, e.g. "1", "4", "ATK-01"."""


class Verb(Enum):
    """The five strategic-altitude order verbs."""

    CAPTURE = "CAPTURE"
    DEFEND = "DEFEND"
    SCOUT = "SCOUT"
    PATROL = "PATROL"
    STAGE = "STAGE"


class Compass(Enum):
    """Universal direction primitives, legal as SCOUT/PATROL targets."""

    N = "N"
    NE = "NE"
    E = "E"
    SE = "SE"
    S = "S"
    SW = "SW"
    W = "W"
    NW = "NW"


@dataclass(frozen=True, slots=True)
class Objective:
    """A verb aimed at a target.

    CAPTURE and DEFEND take a city coordinate; STAGE takes any coordinate;
    SCOUT and PATROL take a coordinate or a compass direction. The validator
    enforces those pairings — the type deliberately does not.
    """

    verb: Verb
    target: Coord | Compass


@dataclass(frozen=True, slots=True)
class TaskForce:
    """One registry record: engine-owned tasking state.

    Records are replaced, never mutated, when an amendment applies. `why`
    is the general's own words at FORM/RETASK time, replayed verbatim in
    every later briefing (intent must survive epochs).
    """

    tf_id: TaskForceId
    members: frozenset[UnitId]
    objective: Objective
    why: str
    formed_turn: int


@dataclass(frozen=True, slots=True)
class ContinueOrder:
    """Keep the task force's objective and membership as they stand."""

    tf_id: TaskForceId
    why: str = ""


@dataclass(frozen=True, slots=True)
class ReinforceOrder:
    """Add UNASSIGNED units to a task force; its objective is unchanged."""

    tf_id: TaskForceId
    unit_ids: tuple[UnitId, ...]
    why: str = ""


@dataclass(frozen=True, slots=True)
class RetaskOrder:
    """Change a task force's objective, optionally committing new units in
    the same act (`RETASK <verb> <target> ADDING <ids>` — the launch pair)."""

    tf_id: TaskForceId
    objective: Objective
    adding: tuple[UnitId, ...] = ()
    why: str = ""


@dataclass(frozen=True, slots=True)
class DisbandOrder:
    """Dissolve a task force; surviving members return to UNASSIGNED."""

    tf_id: TaskForceId
    why: str = ""


@dataclass(frozen=True, slots=True)
class FormOrder:
    """Create a new task force from UNASSIGNED (or just-released) units."""

    tf_id: TaskForceId
    unit_ids: tuple[UnitId, ...]
    objective: Objective
    why: str = ""


@dataclass(frozen=True, slots=True)
class BuildDirective:
    """Set a city's production. Addressed by coordinate because that is what
    the general sees; the compiler resolves it to a `ProductionOrder`.
    Absence of a directive means the city keeps its current build."""

    city: Coord
    kind: UnitKind
    why: str = ""


Amendment = (
    ContinueOrder
    | ReinforceOrder
    | RetaskOrder
    | DisbandOrder
    | FormOrder
    | BuildDirective
)


@dataclass(frozen=True, slots=True)
class Doctrine:
    """One epoch's parsed, validated order set."""

    turn: int
    amendments: tuple[Amendment, ...]


@dataclass(frozen=True, slots=True)
class Refusal:
    """The cannot-comply channel: an order the compiler rejected, with the
    reason. Reported back into the next briefing's ledger — an infeasible
    order is refused loudly, never silently reinterpreted."""

    order_text: str
    reason: str


@dataclass(frozen=True, slots=True)
class Briefing:
    """The rendered strategic picture handed to the general: full prompt
    text (cache-native section order) plus the turn it describes.

    `markers` is the renderer's own map-marker assignment (letter -> unit id,
    UNITS-table order) so `ValidationContext` builders consume the exact
    mapping the general read instead of re-deriving the assignment rule.
    Overflow units (past the marker alphabet) carry no entry."""

    turn: int
    text: str
    markers: Mapping[str, UnitId] = field(default_factory=dict)

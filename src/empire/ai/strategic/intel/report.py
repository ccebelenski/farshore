"""Immutable artifacts produced by `IntelService`.

`WorldView` is a live, mutable view of the world (see
`planning/03-ai-design.md` §1). The strategist must reason against a *stable*
snapshot of the situation, so `IntelService` freezes its assessment into the
value types below. Everything here is a frozen dataclass with hashable
(`tuple` / `frozenset`) fields, so two reports computed from the same
`WorldView` compare equal — the purity guarantee the canary tests rely on.

See `planning/03-ai-design.md` §3.1 and `planning/05-implementation-plan.md`
Phase 11 for the design.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.unit import UnitKind


@dataclass(frozen=True, slots=True)
class Threat:
    """One known enemy force and the danger it projects.

    `projected_reach` is the set of cells the enemy could reach within the
    intel service's projection horizon, computed optimistically (unknown
    terrain is assumed traversable, so defenders do not under-estimate).
    `at_risk_*` list the friendly assets currently standing inside that reach.
    `staleness` is the number of turns since the unit was last seen — 0 for a
    currently-visible enemy, higher for one inferred from memory.
    """

    enemy_unit_id: UnitId
    enemy_owner_id: PlayerId
    kind: UnitKind
    origin: Coord
    combat_power: int
    staleness: int
    projected_reach: frozenset[Coord]
    at_risk_city_ids: tuple[CityId, ...]
    at_risk_unit_ids: tuple[UnitId, ...]


class OpportunityKind(Enum):
    """What kind of gain an `Opportunity` represents."""

    CAPTURE_NEUTRAL_CITY = "capture_neutral_city"
    CAPTURE_ENEMY_CITY = "capture_enemy_city"
    ATTACK_ENEMY_UNIT = "attack_enemy_unit"


@dataclass(frozen=True, slots=True)
class Opportunity:
    """A capturable city or attackable enemy force.

    `score` is the ranking key — `value * success_probability` discounted by
    distance — so a caller can simply sort opportunities by descending score.
    Exactly one of `target_city_id` / `target_unit_id` is set, matching `kind`.
    """

    kind: OpportunityKind
    target: Coord
    target_city_id: CityId | None
    target_unit_id: UnitId | None
    value: int
    success_probability: float
    distance: int
    score: float


class ChokePointKind(Enum):
    """A strait (navigable water pinched by land) or isthmus (land pinched by
    water). Both funnel movement and are worth holding or watching."""

    STRAIT = "strait"
    ISTHMUS = "isthmus"


class ChokeAxis(Enum):
    """The axis the passage runs along: VERTICAL means it is pinched on its
    east and west sides (so traffic flows north-south), HORIZONTAL the reverse.
    """

    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


@dataclass(frozen=True, slots=True)
class ChokePoint:
    """A single bottleneck cell."""

    coord: Coord
    kind: ChokePointKind
    axis: ChokeAxis


class TheaterState(Enum):
    """Strategic ownership state of a `Theater` (per `03-ai-design.md` §3.1)."""

    FRIENDLY_CORE = "friendly_core"
    CONTESTED = "contested"
    ENEMY_CORE = "enemy_core"
    UNEXPLORED = "unexplored"


@dataclass(frozen=True, slots=True)
class Theater:
    """A connected region — a landmass plus its adjacent waters — tagged with
    its strategic state and the cities that determine that state.

    `cells` is the region's footprint; `state` is derived from which players
    own cities inside it (see `theaters.py`).
    """

    cells: frozenset[Coord]
    state: TheaterState
    friendly_city_ids: tuple[CityId, ...]
    enemy_city_ids: tuple[CityId, ...]
    neutral_city_ids: tuple[CityId, ...]


@dataclass(frozen=True, slots=True)
class IntelReport:
    """The frozen situation assessment for one player at one turn.

    Reproducible: the same `WorldView` always yields an equal `IntelReport`.
    """

    turn: int
    threats: tuple[Threat, ...]
    opportunities: tuple[Opportunity, ...]
    chokepoints: tuple[ChokePoint, ...]
    theaters: tuple[Theater, ...]

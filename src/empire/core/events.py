"""Event dataclasses emitted by `Game` and `TurnManager` during play.

These live in `core` because they are domain-level data shapes — `Game`
emits them, and `core` cannot depend on `empire.events`. The
subscription-management infrastructure (`EventBus`) lives in
`empire.events.bus`; subscribers import event types from here and the bus
from there.

"""

from __future__ import annotations

from dataclasses import dataclass

from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId


@dataclass(frozen=True, slots=True)
class TurnAdvancedEvent:
    """Emitted after a full round completes; `turn` is the new turn number."""

    turn: int


@dataclass(frozen=True, slots=True)
class GameEndedEvent:
    """Emitted when `Game.is_over()` becomes true."""

    winner_id: PlayerId | None
    final_turn: int


@dataclass(frozen=True, slots=True)
class UnitPlacedEvent:
    unit_id: UnitId
    at: Coord


@dataclass(frozen=True, slots=True)
class UnitMovedEvent:
    unit_id: UnitId
    from_: Coord
    to: Coord


@dataclass(frozen=True, slots=True)
class UnitRemovedEvent:
    unit_id: UnitId
    last_coord: Coord


@dataclass(frozen=True, slots=True)
class UnitDisbandedEvent:
    """A unit removed for exceeding a city's support limit (spec §5.4) —
    e.g. an army that never left a friendly city, or a second ship in dock."""

    unit_id: UnitId
    last_coord: Coord


@dataclass(frozen=True, slots=True)
class CityCapturedEvent:
    city_id: CityId
    new_owner_id: PlayerId | None
    previous_owner_id: PlayerId | None


@dataclass(frozen=True, slots=True)
class CityFiredEvent:
    """A city's artillery fired at an enemy unit (spec §4.7).

    `destroyed` is True if the salvo killed the target; `hit` is True if it
    connected at all (a damaging hit on a multi-HP unit has hit=True,
    destroyed=False). A miss has both False but still consumed the shot.
    """

    city_id: CityId
    target_id: UnitId
    target_coord: Coord
    hit: bool
    destroyed: bool

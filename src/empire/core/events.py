"""Event dataclasses emitted by `Game` and `TurnManager` during play.

These live in `core` because they are domain-level data shapes — `Game`
emits them, and `core` cannot depend on `empire.events`. The
subscription-management infrastructure (`EventBus`) lives in
`empire.events.bus`; subscribers import event types from here and the bus
from there.

Phase 4 declares the events; Phase 8 actually emits most of them.
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
class CityCapturedEvent:
    city_id: CityId
    new_owner_id: PlayerId | None
    previous_owner_id: PlayerId | None

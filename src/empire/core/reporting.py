"""Bus-event reporting for resolved movement outcomes.

The ONE publisher for every path that resolves a unit step or amphibious
landing — the engine turn loop (`TurnManager`) and the TUI's immediate
moves/unloads — so the event stream cannot drift between call sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from empire.core.engine import MoveOutcome, StepOutcome
from empire.core.events import (
    CityCapturedEvent,
    UnitDisbandedEvent,
    UnitMovedEvent,
    UnitRemovedEvent,
)

if TYPE_CHECKING:
    from empire.core.coord import Coord
    from empire.core.event_bus import EventBusProtocol
    from empire.core.identity import PlayerId, UnitId
    from empire.core.map import Map


def publish_move_outcome(
    bus: EventBusProtocol,
    real_map: Map,
    new_owner_id: PlayerId,
    unit_id: UnitId,
    start: Coord,
    outcome: MoveOutcome,
) -> None:
    """Publish the events for one resolved step/landing: movement,
    destructions, captures, and the conqueror's capture-time disband.

    The disband is reported at the CITY cell, not the square the army attacked
    from — the conqueror disbanded into the city at capture (§4.5) and never
    physically entered the cell.
    """
    unit = real_map.unit_by_id(unit_id)
    if outcome.steps_taken > 0 and unit is not None:
        bus.publish(UnitMovedEvent(unit_id=unit_id, from_=start, to=unit.coord))
    for uid in outcome.units_destroyed:
        bus.publish(UnitRemovedEvent(unit_id=uid, last_coord=start))
    for cid in outcome.cities_captured:
        bus.publish(
            CityCapturedEvent(
                city_id=cid, new_owner_id=new_owner_id, previous_owner_id=None
            )
        )
    if outcome.last_outcome is StepOutcome.CAPTURED:
        city = real_map.city_by_id(outcome.cities_captured[-1])
        bus.publish(
            UnitDisbandedEvent(
                unit_id=unit_id,
                last_coord=city.coord if city is not None else start,
            )
        )

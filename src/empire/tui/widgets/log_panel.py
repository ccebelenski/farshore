"""`LogPanel`: scrolling event log. Subscribes to `EventBus` and appends
human-readable lines for unit moves, captures, removals, and turn ticks.

Fog of war: events outside the human player's visible/remembered set are
not written. Own units always show (the human owns them — fog never
hides your own actions from yourself). Enemy production at an unseen
city, an enemy unit walking across an unseen continent, etc. are all
suppressed — otherwise the log silently betrays information the player
should have to scout for.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import RichLog

from empire.core.coord import Coord
from empire.core.events import (
    CityCapturedEvent,
    GameEndedEvent,
    TurnAdvancedEvent,
    UnitMovedEvent,
    UnitPlacedEvent,
    UnitRemovedEvent,
)
from empire.core.map import Map
from empire.core.player import Player
from empire.events.bus import EventBus


class LogPanel(VerticalScroll):
    """A `RichLog`-backed event panel."""

    DEFAULT_CSS = """
    LogPanel {
        height: 8;
        border-top: solid $accent;
    }
    LogPanel > RichLog {
        background: $surface;
    }
    """

    # The log is read-only context for the player; it must not steal focus
    # from the screen, or screen-level letter bindings stop firing.
    can_focus = False

    def compose(self):  # type: ignore[no-untyped-def]
        # `can_focus=False` is critical: RichLog defaults to focusable, and
        # if it grabs focus from the screen, single-letter keypresses (e,
        # u, p, etc.) hit the log instead of the PlayScreen bindings.
        log = RichLog(id="log", wrap=False, highlight=False, markup=False)
        log.can_focus = False
        yield log

    def attach_to(self, bus: EventBus, real_map: Map, viewer: Player) -> None:
        """Wire bus subscriptions that append to the log, filtered by `viewer`'s fog."""
        log = self.query_one("#log", RichLog)

        def write(line: str) -> None:
            log.write(line)

        def seen(c: Coord) -> bool:
            return c in viewer.view.visible or c in viewer.view.remembered

        def own_unit(uid: int) -> bool:
            from empire.core.identity import UnitId

            unit = real_map.unit_by_id(UnitId(uid))
            return unit is not None and unit.owner is viewer

        def on_turn(e: TurnAdvancedEvent) -> None:
            write(f"-- turn {e.turn} --")

        def on_placed(e: UnitPlacedEvent) -> None:
            if not (own_unit(int(e.unit_id)) or seen(e.at)):
                return
            write(f"  produced unit#{int(e.unit_id)} at ({e.at.x},{e.at.y})")

        def on_moved(e: UnitMovedEvent) -> None:
            if not (own_unit(int(e.unit_id)) or seen(e.from_) or seen(e.to)):
                return
            write(
                f"  unit#{int(e.unit_id)} moved "
                f"({e.from_.x},{e.from_.y})->({e.to.x},{e.to.y})",
            )

        def on_removed(e: UnitRemovedEvent) -> None:
            # Can't look up the unit (it's gone); fall back to coord visibility.
            if not seen(e.last_coord):
                return
            write(
                f"  unit#{int(e.unit_id)} destroyed at "
                f"({e.last_coord.x},{e.last_coord.y})",
            )

        def on_captured(e: CityCapturedEvent) -> None:
            # If we captured it, always show. Otherwise require visibility.
            from empire.core.identity import CityId

            city = real_map.city_by_id(CityId(int(e.city_id)))
            is_own = city is not None and city.owner is viewer
            if not is_own and (city is None or not seen(city.coord)):
                return
            owner = "?" if e.new_owner_id is None else f"P#{int(e.new_owner_id)}"
            write(f"  city#{int(e.city_id)} captured by {owner}")

        def on_ended(e: GameEndedEvent) -> None:
            winner = "?" if e.winner_id is None else f"P#{int(e.winner_id)}"
            write(f"=== game over at turn {e.final_turn}; winner={winner} ===")

        bus.subscribe(TurnAdvancedEvent, on_turn)
        bus.subscribe(UnitPlacedEvent, on_placed)
        bus.subscribe(UnitMovedEvent, on_moved)
        bus.subscribe(UnitRemovedEvent, on_removed)
        bus.subscribe(CityCapturedEvent, on_captured)
        bus.subscribe(GameEndedEvent, on_ended)

"""`LogPanel`: scrolling event log. Subscribes to `EventBus` and appends
human-readable lines for unit moves, captures, removals, and turn ticks.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import RichLog

from empire.core.events import (
    CityCapturedEvent,
    GameEndedEvent,
    TurnAdvancedEvent,
    UnitMovedEvent,
    UnitPlacedEvent,
    UnitRemovedEvent,
)
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

    def attach_to(self, bus: EventBus) -> None:
        """Wire bus subscriptions that append to the log."""
        log = self.query_one("#log", RichLog)

        def write(line: str) -> None:
            log.write(line)

        def on_turn(e: TurnAdvancedEvent) -> None:
            write(f"-- turn {e.turn} --")

        def on_placed(e: UnitPlacedEvent) -> None:
            write(f"  produced unit#{int(e.unit_id)} at ({e.at.x},{e.at.y})")

        def on_moved(e: UnitMovedEvent) -> None:
            write(
                f"  unit#{int(e.unit_id)} moved "
                f"({e.from_.x},{e.from_.y})->({e.to.x},{e.to.y})",
            )

        def on_removed(e: UnitRemovedEvent) -> None:
            write(
                f"  unit#{int(e.unit_id)} destroyed at "
                f"({e.last_coord.x},{e.last_coord.y})",
            )

        def on_captured(e: CityCapturedEvent) -> None:
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

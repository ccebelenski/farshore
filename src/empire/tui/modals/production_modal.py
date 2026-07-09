"""`ProductionModal`: pick a UnitKind to build at a city.

Keyboard-first, consistent with the other in-game pickers (ChoiceModal,
ConfirmModal): ↑/↓ move, Enter/Space select, Esc cancels (no change).
Returns the chosen `UnitKind`, or `None` to build nothing (idle), via
`dismiss(result)`; Esc returns the current target unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from empire.core.unit import UnitKind


class ProductionModal(ModalScreen[UnitKind | None]):
    """A movable-cursor list of build targets (only the kinds this city can
    actually build — e.g. no ships at a landlocked city), then "idle"."""

    DEFAULT_CSS = """
    ProductionModal {
        align: center middle;
    }
    ProductionModal Vertical {
        width: auto;
        min-width: 32;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $accent;
    }
    ProductionModal #prod-title {
        text-style: bold;
        color: $accent;
    }
    """

    def __init__(
        self,
        current: UnitKind | None,
        allowed: Sequence[UnitKind] | None = None,
        city_label: str = "",
    ) -> None:
        super().__init__()
        # Only the kinds this city can build; defaults to all (callers that know
        # the city pass the buildable set so e.g. ships never show inland).
        self._kinds: tuple[UnitKind, ...] = (
            tuple(allowed) if allowed is not None else tuple(UnitKind)
        )
        self._current = current
        self._city_label = city_label  # e.g. "Landfall (11,2)"; "" hides it
        # Cursor starts on the current target if it's still offered, else "idle".
        self._cursor = (
            self._kinds.index(current)
            if current in self._kinds
            else len(self._kinds)
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="prod-title")
            yield Static("", id="prod-list")

    def on_mount(self) -> None:
        self._repaint()

    def _repaint(self) -> None:
        current_label = self._current.value if self._current is not None else "idle"
        at = f"{self._city_label} " if self._city_label else ""
        self.query_one("#prod-title", Static).update(
            f"build {at}(now: {current_label}) — ↑↓ Enter · Esc cancel"
        )
        rows: list[str] = []
        for i, kind in enumerate(self._kinds):
            marker = "►" if i == self._cursor else " "
            rows.append(f" {marker} {kind.value}")
        idle_marker = "►" if self._cursor == len(self._kinds) else " "
        rows.append(f" {idle_marker} (idle — build nothing)")
        self.query_one("#prod-list", Static).update("\n".join(rows))

    def on_key(self, event: events.Key) -> None:
        key = event.key
        n = len(self._kinds) + 1  # kinds + idle row
        if key == "up":
            self._cursor = (self._cursor - 1) % n
        elif key == "down":
            self._cursor = (self._cursor + 1) % n
        elif key in ("enter", "space"):
            chosen = (
                self._kinds[self._cursor]
                if self._cursor < len(self._kinds)
                else None
            )
            self.dismiss(chosen)
            return
        elif key == "escape":
            self.dismiss(self._current)  # cancel: no change
            return
        else:
            return
        event.stop()
        self._repaint()

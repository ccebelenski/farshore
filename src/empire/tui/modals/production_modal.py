"""`ProductionModal`: pick a UnitKind to build at a city.

Keyboard-first, consistent with the other in-game pickers (ChoiceModal,
ConfirmModal): ↑/↓ move, Enter/Space select, Esc cancels (no change).
Returns the chosen `UnitKind`, or `None` to build nothing (idle), via
`dismiss(result)`; Esc returns the current target unchanged.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from empire.core.unit import UnitKind

# Selectable rows: every unit kind, then "idle" (build nothing).
_KINDS: tuple[UnitKind, ...] = tuple(UnitKind)


class ProductionModal(ModalScreen[UnitKind | None]):
    """A movable-cursor list of build targets."""

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

    def __init__(self, current: UnitKind | None) -> None:
        super().__init__()
        self._current = current
        # Cursor starts on the current target (or the trailing "idle" row).
        self._cursor = (
            _KINDS.index(current) if current is not None else len(_KINDS)
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="prod-title")
            yield Static("", id="prod-list")

    def on_mount(self) -> None:
        self._repaint()

    def _repaint(self) -> None:
        current_label = self._current.value if self._current is not None else "idle"
        self.query_one("#prod-title", Static).update(
            f"build (now: {current_label}) — ↑↓ Enter · Esc cancel"
        )
        rows: list[str] = []
        for i, kind in enumerate(_KINDS):
            marker = "►" if i == self._cursor else " "
            rows.append(f" {marker} {kind.value}")
        idle_marker = "►" if self._cursor == len(_KINDS) else " "
        rows.append(f" {idle_marker} (idle — build nothing)")
        self.query_one("#prod-list", Static).update("\n".join(rows))

    def on_key(self, event: events.Key) -> None:
        key = event.key
        n = len(_KINDS) + 1  # kinds + idle row
        if key == "up":
            self._cursor = (self._cursor - 1) % n
        elif key == "down":
            self._cursor = (self._cursor + 1) % n
        elif key in ("enter", "space"):
            chosen = _KINDS[self._cursor] if self._cursor < len(_KINDS) else None
            self.dismiss(chosen)
            return
        elif key == "escape":
            self.dismiss(self._current)  # cancel: no change
            return
        else:
            return
        event.stop()
        self._repaint()

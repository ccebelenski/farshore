"""`ProductionModal`: pick a UnitKind to build at a city.

Returns the chosen `UnitKind` (or `None` to clear production) via
`dismiss(result)`.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from empire.core.unit import UnitKind


class ProductionModal(ModalScreen[UnitKind | None]):
    """List the unit kinds; the player clicks one (or 'Clear' / 'Cancel')."""

    DEFAULT_CSS = """
    ProductionModal {
        align: center middle;
    }
    ProductionModal Vertical {
        width: 30;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    ProductionModal Button {
        width: 100%;
    }
    """

    def __init__(self, current: UnitKind | None) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        label = "building: " + (self._current.value if self._current else "idle")
        with Vertical():
            yield Label(label)
            for kind in UnitKind:
                yield Button(kind.value, id=f"k-{kind.value}")
            yield Button("clear", id="clear", variant="warning")
            yield Button("cancel", id="cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "cancel":
            self.dismiss(self._current)
            return
        if bid == "clear":
            self.dismiss(None)
            return
        for kind in UnitKind:
            if bid == f"k-{kind.value}":
                self.dismiss(kind)
                return

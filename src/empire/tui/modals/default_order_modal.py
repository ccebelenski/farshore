"""`DefaultOrderModal`: pick the default order for a city's produced units.

Returns the chosen `OrderKind` via `dismiss(result)` (or `None` on cancel).
`MOVE_TO` is returned as a bare kind; the caller then runs a cursor pick to
choose the destination coordinate (it needs a target the modal can't supply).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from empire.core.city import OrderKind
from empire.core.unit import UnitKind


class DefaultOrderModal(ModalScreen["OrderKind | None"]):
    """Choose what a city does with each `kind` it produces (spec §5.3)."""

    DEFAULT_CSS = """
    DefaultOrderModal {
        align: center middle;
    }
    DefaultOrderModal Vertical {
        width: 40;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    DefaultOrderModal Button {
        width: 100%;
    }
    """

    def __init__(self, kind: UnitKind, current: OrderKind) -> None:
        super().__init__()
        self._kind = kind
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f"default order for new {self._kind.value.upper()} "
                f"(now: {self._current.value})"
            )
            yield Button("sentry (hold in city)", id="o-sentry")
            yield Button("move to… (pick a cell)", id="o-move_to")
            yield Button("attack nearest enemy", id="o-attack_nearest_enemy")
            yield Button("cancel", id="cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "cancel":
            self.dismiss(None)
            return
        for kind in OrderKind:
            if bid == f"o-{kind.value}":
                self.dismiss(kind)
                return

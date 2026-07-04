"""`DefaultOrderModal`: pick the default order for a city's produced units.

Keyboard-first, consistent with the other in-game pickers: ↑/↓ move,
Enter/Space select, Esc cancels. Returns the chosen `OrderKind` via
`dismiss(result)` (or `None` on cancel). `MOVE_TO` is returned as a bare
kind; the caller then runs a cursor pick to choose the destination cell.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from empire.core.city import OrderKind
from empire.core.unit import UnitKind

# Selectable rows, in display order, with human labels.
_ORDERS: tuple[tuple[OrderKind, str], ...] = (
    (OrderKind.SENTRY, "sentry (hold in city)"),
    (OrderKind.MOVE_TO, "move to… (pick a cell)"),
    (OrderKind.ATTACK_NEAREST_ENEMY, "attack nearest enemy"),
)


class DefaultOrderModal(ModalScreen["OrderKind | None"]):
    """Choose what a city does with each `kind` it produces (spec §5.3)."""

    DEFAULT_CSS = """
    DefaultOrderModal {
        align: center middle;
    }
    DefaultOrderModal Vertical {
        width: auto;
        min-width: 40;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $accent;
    }
    DefaultOrderModal #order-title {
        text-style: bold;
        color: $accent;
    }
    """

    def __init__(self, kind: UnitKind, current: OrderKind) -> None:
        super().__init__()
        self._kind = kind
        self._current = current
        self._cursor = next(
            (i for i, (o, _) in enumerate(_ORDERS) if o is current), 0
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="order-title")
            yield Static("", id="order-list")

    def on_mount(self) -> None:
        self._repaint()

    def _repaint(self) -> None:
        self.query_one("#order-title", Static).update(
            f"new {self._kind.value} → (now: {self._current.value}) "
            "— ↑↓ Enter · Esc cancel"
        )
        rows = [
            f" {'►' if i == self._cursor else ' '} {labelled}"
            for i, (_, labelled) in enumerate(_ORDERS)
        ]
        self.query_one("#order-list", Static).update("\n".join(rows))

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "up":
            self._cursor = (self._cursor - 1) % len(_ORDERS)
        elif key == "down":
            self._cursor = (self._cursor + 1) % len(_ORDERS)
        elif key in ("enter", "space"):
            self.dismiss(_ORDERS[self._cursor][0])
            return
        elif key == "escape":
            self.dismiss(None)
            return
        else:
            return
        event.stop()
        self._repaint()

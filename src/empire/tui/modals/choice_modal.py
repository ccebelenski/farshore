"""`ChoiceModal`: a keyboard-first pick list for one launcher setting.

Opened with tab/enter on a multi-choice row (the 8-bit "open the options"
idiom). Arrow keys move, enter/space picks, escape cancels. Returns the
chosen index via `dismiss`, or `None` on cancel.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ChoiceModal(ModalScreen[int | None]):
    """Single-column option list with a movable highlight."""

    DEFAULT_CSS = """
    ChoiceModal {
        align: center middle;
    }
    ChoiceModal Vertical {
        width: auto;
        min-width: 28;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    ChoiceModal #choice-title {
        text-style: bold;
        color: $accent;
    }
    """

    def __init__(
        self, title: str, options: tuple[str, ...], current: int = 0
    ) -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._cursor = current if 0 <= current < len(options) else 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, id="choice-title")
            yield Static("", id="choice-list")

    def on_mount(self) -> None:
        self._repaint()

    def _repaint(self) -> None:
        lines = [
            f" {'►' if i == self._cursor else ' '} {option}"
            for i, option in enumerate(self._options)
        ]
        self.query_one("#choice-list", Static).update("\n".join(lines))

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "up":
            self._cursor = (self._cursor - 1) % len(self._options)
        elif key == "down":
            self._cursor = (self._cursor + 1) % len(self._options)
        elif key in ("enter", "space"):
            self.dismiss(self._cursor)
        elif key in ("escape", "tab"):
            self.dismiss(None)
        else:
            return
        event.stop()
        self._repaint()

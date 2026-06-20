"""`CityReportModal`: a pop-up overview of your cities and their production.

Read-only: one line per own city — id, coordinates, what it's building, and the
turn the unit completes — so you don't have to walk the map city by city. Any
key (or Esc) closes it.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class CityReportModal(ModalScreen[None]):
    """A scrolling, read-only city/production list."""

    DEFAULT_CSS = """
    CityReportModal {
        align: center middle;
    }
    CityReportModal Vertical {
        width: auto;
        min-width: 48;
        max-height: 80%;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $accent;
    }
    CityReportModal #cr-title {
        text-style: bold;
        color: $accent;
    }
    """

    def __init__(self, lines: list[str]) -> None:
        super().__init__()
        self._lines = lines

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"cities ({len(self._lines)}) — any key to close", id="cr-title"
            )
            body = "\n".join(self._lines) if self._lines else "  (no cities)"
            yield Static(body, id="cr-body")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)

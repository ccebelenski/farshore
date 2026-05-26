"""`HelpModal`: keybinding cheatsheet overlay."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """Empire — keys

Cursor / movement (no unit selected: moves cursor):
  numpad 1-9     8 directions (5 = center on selection)
  hjkl           4 directions
  yubn           4 diagonals

Unit / city:
  u              select own unit at cursor
  p              set production for own city at cursor
  .              sentry selected unit (clear its queued path)
  r              reset queued path for selected unit
  Esc            deselect

Turn flow:
  e              end turn (run AI, advance)
  F2             save game
  F3             load game
  q              quit

Help:
  ?              this overlay (close with any key)
"""


class HelpModal(ModalScreen[None]):
    """Dismiss-on-any-key help overlay."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal Vertical {
        width: auto;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(_HELP_TEXT)

    def on_key(self) -> None:
        self.dismiss(None)

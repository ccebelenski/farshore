"""`HelpModal`: keybinding cheatsheet overlay.

Implementation note: Textual's `ModalScreen` needs an explicitly-sized
container or the contents collapse to zero (looks like a blank bordered
box). We use a fixed-width `Container` and a `Static` with markup off.
Dismissed by any keypress or click outside.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """Empire — keys

Cursor / movement (no unit selected: moves cursor):
  numpad 1-9       8 directions
  hjkl             4 cardinals (vi)
  arrow keys       4 cardinals

Unit / city:
  u                select own unit at cursor
  n                skip this unit (forfeit its remaining moves)
  Tab              peek next unit (current keeps its moves)
  Shift+N          previous unit (un-handles it for revision)
  p                set production for own city at cursor
  .                persistent sentry — wakes on enemy in scan range
  d                set heading — next direction key walks every turn
  g                go-to — move cursor to target, Enter to confirm
  w                wake selected unit (clear standing order)
  Esc              deselect / cancel pending mode

Moves are immediate: with a unit selected, each direction key applies
one step (combat + city capture resolve live). When the unit's move
budget is spent, the next unit auto-selects. End the turn with `e`
to let the AI play.

Turn flow:
  e                end turn (run AI, advance)
  F2               save game (writes empire-save.json)
  F3               load game
  q                quit

Help:
  ?                this overlay (any key to close)
"""


class HelpModal(ModalScreen[None]):
    """Dismiss-on-any-key help overlay."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > Container {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $accent;
    }
    HelpModal Static {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(_HELP_TEXT, markup=False)

    def on_key(self) -> None:
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)

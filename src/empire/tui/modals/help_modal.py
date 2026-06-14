"""`HelpModal`: keybinding cheatsheet overlay.

Implementation note: Textual's `ModalScreen` needs an explicitly-sized
container or the contents collapse to zero. The cheatsheet is taller than
a typical terminal, so the box is capped to the viewport (`max-height`)
and the content scrolls. Scroll keys scroll; any other key (or a click)
closes — preserving the "press anything to dismiss" feel.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """Empire — keys

Cursor / movement (no unit selected: moves cursor):
  numpad 1-9       8 directions
  hjkl             4 cardinals (vi)
  arrow keys       4 cardinals

Unit / city:
  u                select own unit at cursor
  n                skip (defer) this unit — keeps its moves, revisit later
  Tab              peek next unit (current keeps its moves)
  Shift+N          previous unit (un-handles it for revision)
  p                set production for own city at cursor
  k                set a city's default order for the units it builds
  .                sentry (defer) — wakes on enemy; 'w' to wake now
  w                wake a sentried/skipped unit back into this turn
  d                set heading — steps NOW, then walks every turn
  g                go-to target — Enter steps now, then walks
  o                unload — next direction lands the carrier's cargo
  f                bombard — warship fires at the next direction's cell
  w                wake selected unit (clear standing order)
  Esc              deselect / cancel pending mode

Moves are immediate: with a unit selected, each direction key applies
one step (combat + city capture resolve live). When the unit's move
budget is spent, the next unit auto-selects. End the turn with `e`
to let the AI play. Manually moving a unit that has a standing order
(heading / go-to) clears the order — direct control overrides it.

Standing orders never auto-attack: a unit on heading / go-to / patrol
wakes one cell short of an enemy or a city — engage with a manual step.

Cargo: step an Army onto a friendly Transport (or a Fighter onto a
Carrier) to load it. Select the carrier and press `o`, then a direction,
to unload — a unit can't unload the same turn it loaded.

Turn flow:
  e                end turn (run AI, advance)
  a                toggle auto end-turn (on by default)
  F2               save game (writes empire-save.json)
  F3               load game
  q                quit

Map reading: a red-tinged square is covered by a discovered hostile
city's artillery (Fortified Cities rules). One shot per city per round —
mass outside the red, storm together.

Help:
  ?                this overlay
  ↑ ↓ PgUp PgDn    scroll · any other key closes
"""

# Keys that scroll the overlay instead of closing it.
_SCROLL_KEYS = frozenset(
    {"up", "down", "pageup", "pagedown", "home", "end"}
)


class HelpModal(ModalScreen[None]):
    """Scrollable help overlay; scroll keys scroll, anything else closes."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > VerticalScroll {
        width: 78;
        max-width: 95%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        background: $surface;
        border: thick $accent;
    }
    HelpModal Static {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(_HELP_TEXT, markup=False)

    def on_mount(self) -> None:
        # Focus the scroll region so PgUp/PgDn/arrows reach it.
        self.query_one(VerticalScroll).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key not in _SCROLL_KEYS:
            self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)

"""`LauncherScreen`: the 8-bit-style game launcher.

One screen, three pages — MAIN (new game / load / quit), SETUP (the
option rows), LOAD (saved games). All navigation is keyboard-first, in
the old-computer idiom the design asks for:

  - up/down arrows move the highlight,
  - space cycles a row's value forward (left/right cycle either way),
  - tab (or enter) on a multi-choice row opens it as a pick list,
  - enter activates buttons ([ START ], [ BACK ], ...).

The whole menu renders as one block of text — no focus traversal, no
widget zoo — which is both the aesthetic and the simplest thing that
works. The screen owns a mutable cursor + a `GameConfig` it edits; when
the player starts or loads a game it hands off to `EmpireApp`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from empire.tui.launching import OPPONENTS, PROFILE_NAMES, GameConfig, GameLauncher
from empire.tui.modals.choice_modal import ChoiceModal

if TYPE_CHECKING:
    from empire.tui.app import EmpireApp

# FIGlet "ANSI Shadow": solid faces with box-drawing shadow edges — crisp
# letterforms at terminal cell resolution. Left-aligned with explicit padding
# (see #title CSS), so line widths don't affect alignment.
_TITLE = r"""
███████╗███╗   ███╗██████╗ ██╗██████╗ ███████╗
██╔════╝████╗ ████║██╔══██╗██║██╔══██╗██╔════╝
█████╗  ██╔████╔██║██████╔╝██║██████╔╝█████╗
██╔══╝  ██║╚██╔╝██║██╔═══╝ ██║██╔══██╗██╔══╝
███████╗██║ ╚═╝ ██║██║     ██║██║  ██║███████╗
╚══════╝╚═╝     ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝
"""
_SUBTITLE = "Wargame of the Century"


class _Page(Enum):
    MAIN = "main"
    SETUP = "setup"
    LOAD = "load"


@dataclass(frozen=True, slots=True)
class _Row:
    """One menu row: a button (`values` empty) or a multi-choice setting."""

    key: str
    label: str
    values: tuple[str, ...] = ()


_MAIN_ROWS: tuple[_Row, ...] = (
    _Row("new", "NEW GAME"),
    _Row("load", "LOAD GAME"),
    _Row("quit", "QUIT"),
)

_RULESETS: tuple[str, ...] = ("CLASSIC", "FORTIFIED CITIES")
_MAPS: tuple[str, ...] = ("CONTINENTS", "LAND BRAWL")

_SETUP_ROWS: tuple[_Row, ...] = (
    _Row("opponent", "OPPONENT", tuple(o.upper() for o in OPPONENTS)),
    _Row("ruleset", "RULESET", _RULESETS),
    _Row("map", "MAP", _MAPS),
    _Row("size", "SIZE", PROFILE_NAMES),
    _Row("seed", "SEED"),  # special: left/right adjust, space rerolls
    _Row("start", "[ START GAME ]"),
    _Row("back", "[ BACK ]"),
)


class LauncherScreen(Screen[None]):
    """Title + menus; builds a `GameConfig` and asks the app to launch it."""

    # Layout lesson (two bugs in a row): a fractional-height Vertical
    # swallows menu rows, and a width:auto Vertical collapses its
    # fraction-width children into a sliver. Fixed width + auto height +
    # full-width children is the stable combination; the screen's own
    # `align` centers the block. test_launcher_layout asserts both axes.
    CSS = """
    LauncherScreen {
        align: center middle;
    }
    LauncherScreen Vertical {
        width: 64;
        height: auto;
    }
    #title, #subtitle, #menu, #hint {
        width: 100%;
        height: auto;
    }
    #menu {
        text-align: left;
        padding-left: 6;
    }
    #title {
        color: $accent;
        text-style: bold;
        /* Explicit padding, not text-align center: per-line centering can
           shift lines whose art has trailing spaces. (64 - 46) / 2 = 9. */
        text-align: left;
        padding-left: 9;
    }
    #subtitle, #hint {
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, launcher: GameLauncher | None = None) -> None:
        super().__init__()
        import random as _random

        self._launcher = launcher if launcher is not None else GameLauncher()
        self._page: _Page = _Page.MAIN
        self._cursor: int = 0
        # A fresh random seed per launcher visit — otherwise every
        # menu-started game silently plays the same seed-0 map. The SETUP
        # page shows it; left/right nudge, space rerolls.
        self._config: GameConfig = GameConfig(seed=_random.randrange(10_000))
        self._saves: list[Path] = []
        self._load_opponent: int = 0  # index into OPPONENTS for restores
        self._notice: str = ""

    # ---- composition --------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(_TITLE, id="title")
            yield Static(_SUBTITLE, id="subtitle")
            # markup=False: row labels like "[ START GAME ]" are literal
            # text, not Rich markup tags (which silently swallow the line).
            yield Static("", id="menu", markup=False)
            yield Static("", id="hint", markup=False)

    def on_mount(self) -> None:
        self._repaint()

    # ---- the three pages as row models --------------------------------------

    def _rows(self) -> tuple[_Row, ...]:
        if self._page is _Page.MAIN:
            return _MAIN_ROWS
        if self._page is _Page.SETUP:
            return _SETUP_ROWS
        save_rows = tuple(
            _Row(f"save:{i}", self._save_label(p))
            for i, p in enumerate(self._saves)
        ) or (_Row("nosaves", "(no saved games found)"),)
        return (
            *save_rows,
            _Row("load-opponent", "OPPONENT", tuple(o.upper() for o in OPPONENTS)),
            _Row("back", "[ BACK ]"),
        )

    def _save_label(self, path: Path) -> str:
        turn = GameLauncher.peek_turn(path)
        suffix = f"  (turn {turn})" if turn is not None else ""
        return f"{path.name}{suffix}"

    def _value_of(self, row: _Row) -> str:
        if row.key == "opponent":
            return row.values[OPPONENTS.index(self._config.opponent)]
        if row.key == "ruleset":
            return row.values[1 if self._config.fortified else 0]
        if row.key == "map":
            return row.values[1 if self._config.brawl else 0]
        if row.key == "size":
            return self._config.profile_name
        if row.key == "seed":
            return str(self._config.seed)
        if row.key == "load-opponent":
            return row.values[self._load_opponent]
        return ""

    # ---- rendering -----------------------------------------------------------

    def _repaint(self) -> None:
        lines: list[str] = []
        for i, row in enumerate(self._rows()):
            marker = "►" if i == self._cursor else " "
            value = self._value_of(row)
            if value:
                lines.append(f" {marker} {row.label:<10} ◄ {value} ►")
            else:
                lines.append(f" {marker} {row.label}")
        self.query_one("#menu", Static).update("\n".join(lines))
        hints = {
            _Page.MAIN: "↑↓ move · enter select · q quit",
            _Page.SETUP: (
                "↑↓ move · space/←/→ change · tab list · "
                "enter = start/back only · esc back"
            ),
            _Page.LOAD: "↑↓ move · enter load · space/←/→ opponent · esc back",
        }
        hint = self._notice if self._notice else hints[self._page]
        self.query_one("#hint", Static).update(hint)

    # ---- input ----------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        key = event.key
        rows = self._rows()
        row = rows[self._cursor]
        self._notice = ""
        if key == "up":
            self._cursor = (self._cursor - 1) % len(rows)
        elif key == "down":
            self._cursor = (self._cursor + 1) % len(rows)
        elif key in ("left", "right", "space"):
            delta = -1 if key == "left" else 1
            self._cycle(row, delta, reroll=key == "space")
        elif key == "tab":
            self._open_choices(row)
        elif key == "enter":
            self._activate(row)
        elif key == "escape":
            self._go(_Page.MAIN)
        elif key == "q" and self._page is _Page.MAIN:
            self.app.exit()
        else:
            return
        event.stop()
        self._repaint()

    def _go(self, page: _Page) -> None:
        self._page = page
        self._cursor = 0
        if page is _Page.LOAD:
            self._saves = GameLauncher.list_saves()

    # ---- row behaviors ----------------------------------------------------------

    def _cycle(self, row: _Row, delta: int, *, reroll: bool = False) -> None:
        if row.key == "seed":
            # left/right nudge; space rerolls (an 8-bit "random").
            if reroll:
                import random as _random

                self._config = self._config.with_(seed=_random.randrange(10_000))
            else:
                self._config = self._config.with_(
                    seed=max(0, self._config.seed + delta)
                )
            return
        if not row.values:
            return  # buttons are enter-only; space never activates
        current = row.values.index(self._value_of(row))
        self._apply_choice(row, (current + delta) % len(row.values))

    def _open_choices(self, row: _Row) -> None:
        if not row.values:
            return
        current = row.values.index(self._value_of(row))

        def _picked(index: int | None) -> None:
            if index is not None:
                self._apply_choice(row, index)
            self._repaint()

        self.app.push_screen(
            ChoiceModal(title=row.label, options=row.values, current=current),
            _picked,
        )

    def _apply_choice(self, row: _Row, index: int) -> None:
        if row.key == "opponent":
            self._config = self._config.with_(opponent=OPPONENTS[index])
        elif row.key == "ruleset":
            self._config = self._config.with_(fortified=index == 1)
        elif row.key == "map":
            self._config = self._config.with_(brawl=index == 1)
        elif row.key == "size":
            self._config = self._config.with_(profile_name=PROFILE_NAMES[index])
        elif row.key == "load-opponent":
            self._load_opponent = index

    def _activate(self, row: _Row) -> None:
        """Enter means ACTIVATE — buttons only. Settings change with
        space/arrows or via tab's pick list; one key, one meaning."""
        if row.key == "new":
            self._go(_Page.SETUP)
        elif row.key == "load":
            self._go(_Page.LOAD)
        elif row.key == "quit":
            self.app.exit()
        elif row.key == "back":
            self._go(_Page.MAIN)
        elif row.key == "start":
            self._start()
        elif row.key.startswith("save:"):
            self._load(self._saves[int(row.key.split(":")[1])])
        elif row.values or row.key == "seed":
            self._notice = "space/←/→ change · tab opens the list"

    # ---- handoff to the app -------------------------------------------------------

    def _app(self) -> EmpireApp:
        from empire.tui.app import EmpireApp

        app = self.app
        assert isinstance(app, EmpireApp)
        return app

    def _start(self) -> None:
        self._notice = "generating map…"
        self._repaint()
        try:
            launched = self._launcher.build(self._config)
        except RuntimeError as exc:
            self._notice = str(exc)
            return
        self._app().start_play(launched)

    def _load(self, path: Path) -> None:
        opponent = OPPONENTS[self._load_opponent]
        try:
            launched = self._launcher.restore(path, opponent)
        except Exception as exc:  # surface bad files as a notice, not a crash
            self._notice = f"could not load {path.name}: {exc}"
            return
        self._app().start_play(launched)

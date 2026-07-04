"""`EmpireApp`: Textual app entry point. Three modes:

  - `launcher` (no game supplied): boots into `LauncherScreen` — the
    8-bit-style menu that builds a `GameConfig` (new game) or restores a
    save, then hands off here via `start_play`.
  - `play` (game supplied): human player as P1 vs the configured AI.
  - `viewer` (game supplied + auto_advance): AI on both sides, timer-driven.

The CLI wires the right thing for each mode; `python -m empire tui` is the
launcher.
"""

from __future__ import annotations

from textual.app import App

from empire.core.game import Game
from empire.core.player import Player
from empire.events.bus import EventBus
from empire.tui.human_controller import HumanController
from empire.tui.launching import LaunchedGame
from empire.tui.screens.play_screen import PlayScreen


class EmpireApp(App[None]):
    """Top-level app. Mounts the launcher or a `PlayScreen` immediately."""

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(
        self,
        game: Game | None = None,
        human_player: Player | None = None,
        human_controller: HumanController | None = None,
        event_bus: EventBus | None = None,
        auto_advance_seconds: float | None = None,
        opponent: str = "baseline",
        auto_turn: bool = True,
    ) -> None:
        super().__init__()
        # Public-ish: the test suite reads `.game.turn` to verify the engine
        # actually ticked. Treat as read-only externally. None until the
        # launcher starts a game.
        self.game: Game | None = game
        self._human = human_player
        self._human_ctrl = human_controller
        self._bus = event_bus
        self._auto = auto_advance_seconds
        self._opponent = opponent
        self._auto_turn = auto_turn

    def on_mount(self) -> None:
        if self.game is None:
            from empire.tui.screens.launcher_screen import LauncherScreen

            self.push_screen(LauncherScreen())
            return
        assert self._human is not None and self._human_ctrl is not None
        assert self._bus is not None
        screen = PlayScreen(
            game=self.game,
            human_player=self._human,
            human_controller=self._human_ctrl,
            event_bus=self._bus,
            opponent=self._opponent,
            # Viewer mode paces turns with its own interval timer; the
            # in-screen auto-turn would race it.
            auto_turn=self._auto_turn and self._auto is None,
        )
        self.push_screen(screen)
        if self._auto is not None:
            self.set_interval(self._auto, screen.action_end_turn)

    def start_play(self, launched: LaunchedGame) -> None:
        """Launcher handoff: wire the human seat and enter the playfield."""
        from empire.core.engine import refresh_player_view

        bus = EventBus()
        launched.game.event_bus = bus
        human_ctrl = HumanController()
        launched.game.attach_controller(launched.human.id, human_ctrl)
        refresh_player_view(launched.human, launched.game.map, launched.game.turn)

        self.game = launched.game
        self._human = launched.human
        self._human_ctrl = human_ctrl
        self._bus = bus
        self._opponent = launched.opponent
        self.switch_screen(
            PlayScreen(
                game=launched.game,
                human_player=launched.human,
                human_controller=human_ctrl,
                event_bus=bus,
                opponent=launched.opponent,
            )
        )

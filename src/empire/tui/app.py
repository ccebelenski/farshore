"""`EmpireApp`: Textual app entry point. Two modes:

  - `play`: human player as P1, BaselineAI as P2.
  - `viewer`: BaselineAI on both sides; auto-advances on a timer.

Construction takes a fully-built `Game` and the player whose view drives
the rendering. The CLI wires the right thing for each mode.
"""

from __future__ import annotations

from textual.app import App

from empire.core.game import Game
from empire.core.player import Player
from empire.events.bus import EventBus
from empire.tui.human_controller import HumanController
from empire.tui.screens.play_screen import PlayScreen


class EmpireApp(App[None]):
    """Top-level app. Mounts a `PlayScreen` immediately."""

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(
        self,
        game: Game,
        human_player: Player,
        human_controller: HumanController,
        event_bus: EventBus,
        auto_advance_seconds: float | None = None,
    ) -> None:
        super().__init__()
        # Public-ish: the test suite reads `.game.turn` to verify the engine
        # actually ticked. Treat as read-only externally.
        self.game: Game = game
        self._human = human_player
        self._human_ctrl = human_controller
        self._bus = event_bus
        self._auto = auto_advance_seconds

    def on_mount(self) -> None:
        screen = PlayScreen(
            game=self.game,
            human_player=self._human,
            human_controller=self._human_ctrl,
            event_bus=self._bus,
        )
        self.push_screen(screen)
        if self._auto is not None:
            self.set_interval(self._auto, screen.action_end_turn)

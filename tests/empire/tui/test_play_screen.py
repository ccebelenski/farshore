"""Headless smoke tests for `PlayScreen`. Boots `EmpireApp` via Textual's
pilot, runs scripted input, and checks state at the end. No screenshots —
the goal here is "does the game advance without crashing under TUI
control."
"""

from __future__ import annotations

from empire.ai.baseline import BaselineAI
from empire.core.engine import scan_set_for_player
from empire.core.ruleset import SMALL
from empire.events.bus import EventBus
from empire.setup import build_game
from empire.tui.app import EmpireApp
from empire.tui.human_controller import HumanController


def _build_app(seed: int = 0) -> tuple[EmpireApp, HumanController, EventBus]:
    bus = EventBus()
    game, players = build_game(SMALL, seed, p1_is_ai=False, p2_is_ai=True)
    game.event_bus = bus
    human = players[0]
    ctrl = HumanController()
    game.attach_controller(human.id, ctrl)
    game.attach_controller(players[1].id, BaselineAI())
    scanned = scan_set_for_player(human, game.map)
    human.view.update_from_scan(scanned, game.map, game.turn)
    app = EmpireApp(
        game=game,
        human_player=human,
        human_controller=ctrl,
        event_bus=bus,
    )
    return app, ctrl, bus


async def test_app_boots_and_renders() -> None:
    """Mount + initial render path. Asserts the app reached a running state
    with a screen attached — exercising the full PlayScreen.on_mount path
    (scan, log wire-up, auto-cycle). The context manager handles teardown."""
    app, _, _ = _build_app()
    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        assert app.is_running
        assert app.screen is not None


async def test_end_turn_advances_engine() -> None:
    app, _, _ = _build_app()
    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        before = app.game.turn
        await pilot.press("e")
        await pilot.pause()
        after = app.game.turn
    assert after == before + 1, f"turn did not advance: {before} -> {after}"


async def test_help_modal_opens_and_dismisses() -> None:
    app, _, _ = _build_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        # Any key dismisses HelpModal.
        await pilot.press("space")
        await pilot.pause()

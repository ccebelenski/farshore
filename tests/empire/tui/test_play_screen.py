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


def _build_app(
    seed: int = 0, *, auto_turn: bool = False
) -> tuple[EmpireApp, HumanController, EventBus]:
    # auto_turn defaults OFF in tests: scripted input must control exactly
    # when turns advance (the shipped default is ON).
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
        auto_turn=auto_turn,
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
        assert app.game is not None
        before = app.game.turn
        await pilot.press("e")
        # End-turn paints "thinking…" first and runs the engine on a short
        # timer (so the status line shows during synchronous AI turns).
        await pilot.pause(0.2)
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


async def test_auto_turn_advances_without_keypress() -> None:
    """With auto-turn on (the shipped default) and nothing needing orders,
    turns advance on their own."""
    app, _, _ = _build_app(auto_turn=True)
    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        assert app.game is not None
        before = app.game.turn
        # Auto beat (0.4s) + thinking tick (0.05s) per turn; give it ~2 turns.
        await pilot.pause(1.2)
        after = app.game.turn
    assert after > before, "auto-turn did not advance the game"


async def test_end_turn_with_doomed_army_asks_confirmation() -> None:
    """An army parked on its own city would disband (§5.4); ending the turn
    must go through a ConfirmModal instead of silently eating the unit."""
    from empire.core.identity import UnitId
    from empire.core.unit import Army
    from empire.tui.modals import ConfirmModal

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    army = Army(UnitId(900), human, capital.coord)
    game.map.place_unit(army, capital.coord)

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        before = game.turn
        # Skip the army's orders, then end the turn: the confirm must appear.
        await pilot.press("n", "e")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        # Decline: turn must NOT have advanced.
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert game.turn == before

"""Launcher TUI tests: pilot-driven navigation through the 8-bit menus.

Same style as test_play_screen — scripted keys, assert state; no
screenshots."""

from pathlib import Path

import pytest

from empire.persistence.save_manager import SaveManager
from empire.tui.app import EmpireApp
from empire.tui.launching import GameConfig, GameLauncher
from empire.tui.screens.launcher_screen import LauncherScreen
from empire.tui.screens.play_screen import PlayScreen


async def test_boots_into_launcher_when_no_game() -> None:
    app = EmpireApp()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, LauncherScreen)
        assert app.game is None


async def test_new_game_menu_cycles_options() -> None:
    app = EmpireApp()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # NEW GAME -> setup page
        screen = app.screen
        assert isinstance(screen, LauncherScreen)
        # OPPONENT row is first; space cycles baseline -> portfolio.
        await pilot.press("space")
        assert screen._config.opponent == "portfolio"  # pyright: ignore[reportPrivateUsage]
        # Down to RULESET; right selects FORTIFIED CITIES.
        await pilot.press("down", "right")
        assert screen._config.fortified  # pyright: ignore[reportPrivateUsage]
        # Down to MAP; space flips to LAND BRAWL.
        await pilot.press("down", "space")
        assert screen._config.brawl  # pyright: ignore[reportPrivateUsage]
        # Escape returns to the main page.
        await pilot.press("escape")
        assert screen._page.value == "main"  # pyright: ignore[reportPrivateUsage]


async def test_tab_opens_choice_modal() -> None:
    app = EmpireApp()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # setup page
        await pilot.press("tab")  # open OPPONENT choices
        from empire.tui.modals.choice_modal import ChoiceModal

        assert isinstance(app.screen, ChoiceModal)
        await pilot.press("down", "enter")  # pick PORTFOLIO (2nd option)
        screen = app.screen
        assert isinstance(screen, LauncherScreen)
        assert screen._config.opponent == "portfolio"  # pyright: ignore[reportPrivateUsage]


async def test_start_game_reaches_play_screen() -> None:
    app = EmpireApp()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # setup page
        # Walk down to [ START GAME ] (rows: opponent, ruleset, map, size,
        # seed, start) and press it. Defaults: baseline/classic/SMALL/seed 0.
        await pilot.press("down", "down", "down", "down", "down", "enter")
        # Map generation runs synchronously; give the handoff a beat.
        await pilot.pause(0.2)
        assert isinstance(app.screen, PlayScreen)
        assert app.game is not None
        assert any(not p.is_ai for p in app.game.players)


async def test_load_page_lists_saves_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build a quick game, save it into an empty cwd, then load via the menu.
    launched = GameLauncher().build(GameConfig(seed=1))
    save_path = tmp_path / "mygame.json"
    SaveManager().save(launched.game, save_path)
    monkeypatch.chdir(tmp_path)

    app = EmpireApp()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("down", "enter")  # LOAD GAME page
        screen = app.screen
        assert isinstance(screen, LauncherScreen)
        assert screen._saves == [save_path]  # pyright: ignore[reportPrivateUsage]
        await pilot.press("enter")  # load the highlighted save
        await pilot.pause(0.2)
        assert isinstance(app.screen, PlayScreen)
        assert app.game is not None
        assert app.game.turn == launched.game.turn


async def test_enter_on_choice_row_does_not_change_or_open() -> None:
    """Enter is activate-only: on a setting row it neither cycles the value
    nor opens the chooser (tab does that)."""
    app = EmpireApp()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # NEW GAME -> setup page (a button: fine)
        screen = app.screen
        assert isinstance(screen, LauncherScreen)
        before = screen._config  # pyright: ignore[reportPrivateUsage]
        await pilot.press("enter")  # on the OPPONENT row: must be a no-op
        assert isinstance(app.screen, LauncherScreen)  # no modal opened
        assert screen._config == before  # pyright: ignore[reportPrivateUsage]


async def test_all_setup_rows_render() -> None:
    """Every setup row appears in the RENDERED screen text.

    Two regressions live here: containers defaulting to fractional height
    clipped rows, and Rich markup ate the bracket-labelled buttons
    ("[ START GAME ]" parsed as a markup tag and vanished). Only reading
    the compositor's actual output catches both."""
    app = EmpireApp()
    async with app.run_test(size=(80, 34)) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # setup page
        await pilot.pause()
        strips = app.screen._compositor.render_strips()  # pyright: ignore[reportPrivateUsage]
        text = "\n".join(
            "".join(segment.text for segment in strip) for strip in strips
        )
        for label in (
            "OPPONENT", "RULESET", "MAP", "SIZE", "SEED",
            "[ START GAME ]", "[ BACK ]",
        ):
            assert label in text, f"menu row not rendered: {label}"

"""Settings screen tests: pilot-driven, same style as test_launcher_screen —
scripted keys / programmatic widget state, assert outcomes; no screenshots."""

from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Input, Static

from empire.config import AppConfig, ConfigStore, LlmConnection
from empire.tui.app import EmpireApp
from empire.tui.screens.launcher_screen import LauncherScreen
from empire.tui.screens.settings_screen import SettingsScreen


async def test_settings_row_opens_and_escape_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Keep the launcher-constructed store away from the real ~/.config.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = EmpireApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        # MAIN rows: new, load, settings, quit.
        await pilot.press("down", "down", "enter")
        assert isinstance(app.screen, SettingsScreen)
        # The API key field is password-masked.
        assert app.screen.query_one("#llm-api-key", Input).password
        await pilot.press("escape")
        assert isinstance(app.screen, LauncherScreen)


async def test_save_persists_through_store(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    app = EmpireApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        app.push_screen(SettingsScreen(ConfigStore(path)))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        screen.query_one("#llm-enabled", Checkbox).value = True
        screen.query_one("#llm-base-url", Input).value = "http://localhost:1234/v1"
        screen.query_one("#llm-api-key", Input).value = "sk-secret"
        screen.query_one("#llm-model", Input).value = "qwen3.5-4b"
        screen.query_one("#save", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, LauncherScreen)
    assert ConfigStore(path).load() == AppConfig(
        llm=LlmConnection(
            enabled=True,
            base_url="http://localhost:1234/v1",
            api_key="sk-secret",
            model="qwen3.5-4b",
        )
    )


async def test_cancel_discards_edits(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    original = AppConfig(llm=LlmConnection(base_url="http://keep.me/v1"))
    ConfigStore(path).save(original)
    app = EmpireApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        app.push_screen(SettingsScreen(ConfigStore(path)))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        # Fields start from the stored values.
        assert screen.query_one("#llm-base-url", Input).value == "http://keep.me/v1"
        screen.query_one("#llm-base-url", Input).value = "http://discard.me/v1"
        screen.query_one("#cancel", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, LauncherScreen)
    assert ConfigStore(path).load() == original


async def test_load_warning_surfaces_in_notice(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("{{{ not yaml", encoding="utf-8")
    app = EmpireApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        app.push_screen(SettingsScreen(ConfigStore(path)))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        notice = screen.query_one("#settings-notice", Static)
        assert "malformed" in str(notice.content)

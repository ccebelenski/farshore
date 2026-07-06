"""`SettingsScreen`: edit the app configuration (the LLM general's
model connection) and persist it via `ConfigStore`.

Reached from the launcher's main menu (SETTINGS row). Unlike the launcher's
single-block text pages, this screen needs free text entry, so it uses real
form widgets — tab/shift-tab traverse fields, the API key renders masked —
while keeping the launcher's boxed, keyboard-first look. SAVE writes through
the store and returns to the launcher; CANCEL (or escape) discards edits.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Input, Static

from empire.config import AppConfig, ConfigStore, LlmConnection


class SettingsScreen(Screen[None]):
    """A small form over `AppConfig.llm`; owns nothing but pending edits."""

    BINDINGS: ClassVar = [("escape", "cancel", "Cancel")]

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    SettingsScreen > Vertical {
        width: 64;
        height: auto;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    #settings-title {
        color: $accent;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }
    SettingsScreen .field-label {
        color: $text-muted;
        margin-top: 1;
    }
    SettingsScreen Horizontal {
        height: auto;
        margin-top: 1;
        align-horizontal: center;
    }
    SettingsScreen Button {
        margin: 0 2;
    }
    #settings-notice {
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    def __init__(self, store: ConfigStore | None = None) -> None:
        super().__init__()
        self._store = store if store is not None else ConfigStore()
        self._config = self._store.load()

    # ---- composition --------------------------------------------------------

    def compose(self) -> ComposeResult:
        llm = self._config.llm
        with Vertical():
            yield Static("SETTINGS · LLM GENERAL", id="settings-title")
            yield Checkbox("Enabled", value=llm.enabled, id="llm-enabled")
            yield Static("BASE URL (OpenAI-compatible endpoint)", classes="field-label")
            yield Input(
                value=llm.base_url,
                placeholder="http://localhost:8080/v1",
                id="llm-base-url",
            )
            yield Static("API KEY (blank for local servers)", classes="field-label")
            yield Input(value=llm.api_key, password=True, id="llm-api-key")
            yield Static("MODEL ID (blank = whatever the server reports)", classes="field-label")
            yield Input(value=llm.model, id="llm-model")
            with Horizontal():
                yield Button("SAVE", id="save", variant="primary")
                yield Button("CANCEL", id="cancel")
            yield Static(self._store.warning or "", id="settings-notice", markup=False)

    # ---- actions -------------------------------------------------------------

    def _pending(self) -> AppConfig:
        """The `AppConfig` the form currently describes."""
        return AppConfig(
            llm=LlmConnection(
                enabled=self.query_one("#llm-enabled", Checkbox).value,
                base_url=self.query_one("#llm-base-url", Input).value.strip(),
                api_key=self.query_one("#llm-api-key", Input).value.strip(),
                model=self.query_one("#llm-model", Input).value.strip(),
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        elif event.button.id == "cancel":
            self.action_cancel()

    def _save(self) -> None:
        try:
            self._store.save(self._pending())
        except OSError as exc:  # surface disk trouble as a notice, not a crash
            self.query_one("#settings-notice", Static).update(
                f"could not save {self._store.path}: {exc}"
            )
            return
        self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()

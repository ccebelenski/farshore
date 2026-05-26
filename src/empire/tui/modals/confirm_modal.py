"""`ConfirmModal`: yes/no confirmation overlay.

Returns `True` on accept (Y / Enter), `False` on cancel (N / Esc / any
other key). Used for irreversible or expensive actions — quit while a
game is in progress is the headline use case.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfirmModal(ModalScreen[bool]):
    """Single-line yes/no prompt."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > Container {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $accent;
    }
    ConfirmModal Static {
        width: 100%;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "accept", "yes"),
        Binding("enter", "accept", "yes"),
        Binding("n", "cancel", "no"),
        Binding("escape", "cancel", "no"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(f"{self._prompt}\n\n  [y]es    [n]o", markup=False)

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

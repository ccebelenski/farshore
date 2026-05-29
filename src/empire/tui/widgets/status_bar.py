"""`StatusBar`: one-line status summary at the bottom of `PlayScreen`."""

from __future__ import annotations

from collections.abc import Callable

from textual.reactive import reactive
from textual.widgets import Static

from empire.core.city import City
from empire.core.standing_order import Heading, PatrolPath, Sentry
from empire.core.unit import Unit


class StatusBar(Static):
    """Renders turn, current player, selected unit/city, and a hint string."""

    DEFAULT_CSS = """
    StatusBar {
        background: $boost;
        color: $text;
        height: 1;
        padding: 0 1;
    }
    """

    text: reactive[str] = reactive("")

    def __init__(
        self,
        provider: Callable[[], StatusState | None],
        *,
        id: str | None = None,  # noqa: A002 — Textual's id convention
    ) -> None:
        super().__init__("", id=id)
        self._provider = provider

    def refresh_text(self) -> None:
        state = self._provider()
        self.text = state.format() if state is not None else ""

    def watch_text(self, value: str) -> None:
        self.update(value)


class StatusState:
    """Inputs the StatusBar needs each frame. Built by `PlayScreen`."""

    def __init__(
        self,
        turn: int,
        player_name: str,
        selected_unit: Unit | None,
        selected_city: City | None,
        hint: str,
    ) -> None:
        self.turn = turn
        self.player_name = player_name
        self.selected_unit = selected_unit
        self.selected_city = selected_city
        self.hint = hint

    def format(self) -> str:
        parts: list[str] = [f"T{self.turn:>3d}", self.player_name]
        if self.selected_unit is not None:
            u = self.selected_unit
            parts.append(
                f"{u.kind.value.upper()}#{int(u.id):<3d}"
                f" @({u.coord.x},{u.coord.y}) hits={u.hits}/{type(u).max_hits}",
            )
            so = u.standing_order
            if isinstance(so, Heading):
                parts.append(f"order: heading {so.direction.name}")
            elif isinstance(so, PatrolPath):
                parts.append(f"order: go-to ({len(so.remaining)} cells left)")
            elif isinstance(so, Sentry):
                parts.append("order: sentry")
        if self.selected_city is not None:
            c = self.selected_city
            prod = c.production
            build = prod.building.value if prod.building is not None else "idle"
            parts.append(
                f"city#{int(c.id):<2d} @({c.coord.x},{c.coord.y}) {build} {prod.work}",
            )
        parts.append("|")
        parts.append(self.hint)
        return "  ".join(parts)

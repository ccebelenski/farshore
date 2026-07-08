"""`ProductionPanel`: a persistent city-production tile beside the board.

One row per own city — location, what it's building, turns remaining, and the
turn the unit completes — soonest-finishing first (idle cities dimmed, last).
The same data as the `c` city-report pop-up, always on screen so every build is
visible at a glance. Read-only and provider-driven, like `StatusBar`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich import box
from rich.table import Table
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from empire.core.coord import Coord


@dataclass(frozen=True, slots=True)
class ProductionRow:
    """One city's production line. `turns_left`/`eta` are None when idle."""

    coord: Coord
    city_id: int
    task: str
    turns_left: int | None
    eta: int | None


class ProductionState:
    """Everything the panel needs to draw a frame. Built by `PlayScreen`,
    already sorted soonest-first (idle last)."""

    def __init__(self, rows: list[ProductionRow]) -> None:
        self.rows = rows

    def render(self) -> Table:
        """A compact Rich table: City / Building / Left / Done. Numbers are
        right-aligned; an imminent build (≤1 turn) is bold; an idle city is
        dimmed."""
        table = Table(
            box=box.SIMPLE_HEAD,
            expand=True,
            pad_edge=False,
            header_style="bold",
        )
        table.add_column("City", justify="left", no_wrap=True)
        table.add_column("Building", justify="left", no_wrap=True)
        table.add_column("Left", justify="right", no_wrap=True)
        table.add_column("Done", justify="right", no_wrap=True)
        for row in self.rows:
            where = f"({row.coord.x},{row.coord.y})"
            if row.turns_left is None:  # idle
                table.add_row(where, "idle", "—", "—", style="dim")
                continue
            cell = "bold" if row.turns_left <= 1 else ""
            table.add_row(
                where,
                row.task,
                Text(str(row.turns_left), style=cell),
                Text(f"t{row.eta}", style=cell),
            )
        return table


class ProductionPanel(VerticalScroll):
    """A framed, scrolling production tile to the right of the board."""

    DEFAULT_CSS = """
    ProductionPanel {
        /* Content area = width - border(2) - padding(2) - scrollbar gutter,
           and it must clear the widest table row: (xx,yy) + "Battleship" +
           "Left" + "tNNN" with the inter-column gaps (~32 cols). 48 leaves
           comfortable margin so the Building column never clips. */
        width: 48;
        height: 100%;
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 0 1;
        background: $surface;
        overflow-x: hidden;
        scrollbar-size-vertical: 1;
    }
    ProductionPanel > Static {
        width: 1fr;
    }
    """

    # Read-only context: never steal focus, or the screen's letter bindings
    # (e/u/p/…) stop firing — same rule as LogPanel.
    can_focus = False

    def __init__(
        self,
        provider: Callable[[], ProductionState | None],
        *,
        id: str | None = None,  # noqa: A002 — Textual's id convention
    ) -> None:
        super().__init__(id=id)
        self._provider = provider
        self.border_title = "Production"

    def compose(self):  # type: ignore[no-untyped-def]
        yield Static(id="production-body")

    def refresh_table(self) -> None:
        state = self._provider()
        body = self.query_one("#production-body", Static)
        if state is None or not state.rows:
            body.update(Text("  no cities", style="dim"))
            return
        body.update(state.render())

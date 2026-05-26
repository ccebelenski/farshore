"""`MapWidget`: character-grid renderer for the game map with fog of war.

Renders the map cell-by-cell from a `ViewMap`-filtered perspective.
Visible cells show live terrain + units in bright color; remembered cells
show last-seen terrain in dim color (no units); unseen cells render as
blank space. A cursor coordinate, set by the parent screen, draws an
inverted-color overlay so the player knows where their selection is.

The cursor color encodes the input mode:
  - `FREE`:    yellow — direction keys move the cursor over the map.
  - `ACTIVE`:  cyan   — a unit is selected with moves remaining; direction
                        keys command the unit.
  - `IDLE`:    grey   — a unit is selected but has no moves left;
                        direction keys do nothing useful (cosmetic only).

The widget reads `Game` state via a `provider` callback rather than
holding a reference, so live mutations on the engine side update on
`refresh()` without needing to push state in.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widgets import Static

from empire.core.coord import Coord
from empire.core.map import Map
from empire.core.player import Player
from empire.core.tile import TerrainKind


class CursorMode(Enum):
    """What pressing a direction key would do, used to tint the cursor."""

    FREE = "free"      # no unit selected; cursor moves
    ACTIVE = "active"  # unit selected with moves left; unit walks
    IDLE = "idle"      # unit selected but out of moves; cosmetic only

# Symbols. Match the dump-map CLI for consistency.
_LAND = "."
_WATER = "~"
_CITY = "*"
_BORDER = "#"
_UNSEEN = " "


# Style table — single-color foreground, no background fanciness for now.
_STYLE_LAND_VISIBLE = Style(color="green")
_STYLE_LAND_REMEMBERED = Style(color="bright_black")
_STYLE_WATER_VISIBLE = Style(color="blue")
_STYLE_WATER_REMEMBERED = Style(color="bright_black")
_STYLE_CITY_NEUTRAL = Style(color="white", bold=True)
_STYLE_CITY_OWN = Style(color="cyan", bold=True)
_STYLE_CITY_ENEMY = Style(color="red", bold=True)
_STYLE_CITY_REMEMBERED = Style(color="bright_black", bold=True)
_STYLE_BORDER = Style(color="bright_black")
_STYLE_OWN_UNIT = Style(color="cyan", bold=True)
_STYLE_ENEMY_UNIT = Style(color="red", bold=True)

# Cursor tint by mode. Reverse-video so the underlying char stays readable.
_STYLE_CURSOR_FREE = Style(color="yellow", bold=True, reverse=True)
_STYLE_CURSOR_ACTIVE = Style(color="cyan", bold=True, reverse=True)
_STYLE_CURSOR_IDLE = Style(color="bright_black", bold=True, reverse=True)
_CURSOR_STYLES: dict[CursorMode, Style] = {
    CursorMode.FREE: _STYLE_CURSOR_FREE,
    CursorMode.ACTIVE: _STYLE_CURSOR_ACTIVE,
    CursorMode.IDLE: _STYLE_CURSOR_IDLE,
}


class MapWidget(Static):
    """A character-cell view of the map filtered through a player's fog of war."""

    DEFAULT_CSS = """
    MapWidget {
        background: black;
    }
    """

    def __init__(
        self,
        provider: Callable[[], MapView | None],
        *,
        id: str | None = None,  # noqa: A002 — Textual's id convention
    ) -> None:
        super().__init__("", id=id)
        self._provider = provider
        self.cursor: Coord | None = None
        self.cursor_mode: CursorMode = CursorMode.FREE

    # Textual hooks. We override render_line so each line streams without
    # building a giant string each frame.

    def render_line(self, y: int) -> Strip:
        mv = self._provider()
        if mv is None:
            return Strip.blank(self.size.width)
        if y >= mv.real_map.height:
            return Strip.blank(self.size.width)
        segments: list[Segment] = []
        for x in range(mv.real_map.width):
            c = Coord(x, y)
            segments.append(self._cell_segment(mv, c))
        return Strip(segments)

    def on_mount(self) -> None:
        mv = self._provider()
        if mv is not None:
            self.styles.width = mv.real_map.width
            self.styles.height = mv.real_map.height

    # ---- per-cell rendering -----------------------------------------------

    def _cell_segment(self, mv: MapView, c: Coord) -> Segment:
        char, style = self._cell_char_and_style(mv, c)
        if self.cursor is not None and c == self.cursor:
            return Segment(char, _CURSOR_STYLES[self.cursor_mode])
        return Segment(char, style)

    def _cell_char_and_style(self, mv: MapView, c: Coord) -> tuple[str, Style]:
        tile = mv.real_map.tile(c)
        if not tile.on_board:
            return _BORDER, _STYLE_BORDER

        is_visible = c in mv.viewer.view.visible
        is_remembered = c in mv.viewer.view.remembered

        if not is_visible and not is_remembered:
            return _UNSEEN, _STYLE_LAND_REMEMBERED

        # Visible: live state. Render units first (they overlay terrain).
        if is_visible:
            units = mv.real_map.units_at(c)
            if units:
                unit = units[0]
                style = _STYLE_OWN_UNIT if unit.owner is mv.viewer else _STYLE_ENEMY_UNIT
                return unit.symbol, style
            if tile.terrain is TerrainKind.CITY and tile.city is not None:
                if tile.city.owner is None:
                    return _CITY, _STYLE_CITY_NEUTRAL
                if tile.city.owner is mv.viewer:
                    return _CITY, _STYLE_CITY_OWN
                return _CITY, _STYLE_CITY_ENEMY
            if tile.terrain is TerrainKind.WATER:
                return _WATER, _STYLE_WATER_VISIBLE
            return _LAND, _STYLE_LAND_VISIBLE

        # Remembered only: stale state from RememberedTile.
        remembered = mv.viewer.view.remembered[c]
        if remembered.terrain is TerrainKind.CITY:
            return _CITY, _STYLE_CITY_REMEMBERED
        if remembered.terrain is TerrainKind.WATER:
            return _WATER, _STYLE_WATER_REMEMBERED
        return _LAND, _STYLE_LAND_REMEMBERED


class MapView:
    """Bundle of the rendering inputs MapWidget needs each frame.

    Decoupled from `WorldView` because the widget needs raw map access
    (border tiles, etc.) and Coord-keyed iteration — concerns that
    `WorldView` deliberately filters away.
    """

    def __init__(self, real_map: Map, viewer: Player) -> None:
        self.real_map: Map = real_map
        self.viewer: Player = viewer

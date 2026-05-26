"""`PlayScreen`: the main playfield. Composes map + status + log,
wires input, and drives the engine one turn at a time.

Input model:
  - Direction keys move the cursor when no unit is selected.
  - Press `u` to select the own unit at the cursor.
  - With a unit selected, direction keys QUEUE path steps (one per press,
    visualized in the log; the engine resolves them on end-of-turn).
  - `r` resets the queued path; `.` sentries the unit (clears its path).
  - `p` opens production for the own city under the cursor.
  - `e` ends the turn (engine runs human plan + AI players).
  - `?` opens help; `F2`/`F3` save/load; `q` quits.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen

from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove
from empire.core.city import City
from empire.core.coord import Coord, Direction
from empire.core.game import Game
from empire.core.identity import CityId, UnitId
from empire.core.player import Player
from empire.core.unit import Unit, UnitKind
from empire.events.bus import EventBus
from empire.persistence.save_manager import SaveManager
from empire.tui.human_controller import HumanController
from empire.tui.modals import HelpModal, ProductionModal
from empire.tui.widgets import LogPanel, MapView, MapWidget, StatusBar, StatusState

# Map keyboard keys to Direction. Numpad first (the design choice in the
# minimal-TUI plan), then vi-keys as a fallback.
_DIR_KEYS: dict[str, Direction] = {
    # Numpad-style (number row works too; Textual reports digits)
    "1": Direction.SW, "2": Direction.S,  "3": Direction.SE,
    "4": Direction.W,                       "6": Direction.E,
    "7": Direction.NW, "8": Direction.N,  "9": Direction.NE,
    # Vi-keys
    "h": Direction.W,  "j": Direction.S,  "k": Direction.N, "l": Direction.E,
    "y": Direction.NW, "u_vi": Direction.NE,
    "b": Direction.SW, "n_vi": Direction.SE,
    # Arrow keys (Textual's "up"/"down"/"left"/"right")
    "up": Direction.N, "down": Direction.S, "left": Direction.W, "right": Direction.E,
}


class PlayScreen(Screen[None]):
    """The playfield screen."""

    CSS = """
    PlayScreen {
        layout: vertical;
    }
    #map {
        height: 1fr;
        overflow: auto;
    }
    """

    # Bindings are a Textual class-level convention.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("question_mark", "help", "help"),
        Binding("e", "end_turn", "end turn"),
        Binding("u", "select_unit", "select unit"),
        Binding("p", "production", "production"),
        Binding("period", "sentry", "sentry"),
        Binding("r", "reset_path", "reset path"),
        Binding("escape", "deselect", "deselect"),
        Binding("f2", "save", "save"),
        Binding("f3", "load", "load"),
        Binding("q", "quit", "quit"),
        # Numpad / number row covers all 8 directions cleanly.
        *[Binding(k, f"step('{k}')", show=False) for k in "12346789"],
        # Vi cardinals (h/j/k/l). Diagonals (y/u/b/n) conflict with letter
        # commands, so vi-users use numpad or arrows for diagonals.
        *[Binding(k, f"step('{k}')", show=False) for k in "hjkl"],
        # Arrow keys
        Binding("up", "step('up')", show=False),
        Binding("down", "step('down')", show=False),
        Binding("left", "step('left')", show=False),
        Binding("right", "step('right')", show=False),
    ]

    def __init__(
        self,
        game: Game,
        human_player: Player,
        human_controller: HumanController,
        event_bus: EventBus,
    ) -> None:
        super().__init__()
        self._game = game
        self._human = human_player
        self._human_ctrl = human_controller
        self._bus = event_bus

        # Cursor: starts at the human's capital (if they own one) else origin.
        own = [c for c in game.map.cities() if c.owner is human_player]
        self._cursor: Coord = own[0].coord if own else Coord(0, 0)

        # Selection + plan state.
        self._selected_unit_id: UnitId | None = None
        self._pending_paths: dict[UnitId, list[Coord]] = {}
        self._pending_production: dict[CityId, UnitKind | None] = {}
        self._hint: str = "press ? for help"

    # ---- composition ------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield MapWidget(provider=self._map_view, id="map")
            yield StatusBar(provider=self._status_state, id="status")
            yield LogPanel(id="log")

    def on_mount(self) -> None:
        log = self.query_one(LogPanel)
        log.attach_to(self._bus)
        self._refresh_view()
        # Run an initial scan for the human (prior to first turn) so the
        # capital and its surroundings are visible. The engine does this
        # at end-of-round, but the very first render is before any round.
        from empire.core.engine import scan_set_for_player

        scanned = scan_set_for_player(self._human, self._game.map)
        self._human.view.update_from_scan(scanned, self._game.map, self._game.turn)
        self._refresh_view()

    # ---- provider callbacks for widgets -----------------------------------

    def _map_view(self) -> MapView | None:
        return MapView(real_map=self._game.map, viewer=self._human)

    def _status_state(self) -> StatusState | None:
        unit = self._selected_unit()
        city = self._city_at_cursor()
        return StatusState(
            turn=self._game.turn,
            player_name=self._human.name,
            selected_unit=unit,
            selected_city=city,
            hint=self._hint,
        )

    # ---- actions ----------------------------------------------------------

    def action_help(self) -> None:
        self.app.push_screen(HelpModal())

    def action_step(self, key: str) -> None:
        d = _DIR_KEYS.get(key)
        if d is None:
            return
        if self._selected_unit_id is None:
            # Move cursor.
            new = self._cursor.step(d)
            if self._game.map.in_bounds(new):
                self._cursor = new
        else:
            # Extend queued path for selected unit by one step.
            unit = self._selected_unit()
            if unit is None:
                self._selected_unit_id = None
                return
            path = self._pending_paths.setdefault(unit.id, [])
            head = path[-1] if path else unit.coord
            new = head.step(d)
            if self._game.map.in_bounds(new):
                path.append(new)
                self._cursor = new
                self._hint = f"queued {len(path)} step(s); 'r' to reset, 'e' to end turn"
        self._refresh_view()

    def action_select_unit(self) -> None:
        for u in self._game.map.units_at(self._cursor):
            if u.owner is self._human:
                self._selected_unit_id = u.id
                self._hint = "direction keys queue a path; 'e' end turn"
                self._refresh_view()
                return
        self._hint = "no own unit here"
        self._refresh_view()

    def action_deselect(self) -> None:
        self._selected_unit_id = None
        self._hint = "deselected"
        self._refresh_view()

    def action_sentry(self) -> None:
        if self._selected_unit_id is None:
            return
        self._pending_paths.pop(self._selected_unit_id, None)
        self._hint = "sentry (no orders)"
        self._refresh_view()

    def action_reset_path(self) -> None:
        if self._selected_unit_id is None:
            return
        self._pending_paths.pop(self._selected_unit_id, None)
        self._hint = "path cleared"
        self._refresh_view()

    def action_production(self) -> None:
        city = self._city_at_cursor()
        if city is None or city.owner is not self._human:
            self._hint = "no own city here"
            self._refresh_view()
            return

        current = self._pending_production.get(city.id, city.production.building)

        def _set_target(result: UnitKind | None) -> None:
            self._pending_production[city.id] = result
            self._hint = f"production: {result.value if result else 'idle'}"
            self._refresh_view()

        self.app.push_screen(ProductionModal(current), _set_target)

    def action_end_turn(self) -> None:
        plan = self._build_plan()
        self._human_ctrl.set_plan(plan)
        # Reset per-turn state. Production stays committed in the city via
        # the engine; we just clear our scratch dicts.
        self._pending_paths.clear()
        self._pending_production.clear()
        self._selected_unit_id = None
        self._game.run_turn()
        self._hint = f"turn {self._game.turn}"
        self._refresh_view()
        if self._game.is_over():
            winner = self._game.winner()
            self._hint = (
                f"game over — winner: {winner.name if winner else 'draw'}"
            )
            self._refresh_view()

    def action_save(self) -> None:
        path = Path("empire-save.json")
        SaveManager().save(self._game, path)
        self._hint = f"saved to {path}"
        self._refresh_view()

    def action_load(self) -> None:
        path = Path("empire-save.json")
        if not path.exists():
            self._hint = f"no save at {path}"
            self._refresh_view()
            return
        loaded = SaveManager().load(path)
        # Hot-swap: replace the game reference. Controllers reattach to the
        # loaded game's players by id (the load doesn't persist controllers).
        self._game = loaded
        # Reattach the same human controller to the loaded human player.
        human_player = self._game.player_by_id(self._human.id)
        if human_player is None:
            self._hint = "load: no matching human player"
            self._refresh_view()
            return
        self._human = human_player
        self._game.attach_controller(self._human.id, self._human_ctrl)
        # The other player(s) get a fresh BaselineAI.
        from empire.ai.baseline import BaselineAI

        for p in self._game.players:
            if p.id == self._human.id:
                continue
            self._game.attach_controller(p.id, BaselineAI())
        self._hint = f"loaded from {path}"
        self._refresh_view()

    def action_quit(self) -> None:
        self.app.exit()

    # ---- helpers ----------------------------------------------------------

    def _selected_unit(self) -> Unit | None:
        if self._selected_unit_id is None:
            return None
        return self._game.map.unit_by_id(self._selected_unit_id)

    def _city_at_cursor(self) -> City | None:
        tile = self._game.map.tile(self._cursor)
        return tile.city

    def _build_plan(self) -> TurnPlan:
        moves: list[UnitMove] = []
        for uid, cells in self._pending_paths.items():
            if not cells:
                continue
            moves.append(
                UnitMove(unit_id=uid, path=tuple((c.x, c.y) for c in cells)),
            )

        production_orders: list[ProductionOrder] = []
        for cid, kind in self._pending_production.items():
            production_orders.append(ProductionOrder(city_id=cid, target=kind))

        # Also: any own city idle (no building) gets ARMY by default. This
        # keeps captured cities productive without forcing the player to
        # micromanage every cell.
        for city in self._game.map.cities():
            if city.owner is not self._human:
                continue
            if city.id in self._pending_production:
                continue
            if city.production.building is None:
                production_orders.append(
                    ProductionOrder(city_id=city.id, target=UnitKind.ARMY),
                )

        return TurnPlan(
            production_orders=tuple(production_orders),
            moves=tuple(moves),
        )

    def _refresh_view(self) -> None:
        map_widget = self.query_one("#map", MapWidget)
        map_widget.cursor = self._cursor
        map_widget.refresh()
        self.query_one("#status", StatusBar).refresh_text()

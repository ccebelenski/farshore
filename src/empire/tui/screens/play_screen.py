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
from textual.widgets import Footer

from empire.contracts.turn_plan import ProductionOrder, TurnPlan
from empire.core.city import City
from empire.core.coord import Coord, Direction
from empire.core.engine import execute_unit_path, scan_set_for_player
from empire.core.events import CityCapturedEvent, UnitMovedEvent, UnitRemovedEvent
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
        Binding("n", "next_unit", "next unit"),
        Binding("shift+n", "prev_unit", "prev unit"),
        Binding("tab", "next_unit", "next unit"),
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
        # Moves the human has spent on each unit this turn. Reset on
        # end-turn. Used to enforce per-unit move budget across multiple
        # direction-key presses (each press = one immediate step).
        self._moves_used: dict[UnitId, int] = {}
        self._pending_production: dict[CityId, UnitKind | None] = {}
        # Set of unit IDs already given orders this turn (sentried, dead,
        # or out of moves) so the auto-cycle skips them.
        self._handled: set[UnitId] = set()
        self._hint: str = "press ? for help"

    # ---- composition ------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield MapWidget(provider=self._map_view, id="map")
            yield StatusBar(provider=self._status_state, id="status")
            yield LogPanel(id="log")
            yield Footer()

    def on_mount(self) -> None:
        log = self.query_one(LogPanel)
        log.attach_to(self._bus, self._game.map, self._human)
        # Run an initial scan for the human (prior to first turn) so the
        # capital and its surroundings are visible. The engine does this
        # at end-of-round, but the very first render is before any round.
        from empire.core.engine import scan_set_for_player

        scanned = scan_set_for_player(self._human, self._game.map)
        self._human.view.update_from_scan(scanned, self._game.map, self._game.turn)
        # Auto-select the first unit (if any) so the player doesn't have to
        # hunt for it. On turn 0 there are no units yet (capital is still
        # producing), so this is a no-op — the player presses `e` to advance.
        self._advance_to_next_unit(initial=True)
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
            # No selection: direction keys move the cursor freely.
            new = self._cursor.step(d)
            if self._game.map.in_bounds(new):
                self._cursor = new
            self._refresh_view()
            return

        unit = self._selected_unit()
        if unit is None:
            self._selected_unit_id = None
            self._refresh_view()
            return

        used = self._moves_used.get(unit.id, 0)
        budget = unit.moves_this_turn()
        if used >= budget:
            self._hint = "unit out of moves this turn"
            self._handled.add(unit.id)
            self._advance_to_next_unit()
            self._refresh_view()
            return

        target = unit.coord.step(d)
        if not self._game.map.in_bounds(target):
            self._hint = "off the map"
            self._refresh_view()
            return

        start = unit.coord
        outcome = execute_unit_path(
            unit=unit,
            path=((target.x, target.y),),
            real_map=self._game.map,
            rules=self._game.rules,
            combat_resolver=self._game.combat_resolver,
            rng=self._game.rng,
        )

        # Publish the engine's outcome events on the bus so the log picks
        # them up (engine.execute_unit_path doesn't publish; TurnManager
        # is what normally does, and we're bypassing it for immediate moves).
        unit_died = self._game.map.unit_by_id(unit.id) is None
        if outcome.steps_taken > 0 and not unit_died:
            self._bus.publish(
                UnitMovedEvent(unit_id=unit.id, from_=start, to=unit.coord),
            )
        for uid in outcome.units_destroyed:
            self._bus.publish(UnitRemovedEvent(unit_id=uid, last_coord=start))
        for cid in outcome.cities_captured:
            self._bus.publish(
                CityCapturedEvent(
                    city_id=cid,
                    new_owner_id=self._human.id,
                    previous_owner_id=None,
                ),
            )

        # Update fog: a step may have revealed (or hidden) tiles.
        scanned = scan_set_for_player(self._human, self._game.map)
        self._human.view.update_from_scan(scanned, self._game.map, self._game.turn)

        if unit_died:
            self._selected_unit_id = None
            self._handled.add(unit.id)
            self._hint = f"unit destroyed at ({start.x},{start.y})"
            self._advance_to_next_unit()
            self._refresh_view()
            return

        if outcome.steps_taken > 0:
            self._moves_used[unit.id] = used + outcome.steps_taken
            self._cursor = unit.coord

        # Done if out of moves now.
        if self._moves_used.get(unit.id, 0) >= budget:
            self._handled.add(unit.id)
            self._advance_to_next_unit()
        else:
            remaining = budget - self._moves_used.get(unit.id, 0)
            self._hint = f"{remaining} move(s) left for this unit"
        self._refresh_view()

    def action_select_unit(self) -> None:
        """Manual free-select: take the own unit under the cursor.

        Reuses any remaining moves the unit hasn't spent this turn — we
        don't refund moves the user already burned (that would defeat the
        budget). Resuming after auto-cycle skipped it is fine; the unit
        just continues from where it stopped."""
        for u in self._game.map.units_at(self._cursor):
            if u.owner is self._human:
                self._selected_unit_id = u.id
                # Un-handle so direction keys apply moves again (if any
                # budget remains).
                self._handled.discard(u.id)
                remaining = u.moves_this_turn() - self._moves_used.get(u.id, 0)
                self._hint = (
                    f"free-select: {remaining} move(s) left; "
                    f"direction keys walk, '.' sentry, Esc → cursor"
                )
                self._refresh_view()
                return
        self._hint = "no own unit here — move cursor with direction keys"
        self._refresh_view()

    def action_next_unit(self) -> None:
        # Mark the current unit handled (if any) and move on.
        if self._selected_unit_id is not None:
            self._handled.add(self._selected_unit_id)
        self._advance_to_next_unit()
        self._refresh_view()

    def action_prev_unit(self) -> None:
        # Step back to the previously-handled unit; let the user revise.
        candidates = self._units_needing_orders(include_handled=True)
        if not candidates:
            return
        current = self._selected_unit_id
        if current is None:
            target = candidates[-1]
        else:
            idx = next(
                (i for i, u in enumerate(candidates) if u.id == current),
                0,
            )
            target = candidates[(idx - 1) % len(candidates)]
        self._select_and_center(target)
        # The user is revisiting; un-mark "handled" so any new orders count.
        self._handled.discard(target.id)
        self._refresh_view()

    def action_deselect(self) -> None:
        self._selected_unit_id = None
        self._hint = "deselected — direction keys move the cursor"
        self._refresh_view()

    def action_sentry(self) -> None:
        """Skip the rest of this unit's turn — moves left are forfeit."""
        if self._selected_unit_id is None:
            return
        self._handled.add(self._selected_unit_id)
        self._advance_to_next_unit()
        self._refresh_view()

    def action_reset_path(self) -> None:
        """Legacy no-op kept for the `r` binding. Immediate moves can't be
        rolled back once applied (combat may have resolved). Press `r` is
        now a hint reminder; we leave the binding in place so muscle
        memory from path-queueing days doesn't crash."""
        if self._selected_unit_id is None:
            return
        self._hint = "moves are immediate now — no path to reset"
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
        self._pending_production.clear()
        self._moves_used.clear()
        self._selected_unit_id = None
        self._handled.clear()
        self._game.run_turn()
        if self._game.is_over():
            winner = self._game.winner()
            self._hint = (
                f"game over — winner: {winner.name if winner else 'draw'}"
            )
        else:
            # Set up the next turn: pick the first unit that needs orders.
            self._advance_to_next_unit(initial=True)
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

    def _units_needing_orders(self, *, include_handled: bool = False) -> list[Unit]:
        """Own units with moves remaining; by default skips already-handled."""
        result: list[Unit] = []
        for unit in self._game.map.all_units():
            if unit.owner is not self._human:
                continue
            if unit.moves_this_turn() <= 0:
                continue
            if not include_handled and unit.id in self._handled:
                continue
            result.append(unit)
        # Stable order: by id, so cycling is predictable.
        result.sort(key=lambda u: int(u.id))
        return result

    def _advance_to_next_unit(self, *, initial: bool = False) -> None:
        """Select the next unit that needs orders. If none, hint end-turn."""
        candidates = self._units_needing_orders()
        if not candidates:
            self._selected_unit_id = None
            self._hint = (
                "all units handled — press 'e' to end turn"
                if not initial
                else f"turn {self._game.turn}: no units to order yet — 'e' to advance"
            )
            return
        # Pick the lowest-id unit (or the next one after the current selection).
        current = self._selected_unit_id
        if current is None:
            target = candidates[0]
        else:
            try:
                idx = next(
                    i for i, u in enumerate(candidates) if int(u.id) > int(current)
                )
                target = candidates[idx]
            except StopIteration:
                target = candidates[0]
        self._select_and_center(target)
        remaining = len(candidates)
        self._hint = (
            f"{remaining} unit(s) need orders; direction keys queue path, "
            f"'n' skip, '.' sentry"
        )

    def _select_and_center(self, unit: Unit) -> None:
        self._selected_unit_id = unit.id
        self._cursor = unit.coord

    def _build_plan(self) -> TurnPlan:
        """Build the human's TurnPlan for end-of-turn.

        Moves are already applied immediately on key press, so this only
        carries production orders. Any own city without a build target
        gets ARMY by default so captured cities don't sit silent.
        """
        production_orders: list[ProductionOrder] = []
        for cid, kind in self._pending_production.items():
            production_orders.append(ProductionOrder(city_id=cid, target=kind))

        for city in self._game.map.cities():
            if city.owner is not self._human:
                continue
            if city.id in self._pending_production:
                continue
            if city.production.building is None:
                production_orders.append(
                    ProductionOrder(city_id=city.id, target=UnitKind.ARMY),
                )

        return TurnPlan(production_orders=tuple(production_orders))

    def _refresh_view(self) -> None:
        map_widget = self.query_one("#map", MapWidget)
        map_widget.cursor = self._cursor
        map_widget.refresh()
        self.query_one("#status", StatusBar).refresh_text()

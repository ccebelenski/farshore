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
from textual.containers import ScrollableContainer, Vertical
from textual.geometry import Region, Spacing
from textual.screen import Screen
from textual.widgets import Footer

from empire.contracts.turn_plan import ProductionOrder, SetOrder, TurnPlan
from empire.core.city import City, DefaultOrder, OrderKind
from empire.core.coord import Coord, Direction
from empire.core.engine import (
    BombardmentOutcome,
    MoveOutcome,
    StepOutcome,
    can_bombard,
    execute_bombardment,
    execute_unit_path,
    execute_unload,
    load_adjacent_cargo,
    refresh_player_view,
    step_would_attack,
    step_would_enter_artillery_zone,
)
from empire.core.events import UnitRemovedEvent
from empire.core.game import Game
from empire.core.identity import CityId, UnitId
from empire.core.player import Player
from empire.core.reporting import publish_move_outcome
from empire.core.standing_order import (
    Explore,
    Heading,
    Loading,
    PatrolPath,
    Sentry,
    StandingOrder,
)
from empire.core.unit import UNIT_REGISTRY, Unit, UnitKind
from empire.events.bus import EventBus
from empire.pathfinding.bfs import BFSPathfinder
from empire.pathfinding.cost import AIR, ARMY, SEA
from empire.persistence.save_manager import SaveManager
from empire.tui.human_controller import HumanController
from empire.tui.modals import (
    CityReportModal,
    ConfirmModal,
    DefaultOrderModal,
    HelpModal,
    ProductionModal,
)
from empire.tui.widgets import (
    CursorMode,
    LogPanel,
    MapView,
    MapWidget,
    StatusBar,
    StatusState,
)
from empire.tui.widgets.map_widget import _GUTTER

# Map keyboard keys to Direction. Numpad first (the design choice in the
# minimal-TUI plan), then vi-keys as a fallback.
_DIR_KEYS: dict[str, Direction] = {
    # Numpad-style (number row works too; Textual reports digits)
    "1": Direction.SW, "2": Direction.S,  "3": Direction.SE,
    "4": Direction.W,                       "6": Direction.E,
    "7": Direction.NW, "8": Direction.N,  "9": Direction.NE,
    # Vi-keys. 'k' (north) and 'l' (east) are given to command letters
    # (city-orders, load); vi users reach those directions via numpad/arrows.
    "h": Direction.W,  "j": Direction.S,
    "y": Direction.NW, "u_vi": Direction.NE,
    "b": Direction.SW, "n_vi": Direction.SE,
    # Arrow keys (Textual's "up"/"down"/"left"/"right")
    "up": Direction.N, "down": Direction.S, "left": Direction.W, "right": Direction.E,
}

# Unit kinds that can fly a patrol route ('t'): ships only.
_PATROL_ROUTE_KINDS = frozenset({
    UnitKind.PATROL, UnitKind.DESTROYER, UnitKind.SUBMARINE,
    UnitKind.TRANSPORT, UnitKind.CARRIER, UnitKind.BATTLESHIP,
})

# GOTO routes around discovered hostile-city gun rings: a danger cell costs this
# much extra, so the path detours up to ~this many cells to skip one — but it's
# finite, so a goal in/behind the ring is still reachable (intended assault).
_GOTO_DANGER_WEIGHT = 8.0


class PlayScreen(Screen[None]):
    """The playfield screen."""

    CSS = """
    PlayScreen {
        layout: vertical;
    }
    #map-scroll {
        /* Natural size when the map fits (the log's 1fr takes the surplus);
           a viewport that scrolls — following the cursor — when the map is
           bigger than the terminal (STANDARD/LARGE profiles). The max-height
           that reserves the status/footer/log-minimum rows is set
           programmatically in `_cap_map_viewport` (CSS has no calc()). */
        height: auto;
        scrollbar-size: 1 1;
    }
    """

    # Bindings are a Textual class-level convention.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("question_mark", "help", "help"),
        Binding("e", "end_turn", "end turn"),
        Binding("a", "toggle_auto", "auto-turn"),
        Binding("u", "select_unit", "select unit"),
        Binding("d", "set_heading", "set heading"),
        Binding("g", "go_to", "go-to"),
        Binding("t", "patrol_route", "patrol route"),
        Binding("v", "explore", "explore"),
        Binding("o", "unload", "unload cargo"),
        Binding("l", "loading", "load ship"),
        Binding("f", "bombard", "bombard"),
        Binding("w", "wake", "wake unit"),
        Binding("n", "next_unit", "next unit"),
        Binding("shift+n", "prev_unit", "prev unit"),
        Binding("tab", "peek_next_unit", "peek next"),
        Binding("p", "production", "production"),
        Binding("k", "city_orders", "city orders"),
        Binding("c", "city_report", "cities"),
        Binding("x", "disband", "disband"),
        Binding("full_stop", "sentry", "sentry"),
        Binding("r", "reset_path", "reset path"),
        Binding("escape", "deselect", "deselect"),
        Binding("f2", "save", "save"),
        Binding("f3", "load", "load"),
        Binding("q", "quit", "quit"),
        # Numpad / number row covers all 8 directions cleanly.
        *[Binding(k, f"step('{k}')", show=False) for k in "12346789"],
        # Vi cardinals h/j only — k (north) and l (east) are command letters
        # (city-orders, load); diagonals also defer to numpad/arrows.
        *[Binding(k, f"step('{k}')", show=False) for k in "hj"],
        # Arrow keys
        Binding("up", "step('up')", show=False),
        Binding("down", "step('down')", show=False),
        Binding("left", "step('left')", show=False),
        Binding("right", "step('right')", show=False),
        Binding("enter", "confirm", show=False),
    ]

    def __init__(
        self,
        game: Game,
        human_player: Player,
        human_controller: HumanController,
        event_bus: EventBus,
        opponent: str = "baseline",
        auto_turn: bool = True,
    ) -> None:
        super().__init__()
        self._game = game
        self._human = human_player
        self._human_ctrl = human_controller
        self._bus = event_bus
        # Which AI personality fills the non-human seats (used to reattach
        # controllers after F3 load — saves don't persist controllers).
        self._opponent = opponent
        # True while the engine is resolving a turn (the AI may think for
        # seconds); input is ignored and the status line says so.
        self._turn_running = False
        # Auto-turn: when every unit has orders (or there are none yet), the
        # turn ends itself after a short beat — no 'e' grind. 'a' toggles.
        self._auto_turn = auto_turn
        # Standing orders set this turn ('d' heading / 'g' go-to). They ride
        # the TurnPlan's set_orders so the engine activates them AFTER this
        # round's standing-orders phase — the unit already took its first
        # step immediately at set time; activating now would double-move it.
        self._pending_orders: dict[UnitId, StandingOrder] = {}

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
        # Set of unit IDs the auto-cycle should skip this turn (moved out,
        # dead, loaded, sentried, or skipped). A sentry/skip is reversible
        # until end-turn and does NOT consume the unit's move: waking or
        # revisiting it restores its full move for this turn. Auto-end still
        # fires once the queue empties.
        self._handled: set[UnitId] = set()
        # Next direction key triggers a heading-set instead of stepping.
        self._awaiting_heading: bool = False
        # Cursor mode is "pick a destination for go-to" while True.
        self._awaiting_goto_target: bool = False
        # Cursor mode is "pick a patrol endpoint" (ships) while True.
        self._awaiting_patrol_target: bool = False
        # Next direction key unloads the selected carrier's next cargo unit.
        self._awaiting_unload: bool = False
        # Next direction key fires a bombardment at that adjacent cell.
        self._awaiting_bombard: bool = False
        # When set, the cursor is picking a MOVE_TO destination for a city's
        # default order: (city_id, unit_kind).
        self._awaiting_city_order_target: tuple[CityId, UnitKind] | None = None
        self._hint: str = "press ? for help"

    # ---- composition ------------------------------------------------------

    def compose(self) -> ComposeResult:
        # The scroller must not take focus: it would grab the arrow keys from
        # the screen bindings (cursor movement). Scrolling is driven by the
        # cursor instead — see `_scroll_cursor_into_view`.
        scroller = ScrollableContainer(id="map-scroll")
        scroller.can_focus = False
        with Vertical():
            with scroller:
                yield MapWidget(provider=self._map_view, id="map")
            yield StatusBar(provider=self._status_state, id="status")
            yield LogPanel(id="log")
            yield Footer()

    def on_mount(self) -> None:
        self._cap_map_viewport()
        # First scroll must wait for layout (sizes are 0 during mount).
        self.call_after_refresh(self._scroll_cursor_into_view)
        log = self.query_one(LogPanel)
        log.attach_to(self._bus, self._game.map, self._human)
        # Run an initial scan for the human (prior to first turn) so the
        # capital and its surroundings are visible. The engine does this
        # at end-of-round, but the very first render is before any round.
        refresh_player_view(self._human, self._game.map, self._game.turn)
        # Auto-select the first unit (if any) so the player doesn't have to
        # hunt for it. On turn 0 there are no units yet (capital is still
        # producing), so this is a no-op — the player presses `e` to advance.
        self._advance_to_next_unit(initial=True)
        self._refresh_view()

    # ---- provider callbacks for widgets -----------------------------------

    def _map_view(self) -> MapView | None:
        return MapView(
            real_map=self._game.map,
            viewer=self._human,
            artillery_range=self._game.rules.city_artillery_range,
        )

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
        # Heading-set mode: next direction sets a heading instead of walking.
        if self._awaiting_heading and self._selected_unit_id is not None:
            self._set_heading(self._selected_unit_id, d)
            return
        # Go-to / patrol / city-order-target modes: cursor movement.
        if (
            self._awaiting_goto_target
            or self._awaiting_patrol_target
            or self._awaiting_city_order_target is not None
        ):
            new = self._cursor.step(d)
            if self._game.map.in_bounds(new):
                self._cursor = new
            self._refresh_view()
            return
        # Unload mode: the next direction key lands a cargo unit ashore.
        if self._awaiting_unload and self._selected_unit_id is not None:
            self._do_unload(self._selected_unit_id, d)
            return
        # Bombard mode: the next direction key fires at that adjacent cell.
        if self._awaiting_bombard and self._selected_unit_id is not None:
            self._do_bombard(self._selected_unit_id, d)
            return
        if self._selected_unit_id is None:
            # No selection: direction keys move the cursor freely.
            new = self._cursor.step(d)
            if self._game.map.in_bounds(new):
                self._cursor = new
            self._hint = self._cursor_context_hint()
            self._refresh_view()
            return

        unit = self._selected_unit()
        if unit is None:
            self._selected_unit_id = None
            self._refresh_view()
            return

        if unit.kind is UnitKind.SATELLITE:
            self._launch_satellite(unit, d)
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
        outcome, unit_died = self._execute_step(unit, target)

        # Taking direct control revokes a standing order — live (set on a
        # previous turn) or pending (set this turn): without this, a
        # manually-stepped unit with a heading/go-to would move AGAIN in the
        # engine's standing-orders phase — a free extra move.
        if outcome.steps_taken > 0 and not unit_died:
            had_order = (
                unit.standing_order is not None
                or self._pending_orders.pop(unit.id, None) is not None
            )
            unit.standing_order = None
            if had_order:
                self._hint = "standing order cleared (manual move)"

        # Capture: the army was consumed (§4.5) but this is a win, not a
        # loss — prompt for the new city's production right away rather than
        # letting it sit on a silent default.
        if outcome.last_outcome is StepOutcome.CAPTURED and outcome.cities_captured:
            self._selected_unit_id = None
            self._handled.add(unit.id)
            captured = self._game.map.city_by_id(outcome.cities_captured[-1])
            if captured is not None:
                self._prompt_capture_production(captured)
            else:
                self._advance_to_next_unit()
            self._refresh_view()
            return

        if unit_died:
            self._selected_unit_id = None
            self._handled.add(unit.id)
            self._hint = f"unit destroyed at ({start.x},{start.y})"
            self._advance_to_next_unit()
            self._refresh_view()
            return

        # Loading: the unit boarded a friendly carrier and is now off the map.
        if outcome.last_outcome is StepOutcome.LOADED:
            carrier = next(
                (o for o in self._game.map.units_at(unit.coord) if o.owner is self._human),
                None,
            )
            label = (
                f"{carrier.kind.value.upper()}#{int(carrier.id)}"
                if carrier is not None
                else "carrier"
            )
            cargo_n = len(carrier.cargo) if carrier is not None else 0
            cap = carrier.effective_capacity() if carrier is not None else 0
            self._handled.add(unit.id)
            self._selected_unit_id = None
            self._hint = f"loaded onto {label} ({cargo_n}/{cap})"
            # If this filled a carrier waiting in loading mode, wake it now
            # (the engine also wakes full loaders at turn start; this gives
            # immediate in-turn feedback).
            if (
                carrier is not None
                and isinstance(carrier.standing_order, Loading)
                and cargo_n >= cap
            ):
                carrier.standing_order = None
                self._handled.discard(carrier.id)
                self._hint = f"{label} fully loaded ({cargo_n}/{cap}) — woken"
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

    def _execute_step(self, unit: Unit, target: Coord) -> tuple[MoveOutcome, bool]:
        """Run one engine step for `unit` toward `target`, publish the
        outcome events, and update fog. Shared by manual steps and the
        immediate first step of a freshly set movement order. Returns
        (outcome, unit_died)."""
        start = unit.coord
        outcome = execute_unit_path(
            unit=unit,
            path=((target.x, target.y),),
            real_map=self._game.map,
            rules=self._game.rules,
            combat_resolver=self._game.combat_resolver,
            rng=self._game.rng,
        )

        # Publish the outcome events via the shared reporter (execute_unit_path
        # doesn't publish; TurnManager normally does, and we're bypassing it
        # for immediate moves). City artillery is not part of the step — it
        # fires in the deferred phase at end of the human's segment, AFTER the
        # fog update below, so the fort is discovered before it fires (§4.7).
        unit_died = self._game.map.unit_by_id(unit.id) is None
        publish_move_outcome(
            self._bus, self._game.map, self._human.id, unit.id, start, outcome
        )

        # Update fog: a step may have revealed (or hidden) tiles.
        refresh_player_view(self._human, self._game.map, self._game.turn)
        return outcome, unit_died

    def _launch_satellite(self, sat: Unit, d: Direction) -> None:
        """Direction key on a satellite: launch it onto its one-way orbit
        (spec §2.4). The heading is chosen exactly once — after launch the
        satellite wraps around the map on its own until its fuel runs out
        and it crashes; there is no manual control, ever."""
        if sat.orbit_direction is not None:
            self._hint = "satellite is in orbit — no manual control"
            self._refresh_view()
            return
        sat.orbit_direction = d
        self._handled.add(sat.id)
        self._hint = (
            f"satellite launched {d.name} — orbits until its fuel "
            f"({sat.range} turns) runs out"
        )
        self._advance_to_next_unit()
        self._refresh_view()

    def _order_first_step(
        self, unit: Unit, target: Coord
    ) -> tuple[bool, bool, bool]:
        """Setting a movement order moves the unit immediately (player
        expectation: 'setting the heading moves the unit'). Executes the
        order's first step now if budget remains. The order itself is NOT
        live yet — the caller queues it via the TurnPlan's set_orders, which
        the engine applies AFTER this round's standing-orders phase (the
        designed mechanism that prevents the same-round double step).

        Returns (stepped, unit_alive, block) where `block` is None, "attack",
        or "artillery". A move order never auto-attacks (first step would
        resolve as combat / a capture → "attack") and never sleepwalks into a
        hostile city's gun range (first step would enter the red zone →
        "artillery", spec §4.7). Both are deliberate manual steps, never an
        order side effect — the same rule the engine applies to later steps."""
        used = self._moves_used.get(unit.id, 0)
        if used >= unit.moves_this_turn():
            return False, True, None  # out of moves; the order starts next round
        if not self._game.map.in_bounds(target):
            return False, True, None
        if step_would_attack(unit, target, self._game.map):
            return False, True, "attack"
        if step_would_enter_artillery_zone(
            unit, target, self._game.map, self._game.rules
        ):
            return False, True, "artillery"
        outcome, unit_died = self._execute_step(unit, target)
        if unit_died:
            self._selected_unit_id = None
            self._handled.add(unit.id)
            return outcome.steps_taken > 0, False, None
        if outcome.steps_taken > 0:
            self._moves_used[unit.id] = used + outcome.steps_taken
            self._cursor = unit.coord
            return True, True, None
        return False, True, None

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
        """`n`: skip (defer) this unit — pass it for now, keep its moves.

        Deferring is not finishing: the unit keeps its unspent moves and can
        be revisited (Shift+N / free-select) or woken later this turn, and it
        holds the turn open against auto-end."""
        if self._selected_unit_id is not None:
            self._handled.add(self._selected_unit_id)
        self._advance_to_next_unit()
        self._refresh_view()

    def action_peek_next_unit(self) -> None:
        """Tab: jump to the next unit needing orders without committing
        the current one. Its remaining moves are preserved so the player
        can return to it later this turn."""
        candidates = self._units_needing_orders()
        if not candidates:
            self._hint = "no other units need orders — staying here"
            self._refresh_view()
            return
        current = self._selected_unit_id
        if current is None:
            target = candidates[0]
        else:
            # Find the next candidate with a higher id than current; wrap.
            try:
                idx = next(
                    i for i, u in enumerate(candidates) if int(u.id) > int(current)
                )
                target = candidates[idx]
            except StopIteration:
                target = candidates[0]
            if target.id == current:
                self._hint = "this is the only unit needing orders"
                self._refresh_view()
                return
        self._select_and_center(target)
        self._hint = f"peeked — {len(candidates)} unit(s) need orders; previous unit kept its moves"
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
        # (sentry/skip didn't consume the move, so it's fully restorable.)
        self._handled.discard(target.id)
        self._refresh_view()

    def action_deselect(self) -> None:
        if (
            self._awaiting_heading
            or self._awaiting_goto_target
            or self._awaiting_patrol_target
            or self._awaiting_unload
            or self._awaiting_bombard
            or self._awaiting_city_order_target is not None
        ):
            self._awaiting_heading = False
            self._awaiting_goto_target = False
            self._awaiting_patrol_target = False
            self._awaiting_unload = False
            self._awaiting_bombard = False
            self._awaiting_city_order_target = None
            self._hint = "cancelled"
            self._refresh_view()
            return
        self._selected_unit_id = None
        self._hint = self._cursor_context_hint()
        self._refresh_view()

    def action_sentry(self) -> None:
        """Put the selected unit on persistent sentry (skip this and future turns)."""
        if self._selected_unit_id is None:
            return
        uid = self._selected_unit_id
        unit = self._game.map.unit_by_id(uid)
        if unit is not None and unit.kind is UnitKind.SATELLITE:
            self._hint = "satellites can't hold — press a direction to launch"
            self._refresh_view()
            return
        self._handled.add(uid)
        if unit is not None:
            unit.standing_order = Sentry()
        self._hint = "sentry — wakes on enemy in scan range ('w' to wake now)"
        self._advance_to_next_unit()
        self._refresh_view()

    def action_set_heading(self) -> None:
        """Arm heading-set: the next direction key sets a Heading instead of walking."""
        if self._selected_unit_id is None:
            self._hint = "select a unit first ('u' or auto-cycle)"
            self._refresh_view()
            return
        unit = self._selected_unit()
        if unit is not None and unit.kind is UnitKind.SATELLITE:
            self._hint = "satellites take no orders — press a direction to launch"
            self._refresh_view()
            return
        self._awaiting_heading = True
        self._hint = "heading: press a direction (Esc to cancel)"
        self._refresh_view()

    def action_go_to(self) -> None:
        """Arm go-to: cursor picks a destination; Enter confirms."""
        if self._selected_unit_id is None:
            self._hint = "select a unit first"
            self._refresh_view()
            return
        unit = self._selected_unit()
        if unit is None:
            self._refresh_view()
            return
        if unit.kind is UnitKind.SATELLITE:
            self._hint = "satellites take no orders — press a direction to launch"
            self._refresh_view()
            return
        self._awaiting_goto_target = True
        self._cursor = unit.coord
        self._hint = "go-to: direction keys move cursor; Enter to confirm, Esc to cancel"
        self._refresh_view()

    def action_patrol_route(self) -> None:
        """Arm patrol-route (ships): cursor picks the far endpoint; Enter
        confirms. The ship shuttles start<->endpoint continuously until woken
        — manually ('w') or by a wake event (enemy in scan, discovery,
        artillery ring)."""
        if self._selected_unit_id is None:
            self._hint = "select a ship first"
            self._refresh_view()
            return
        unit = self._selected_unit()
        if unit is None:
            self._refresh_view()
            return
        if unit.kind not in _PATROL_ROUTE_KINDS:
            self._hint = "patrol routes are for ships"
            self._refresh_view()
            return
        self._awaiting_patrol_target = True
        self._cursor = unit.coord
        self._hint = (
            "patrol: cursor to the far endpoint; Enter to confirm, Esc to cancel"
        )
        self._refresh_view()

    def action_explore(self) -> None:
        """Put the selected unit (army/ship/fighter) into Explore: it reveals
        unexplored tiles autonomously — shore first — until woken by contact,
        a discovery, 'w', or running out of reachable frontier."""
        if self._selected_unit_id is None:
            self._hint = "select a unit first"
            self._refresh_view()
            return
        unit = self._selected_unit()
        if unit is None:
            self._refresh_view()
            return
        if unit.kind is UnitKind.SATELLITE:
            self._hint = "satellites take no orders — press a direction to launch"
            self._refresh_view()
            return
        if unit.carried_by is not None:
            self._hint = "aboard a carrier — unload it first"
            self._refresh_view()
            return
        unit.standing_order = Explore()
        self._handled.add(unit.id)
        self._hint = (
            "exploring — reveals unknown tiles (shore first); wakes on "
            "contact/discovery, 'w', or when nothing is left to explore"
        )
        self._advance_to_next_unit()
        self._refresh_view()

    def action_unload(self) -> None:
        """Arm unload: the next direction key lands the carrier's next cargo unit."""
        if self._selected_unit_id is None:
            self._hint = "select a carrier first"
            self._refresh_view()
            return
        unit = self._selected_unit()
        if unit is None or not unit.cargo:
            self._hint = "selected unit has no cargo to unload"
            self._refresh_view()
            return
        self._awaiting_unload = True
        self._hint = (
            f"unload ({len(unit.cargo)} aboard): press a direction "
            f"toward the destination cell (Esc to cancel)"
        )
        self._refresh_view()

    def action_loading(self) -> None:
        """'l': put the selected carrier into loading mode.

        Snaps any ADJACENT eligible cargo aboard immediately, then — if room
        remains — holds position (a Loading sentry) waiting for more cargo to
        walk/fly aboard on its own; the engine wakes it the moment it fills.
        Units that aren't already adjacent are NOT pulled — they board by
        moving onto the carrier as normal."""
        if self._selected_unit_id is None:
            self._hint = "select a carrier first ('u')"
            self._refresh_view()
            return
        carrier = self._selected_unit()
        if carrier is None or type(carrier).cargo_kind is None:
            self._hint = "not a carrier — only transports/carriers can load"
            self._refresh_view()
            return

        snapped = self._snap_adjacent_cargo(carrier)
        cap = carrier.effective_capacity()
        if len(carrier.cargo) >= cap:
            self._hint = f"carrier full ({len(carrier.cargo)}/{cap})"
            # Full now: no point waiting; leave it free to move/act.
            self._advance_to_next_unit()
            self._refresh_view()
            return

        carrier.standing_order = Loading()
        self._handled.add(carrier.id)  # skipped by the cycle; waits across turns
        note = f"loaded {snapped} adjacent, " if snapped else ""
        self._hint = (
            f"loading mode: {note}{len(carrier.cargo)}/{cap} aboard — "
            "holds until full ('w' to wake)"
        )
        self._advance_to_next_unit()
        self._refresh_view()

    def _snap_adjacent_cargo(self, carrier: Unit) -> int:
        """Load eligible adjacent units aboard `carrier` now, until full.
        Delegates to the shared engine loader (the same one the per-turn
        Loading sweep uses) so the set-time snap and the sweep can't drift;
        here we also drop boarded units from the auto-cycle."""
        boarded = load_adjacent_cargo(carrier, self._game.map)
        for cid in boarded:
            self._handled.discard(cid)  # aboard now, off the cycle
        return len(boarded)

    def action_bombard(self) -> None:
        """Arm bombardment: the next direction key fires at that adjacent cell."""
        if self._selected_unit_id is None:
            self._hint = "select a warship first"
            self._refresh_view()
            return
        unit = self._selected_unit()
        if unit is None or not can_bombard(unit):
            self._hint = "can't bombard — needs a Battleship/Destroyer/Patrol with 2+ HP"
            self._refresh_view()
            return
        self._awaiting_bombard = True
        self._hint = "bombard: press a direction toward the adjacent target (Esc to cancel)"
        self._refresh_view()

    def _do_bombard(self, ship_id: UnitId, d: Direction) -> None:
        """Fire one salvo from the selected ship at the adjacent cell in `d`."""
        self._awaiting_bombard = False
        ship = self._game.map.unit_by_id(ship_id)
        if ship is None:
            self._refresh_view()
            return
        target = ship.coord.step(d)
        result = execute_bombardment(
            ship=ship,
            target=target,
            real_map=self._game.map,
            rules=self._game.rules,
            combat_resolver=self._game.combat_resolver,
            rng=self._game.rng,
        )
        fired = result.outcome in (
            BombardmentOutcome.TARGET_DESTROYED,
            BombardmentOutcome.TARGET_SUNK,
            BombardmentOutcome.ATTACKER_SUNK,
        )
        if not fired:
            # No salvo spent (ineligible / out of range / empty cell).
            self._hint = f"can't bombard there ({result.outcome.value})"
            self._refresh_view()
            return

        if result.target_id is not None:
            self._bus.publish(
                UnitRemovedEvent(unit_id=result.target_id, last_coord=target)
            )
        if result.attacker_destroyed:
            self._bus.publish(UnitRemovedEvent(unit_id=ship.id, last_coord=ship.coord))

        # The salvo is the ship's action for the turn.
        self._handled.add(ship.id)
        alive = self._game.map.unit_by_id(ship.id)
        if alive is not None:
            self._moves_used[ship.id] = alive.moves_this_turn()
        refresh_player_view(self._human, self._game.map, self._game.turn)
        if result.outcome is BombardmentOutcome.ATTACKER_SUNK:
            self._hint = f"bombardment lost the duel at ({target.x},{target.y})"
        elif alive is not None:
            self._hint = f"bombarded ({target.x},{target.y}); ship now at {alive.hits} HP"
        self._selected_unit_id = None
        self._advance_to_next_unit()
        self._refresh_view()

    def action_wake(self) -> None:
        """Wake the own unit under the CURSOR (point-and-wake), or the
        selected unit if the cursor isn't on one. Cursor-first is what makes
        waking several units work: move cursor, 'w', move cursor, 'w' — no
        select step, so the cursor keeps moving."""
        unit = next(
            (u for u in self._game.map.units_at(self._cursor) if u.owner is self._human),
            None,
        )
        if unit is None:
            unit = self._selected_unit()
        if unit is None:
            self._hint = "no unit here to wake — put the cursor on one"
            self._refresh_view()
            return
        unit.standing_order = None
        self._pending_orders.pop(unit.id, None)  # cancel an order set this turn
        self._handled.discard(unit.id)
        # A woken unit that never actually moved gets its full turn back —
        # sentry/skip spend no moves, so _moves_used is already 0 for it.
        moves_left = unit.moves_this_turn() - self._moves_used.get(unit.id, 0)
        self._hint = f"woke {unit.kind.value}#{int(unit.id)} — {moves_left} move(s)"
        self._refresh_view()

    def action_confirm(self) -> None:
        """Enter key: confirm whatever modal-mode is active (go-to / city order)."""
        if self._awaiting_city_order_target is not None:
            city_id, kind = self._awaiting_city_order_target
            self._awaiting_city_order_target = None
            city = self._game.map.city_by_id(city_id)
            if city is not None:
                city.default_orders[kind] = DefaultOrder(OrderKind.MOVE_TO, self._cursor)
                self._hint = (
                    f"new {kind.value}: move-to ({self._cursor.x},{self._cursor.y})"
                )
            self._refresh_view()
            return
        if self._awaiting_patrol_target:
            self._confirm_patrol_route()
            return
        if not self._awaiting_goto_target:
            return
        unit = self._selected_unit()
        if unit is None:
            self._awaiting_goto_target = False
            self._refresh_view()
            return
        path = self._build_goto_path(unit, self._cursor)
        self._awaiting_goto_target = False
        if path is None or len(path) == 0:
            self._hint = "no path to that cell"
            self._refresh_view()
            return
        # Step the first cell now; the REMAINDER goes live via the plan's
        # set_orders so the engine walks it from the next round onward.
        stepped, alive, block = self._order_first_step(unit, path[0])
        if block == "attack":
            # The route starts into an enemy/city: a go-to never auto-attacks.
            # Leave the unit selected so the player can step in deliberately.
            self._hint = "go-to blocked by an enemy ahead — step in to attack"
            self._refresh_view()
            return
        if block == "artillery":
            # The route starts into a hostile city's gun range: a go-to never
            # walks into the guns. Leave the unit at the edge for the player.
            self._hint = "go-to stops at a hostile city's gun range — step in deliberately"
            self._refresh_view()
            return
        self._handled.add(unit.id)
        if not alive:
            self._hint = "unit lost on the first go-to step"
        else:
            tail = tuple(path[1:]) if stepped else tuple(path)
            if tail:
                self._pending_orders[unit.id] = PatrolPath.new(tail)
            self._hint = (
                f"go-to set: {len(tail)} cells queued"
                + (" (stepped)" if stepped else "")
            )
        self._advance_to_next_unit()
        self._refresh_view()

    def _confirm_patrol_route(self) -> None:
        """Enter on a patrol endpoint: build the round trip and set it as a
        LOOPING standing order — the ship shuttles start<->endpoint until
        woken. The cycle's last cell is the start itself, adjacent to its
        first cell, so the re-armed pass always begins with a legal step."""
        self._awaiting_patrol_target = False
        unit = self._selected_unit()
        if unit is None:
            self._refresh_view()
            return
        start = unit.coord
        forward = self._build_goto_path(unit, self._cursor)
        if forward is None or len(forward) == 0:
            self._hint = "no sea path to that cell"
            self._refresh_view()
            return
        # Round trip: out along the path, back along the same path to start.
        back = tuple(reversed((start, *forward[:-1])))
        cycle = (*forward, *back)
        # Step the first cell now (player expectation, same as go-to); the
        # remainder goes live via set_orders after this round's phase.
        stepped, alive, block = self._order_first_step(unit, forward[0])
        if block == "attack":
            self._hint = "patrol blocked by an enemy ahead — step in to attack"
            self._refresh_view()
            return
        if block == "artillery":
            self._hint = "patrol would enter a city's gun range — pick another route"
            self._refresh_view()
            return
        self._handled.add(unit.id)
        if not alive:
            self._hint = "ship lost on the first patrol step"
        else:
            remaining = cycle[1:] if stepped else cycle
            self._pending_orders[unit.id] = PatrolPath(
                remaining=remaining, original=cycle, loop=True
            )
            self._hint = (
                f"patrol set: ({start.x},{start.y})<->({self._cursor.x},{self._cursor.y})"
                f" — loops until woken ('w')"
            )
        self._advance_to_next_unit()
        self._refresh_view()

    def action_reset_path(self) -> None:
        """No-op kept so the `r` binding doesn't crash: moves are immediate
        and can't be rolled back once applied (combat may have resolved)."""
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
        self._open_production(city)

    def _buildable_kinds(self, city: City) -> tuple[UnitKind, ...]:
        """Unit kinds this city may build — ships only at a port (§3.2)."""
        from empire.core.engine import city_can_produce

        return tuple(
            k for k in UnitKind
            if city_can_produce(k, city.coord, self._game.map)
        )

    def _open_production(self, city: City) -> None:
        current = self._pending_production.get(city.id, city.production.building)

        def _set_target(result: UnitKind | None) -> None:
            self._pending_production[city.id] = result
            self._hint = f"production: {result.value if result else 'idle'}"
            self._refresh_view()

        self.app.push_screen(
            ProductionModal(current, self._buildable_kinds(city)), _set_target
        )

    def _prompt_capture_production(self, city: City) -> None:
        """Open the production picker for a just-captured city, and only
        advance to the next unit (or auto-end) once it's dismissed — so an
        auto-turn beat can't fire the turn out from under the open modal."""
        current = self._pending_production.get(city.id, city.production.building)

        def _done(result: UnitKind | None) -> None:
            self._pending_production[city.id] = result
            self._hint = (
                f"new city building {result.value}"
                if result is not None
                else "new city left idle"
            )
            self._advance_to_next_unit()
            self._refresh_view()

        self.app.push_screen(
            ProductionModal(current, self._buildable_kinds(city)), _done
        )

    def action_city_orders(self) -> None:
        """Set the default order applied to units this city produces (spec §5.3)."""
        city = self._city_at_cursor()
        if city is None or city.owner is not self._human:
            self._hint = "no own city here"
            self._refresh_view()
            return
        # Configure the order for the kind the city is currently building
        # (Army is a sensible fallback when idle).
        kind = city.production.building or UnitKind.ARMY
        current = city.default_order_for(kind).kind

        def _set_order(result: OrderKind | None) -> None:
            if result is None:
                self._hint = "city orders unchanged"
                self._refresh_view()
                return
            if result is OrderKind.MOVE_TO:
                # Hand off to a cursor pick for the destination cell.
                self._awaiting_city_order_target = (city.id, kind)
                self._cursor = city.coord
                self._hint = (
                    f"{kind.value} move-to: pick a cell with direction keys, "
                    f"Enter to confirm (Esc cancels)"
                )
                self._refresh_view()
                return
            city.default_orders[kind] = DefaultOrder(result)
            self._hint = f"new {kind.value}: {result.value}"
            self._refresh_view()

        self.app.push_screen(DefaultOrderModal(kind, current), _set_order)

    def action_city_report(self) -> None:
        """Pop up a read-only overview of every own city and its production ETA,
        soonest-finishing first, so you don't have to walk the map city by city."""
        rows: list[tuple[int, str]] = []
        for city in self._game.map.cities():
            if city.owner is not self._human:
                continue
            x, y = city.coord.x, city.coord.y
            # Reflect a queued (not-yet-applied) production change if any.
            building = self._pending_production.get(city.id, city.production.building)
            if building is None:
                rows.append((10**9, f"  city#{int(city.id)} ({x},{y}): idle"))
                continue
            build_time = UNIT_REGISTRY[building].build_time
            # Accumulated work only counts toward the current target; a queued
            # switch starts effectively from this turn's progress.
            work = city.production.work if building is city.production.building else 0
            left = max(0, build_time - work)
            eta = self._game.turn + left
            rows.append(
                (
                    eta,
                    f"  city#{int(city.id)} ({x},{y}): {building.value} "
                    f"— done t{eta} ({left} left)",
                )
            )
        rows.sort(key=lambda r: r[0])  # soonest first; idle (sentinel) last
        self.app.push_screen(CityReportModal([line for _, line in rows]))

    def action_disband(self) -> None:
        """Deliberately scrap the selected (or hovered) own unit, after a
        confirm. A loaded carrier warns that the cargo goes down with it."""
        unit = self._selected_unit() or next(
            (u for u in self._game.map.units_at(self._cursor) if u.owner is self._human),
            None,
        )
        if unit is None or unit.owner is not self._human:
            self._hint = "no own unit to disband (select or hover one)"
            self._refresh_view()
            return

        label = f"{unit.kind.value} #{int(unit.id)}"
        cargo_n = len(unit.cargo)
        if cargo_n:
            prompt = f"Disband {label} AND the {cargo_n} unit(s) aboard? (Y/N)"
        else:
            prompt = f"Disband {label}? (Y/N)"
        uid = unit.id

        def _done(confirmed: bool | None) -> None:
            if not confirmed:
                self._hint = "disband cancelled"
                self._refresh_view()
                return
            u = self._game.map.unit_by_id(uid)
            if u is not None:
                last = u.coord
                self._game.map.remove_unit(u)
                self._bus.publish(UnitRemovedEvent(unit_id=uid, last_coord=last))
            self._handled.add(uid)
            if self._selected_unit_id == uid:
                self._selected_unit_id = None
            self._hint = f"disbanded {label}"
            self._advance_to_next_unit()
            self._refresh_view()

        self.app.push_screen(ConfirmModal(prompt), _done)

    def action_toggle_auto(self) -> None:
        self._auto_turn = not self._auto_turn
        self._hint = f"auto end-turn {'ON' if self._auto_turn else 'OFF'}"
        self._refresh_view()

    def action_end_turn(self) -> None:
        if self._turn_running:
            return
        # §5.4 tripwire: an army left in a friendly city disbands at the
        # start of your next segment. Don't let that happen on a reflexive
        # 'e' (or an auto-end) — confirm first.
        doomed = self._doomed_garrison_units()
        if doomed:
            names = ", ".join(
                f"{u.kind.value}#{int(u.id)} at ({u.coord.x},{u.coord.y})"
                for u in doomed[:3]
            )
            more = "" if len(doomed) <= 3 else f" (+{len(doomed) - 3} more)"

            def _on_answer(confirmed: bool | None) -> None:
                if confirmed:
                    self._begin_end_turn()
                else:
                    # Put the player straight onto the unit they declined to
                    # lose — no hunting for it.
                    # Refund a skip's forfeited budget: 'n' is a UI gesture,
                    # not a physical act, and they just changed their mind.
                    rescued = doomed[0]
                    self._handled.discard(rescued.id)
                    self._moves_used.pop(rescued.id, None)
                    self._select_and_center(rescued)
                    self._hint = (
                        f"{rescued.kind.value}#{int(rescued.id)} selected — "
                        "move it off the city (or sentry to accept the disband)"
                    )
                    self._refresh_view()

            self.app.push_screen(
                ConfirmModal(
                    f"{names}{more} will DISBAND if left in the city. "
                    "End turn anyway?"
                ),
                _on_answer,
            )
            return
        self._begin_end_turn()

    def _doomed_garrison_units(self) -> list[Unit]:
        """Own units that §5.4 will disband at this turn's end. (A conqueror
        never appears here: it disbands into its city at capture time, §4.5 —
        only produced-and-unmoved units can be standing on an own city.)"""
        return [
            u
            for u in self._game.map.board_units()
            if u.owner is self._human and self._in_city_warning(u) is not None
        ]

    def _begin_end_turn(self) -> None:
        plan = self._build_plan()
        self._human_ctrl.set_plan(plan)
        # Reset per-turn state. Production stays committed in the city via
        # the engine; we just clear our scratch dicts.
        self._pending_production.clear()
        self._pending_orders.clear()  # handed off via the plan's set_orders
        self._moves_used.clear()
        self._selected_unit_id = None
        self._handled.clear()
        self._awaiting_heading = False
        self._awaiting_goto_target = False
        self._awaiting_patrol_target = False
        self._awaiting_unload = False
        self._awaiting_city_order_target = None
        # Paint the status line FIRST, then run the (synchronous) engine
        # turn on the next tick: a search AI can think for seconds, and the
        # screen is allowed to sit static under this banner meanwhile.
        self._turn_running = True
        self._hint = f"{self._opponent} is thinking…"
        self._refresh_view()
        self.set_timer(0.05, self._finish_end_turn)

    def _finish_end_turn(self) -> None:
        try:
            self._game.run_turn()
        finally:
            self._turn_running = False
        if self._game.is_over():
            winner = self._game.winner()
            self._hint = (
                f"game over — winner: {winner.name if winner else 'draw'}"
            )
        else:
            self._hint = "press ? for help"
            # Set up the next turn: pick the first unit that needs orders.
            self._advance_to_next_unit(initial=True)
        self._refresh_view()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Freeze the keyboard while the engine resolves a turn (the screen
        is deliberately static during AI thinking; see action_end_turn)."""
        del parameters
        return not (self._turn_running and action not in ("help", "quit"))

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
        # The other player(s) get a fresh controller of the session's
        # configured opponent kind (saves don't persist controllers).
        from empire.tui.launching import GameLauncher

        launcher = GameLauncher()
        for p in self._game.players:
            if p.id == self._human.id:
                continue
            self._game.attach_controller(p.id, launcher.make_opponent(self._opponent))
        # Reset transient selection/cursor state to the loaded game — the old
        # cursor may be out of bounds for a differently-shaped map.
        self._selected_unit_id = None
        self._handled.clear()
        self._moves_used.clear()
        self._awaiting_heading = False
        self._awaiting_goto_target = False
        self._awaiting_patrol_target = False
        self._awaiting_unload = False
        self._cursor = self._home_cursor()
        refresh_player_view(self._human, self._game.map, self._game.turn)
        self._hint = f"loaded from {path}"
        self._refresh_view()

    def _home_cursor(self) -> Coord:
        """A valid starting cursor for the current game: the human's first
        city, then first unit, else the map origin."""
        for city in self._game.map.cities():
            if city.owner is self._human:
                return city.coord
        for unit in self._game.map.board_units():
            if unit.owner is self._human:
                return unit.coord
        return Coord(0, 0)

    def action_quit(self) -> None:
        def _on_answer(confirmed: bool | None) -> None:
            if confirmed:
                self.app.exit()
            else:
                self._hint = "quit cancelled"
                self._refresh_view()

        self.app.push_screen(
            ConfirmModal("Quit Empire? Unsaved progress will be lost."),
            _on_answer,
        )

    # ---- helpers ----------------------------------------------------------

    def _selected_unit(self) -> Unit | None:
        if self._selected_unit_id is None:
            return None
        return self._game.map.unit_by_id(self._selected_unit_id)

    def _city_at_cursor(self) -> City | None:
        tile = self._game.map.tile(self._cursor)
        return tile.city

    def _cursor_context_hint(self) -> str:
        """The free-cursor hint, contextual to what's under the cursor —
        makes city interaction discoverable without the help screen."""
        city = self._city_at_cursor()
        if city is not None and city.owner is self._human:
            return "your city — 'p' set production, 'k' default order"
        unit = next(
            (u for u in self._game.map.units_at(self._cursor) if u.owner is self._human),
            None,
        )
        if unit is not None:
            return "your unit here — 'u' to take control"
        return "cursor — direction keys move; 'u' select unit, 'p' city build"

    def _units_needing_orders(self, *, include_handled: bool = False) -> list[Unit]:
        """Own units with moves remaining; by default skips already-handled.

        Units with a non-None `standing_order` are skipped: the engine
        will drive them next turn (or has driven them this turn). Aboard
        cargo is skipped too — it's commanded via its carrier (unload),
        not the normal auto-cycle (`board_units` excludes it).
        """
        result: list[Unit] = []
        for unit in self._game.map.board_units():
            if unit.owner is not self._human:
                continue
            # A LAUNCHED satellite is not player-commanded (spec §2.4 —
            # fixed one-way orbit, wraps at edges): it never re-enters the
            # queue. An UNLAUNCHED one enters once, for its launch prompt.
            if unit.kind is UnitKind.SATELLITE:
                if unit.orbit_direction is None:
                    result.append(unit)
                continue
            if unit.moves_this_turn() <= 0:
                continue
            if unit.standing_order is not None:
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
            if self._auto_turn and not self._game.is_over():
                self._hint = "all units handled — ending turn (auto; 'a' toggles)"
                # A visible beat so the player sees the world before it moves.
                self.set_timer(0.4, self._auto_end_turn)
            else:
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
        warning = self._in_city_warning(target)
        if warning is not None:
            self._hint = warning
        elif target.kind is UnitKind.SATELLITE:
            self._hint = (
                "satellite ready: press a direction to LAUNCH its one-way "
                "orbit (wraps the map; no control after launch)"
            )
        else:
            self._hint = (
                f"{remaining} unit(s) need orders; direction keys move it, "
                f"'n' skip, '.' sentry, Esc → free cursor"
            )

    def _auto_end_turn(self) -> None:
        """Timer callback for auto-turn. Re-checks the world — the player may
        have toggled auto off, selected a unit, woken a sentried unit (which
        re-enters the order queue), or ended the turn manually while the
        timer was pending."""
        if (
            not self._auto_turn
            or self._turn_running
            or self._game.is_over()
            or self._units_needing_orders()
        ):
            return
        self.action_end_turn()

    def _select_and_center(self, unit: Unit) -> None:
        self._selected_unit_id = unit.id
        self._cursor = unit.coord

    def _in_city_warning(self, unit: Unit) -> str | None:
        """A move-or-lose warning if `unit` sits in a friendly city it can't
        garrison (spec §5.4). Armies (city limit 0) are always at risk."""
        tile = self._game.map.tile(unit.coord)
        if tile.city is None or tile.city.owner is not self._human:
            return None
        if unit.kind is UnitKind.ARMY:
            return "army in your city — move it out this turn or it disbands"
        return None

    def _build_plan(self) -> TurnPlan:
        """Build the human's TurnPlan for end-of-turn.

        Moves are already applied immediately on key press, so this carries
        production orders plus the standing orders set this turn (their
        first step already happened; set_orders activates them after this
        round's standing-orders phase). Any own city without a build target
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

        set_orders = tuple(
            SetOrder(unit_id=uid, order=order)
            for uid, order in self._pending_orders.items()
        )
        return TurnPlan(
            production_orders=tuple(production_orders), set_orders=set_orders
        )

    def _set_heading(self, unit_id: UnitId, d: Direction) -> None:
        unit = self._game.map.unit_by_id(unit_id)
        if unit is None:
            self._awaiting_heading = False
            return
        self._awaiting_heading = False
        # Step now; the heading goes live via the plan's set_orders so the
        # engine walks it from the NEXT round (not again this round).
        stepped, alive, block = self._order_first_step(unit, unit.coord.step(d))
        if block == "attack":
            # A heading never auto-attacks: enemy/city ahead → engage manually.
            self._hint = f"enemy {d.name} — step in to attack (heading not set)"
            self._refresh_view()
            return
        if block == "artillery":
            # A heading never walks into a hostile city's gun range (spec §4.7).
            self._hint = f"{d.name} enters a city's gun range — step in deliberately (heading not set)"
            self._refresh_view()
            return
        self._handled.add(unit_id)
        if alive:
            self._pending_orders[unit.id] = Heading(direction=d)
            self._hint = f"heading set: {d.name}" + (" (stepped)" if stepped else "")
        else:
            self._hint = f"unit lost stepping {d.name}"
        self._advance_to_next_unit()
        self._refresh_view()

    def _do_unload(self, carrier_id: UnitId, d: Direction) -> None:
        """Land the carrier's next aboard unit on the cell in direction `d`."""
        self._awaiting_unload = False
        carrier = self._game.map.unit_by_id(carrier_id)
        if carrier is None or not carrier.cargo:
            self._refresh_view()
            return
        cargo = self._game.map.unit_by_id(carrier.cargo[0])
        if cargo is None:
            self._refresh_view()
            return
        to = carrier.coord.step(d)
        start = carrier.coord
        outcome = execute_unload(
            cargo=cargo,
            to=to,
            real_map=self._game.map,
            rules=self._game.rules,
            combat_resolver=self._game.combat_resolver,
            rng=self._game.rng,
        )
        # Events via the shared reporter — an unload can move, die in a landing
        # fight, or CAPTURE the city it disembarks onto (§4.5); all outcomes
        # publish the same way the engine turn loop does.
        publish_move_outcome(
            self._bus, self._game.map, self._human.id, cargo.id, start, outcome
        )
        refresh_player_view(self._human, self._game.map, self._game.turn)

        if outcome.last_outcome is StepOutcome.OK:
            # The disembark IS the landed unit's move for the turn (spec §3.4):
            # mark it spent + handled so it can't also move after landing.
            landed = self._game.map.unit_by_id(cargo.id)
            if landed is not None:
                self._moves_used[cargo.id] = landed.moves_this_turn()
                self._handled.add(cargo.id)
            remaining = len(carrier.cargo)
            self._hint = f"unloaded to ({to.x},{to.y}); {remaining} still aboard"
        elif outcome.last_outcome is StepOutcome.CAPTURED and outcome.cities_captured:
            # Amphibious capture: the cargo stormed ashore and took the city
            # (consumed into it, §4.5). Prompt production right away, same as
            # a land capture.
            self._handled.add(cargo.id)
            captured = self._game.map.city_by_id(outcome.cities_captured[-1])
            remaining = len(carrier.cargo)
            self._hint = f"city captured from the sea! {remaining} still aboard"
            if captured is not None:
                self._prompt_capture_production(captured)
        elif outcome.last_outcome is StepOutcome.NO_UNLOAD_YET:
            self._hint = "can't unload the same turn it loaded — try next turn"
        else:
            self._hint = f"unload failed ({outcome.last_outcome.value})"
        self._refresh_view()

    def _build_goto_path(self, unit: Unit, target: Coord) -> list[Coord] | None:
        """BFS over the human's ViewMap; returns cells AFTER start (exclusive).

        Routes AROUND the gun rings of *discovered* hostile cities (§4.7) by
        weighting those cells, so GOTO never walks a unit into a known gauntlet
        when a detour exists — but it's a weight, not a wall, so an intended
        assault to a goal inside/behind the ring still reaches it.
        """
        from dataclasses import replace

        from empire.core.unit import (
            Army,
            Battleship,
            Carrier,
            Destroyer,
            Patrol,
            Submarine,
            Transport,
        )

        kind_cls = type(unit)
        if kind_cls is Army:
            profile = ARMY
        elif kind_cls in (Patrol, Destroyer, Submarine, Transport, Carrier, Battleship):
            profile = SEA
        else:
            profile = AIR

        mv = self._map_view()
        danger = mv.danger_cells if mv is not None else frozenset()
        # Don't penalize the goal itself — if the player aimed at a danger cell,
        # take them there; only avoid danger encountered en route.
        threat_at = None
        if danger:
            profile = replace(profile, danger_weight=_GOTO_DANGER_WEIGHT)
            threat_at = lambda c: 1 if (c in danger and c != target) else 0  # noqa: E731

        result = BFSPathfinder().find_path(
            start=unit.coord,
            goal=target,
            real_map=self._game.map,
            profile=profile,
            view=self._human.view,
            threat_at=threat_at,
        )
        if result is None:
            return None
        # Drop the start cell — engine path is the cells the unit ENTERS.
        return list(result.cells[1:])

    def _refresh_view(self) -> None:
        map_widget = self.query_one("#map", MapWidget)
        map_widget.cursor = self._cursor
        map_widget.cursor_mode = self._cursor_mode()
        map_widget.refresh()
        self._scroll_cursor_into_view()
        self.query_one("#status", StatusBar).refresh_text()

    def on_resize(self) -> None:
        self._cap_map_viewport()
        self._scroll_cursor_into_view()

    def _cap_map_viewport(self) -> None:
        """Cap the map viewport so the status bar, footer and the log's
        minimum height always keep their rows when the map is bigger than
        the terminal. (When the map fits, height:auto keeps it at natural
        size and the log's 1fr absorbs the surplus.)"""
        reserved = 2 + 8  # status + footer + LogPanel min-height
        scroller = self.query_one("#map-scroll", ScrollableContainer)
        scroller.styles.max_height = max(10, self.size.height - reserved)

    def _scroll_cursor_into_view(self) -> None:
        """Keep the cursor inside the map viewport (with a margin of context)
        when the map is bigger than the terminal — the viewport follows the
        cursor / auto-cycle selection instead of the player driving scrollbars.
        Instant (no animation): turn-based play wants a snappy jump."""
        scroller = self.query_one("#map-scroll", ScrollableContainer)
        # Cursor cell in the MapWidget's coordinates: x offset by the row-label
        # gutter, y offset by the column ruler (widget row 0).
        cell = Region(self._cursor.x + _GUTTER, self._cursor.y + 1, 1, 1)
        scroller.scroll_to_region(
            cell, spacing=Spacing(4, 8, 4, 8), animate=False, force=True
        )

    def _cursor_mode(self) -> CursorMode:
        """Tri-state cursor color hint:
          FREE   — no unit selected (cursor wanders the map)
          ACTIVE — unit selected with unspent moves (direction = walk)
          IDLE   — unit selected but out of moves (cosmetic — looking)
        """
        if self._selected_unit_id is None:
            return CursorMode.FREE
        unit = self._selected_unit()
        if unit is None:
            return CursorMode.FREE
        remaining = unit.moves_this_turn() - self._moves_used.get(unit.id, 0)
        return CursorMode.ACTIVE if remaining > 0 else CursorMode.IDLE

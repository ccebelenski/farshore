"""Headless smoke tests for `PlayScreen`. Boots `EmpireApp` via Textual's
pilot, runs scripted input, and checks state at the end. No screenshots —
the goal here is "does the game advance without crashing under TUI
control."
"""

from __future__ import annotations

import pytest

from empire.ai.baseline import BaselineAI
from empire.core.engine import scan_set_for_player
from empire.core.ruleset import SMALL
from empire.events.bus import EventBus
from empire.setup import build_game
from empire.tui.app import EmpireApp
from empire.tui.human_controller import HumanController


def _build_app(
    seed: int = 0, *, auto_turn: bool = False
) -> tuple[EmpireApp, HumanController, EventBus]:
    # auto_turn defaults OFF in tests: scripted input must control exactly
    # when turns advance (the shipped default is ON).
    bus = EventBus()
    game, players = build_game(SMALL, seed, p1_is_ai=False, p2_is_ai=True)
    game.event_bus = bus
    human = players[0]
    ctrl = HumanController()
    game.attach_controller(human.id, ctrl)
    game.attach_controller(players[1].id, BaselineAI())
    scanned = scan_set_for_player(human, game.map)
    human.view.update_from_scan(scanned, game.map, game.turn)
    app = EmpireApp(
        game=game,
        human_player=human,
        human_controller=ctrl,
        event_bus=bus,
        auto_turn=auto_turn,
    )
    return app, ctrl, bus


async def test_app_boots_and_renders() -> None:
    """Mount + initial render path. Asserts the app reached a running state
    with a screen attached — exercising the full PlayScreen.on_mount path
    (scan, log wire-up, auto-cycle). The context manager handles teardown."""
    app, _, _ = _build_app()
    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        assert app.is_running
        assert app.screen is not None


async def test_end_turn_advances_engine() -> None:
    app, _, _ = _build_app()
    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        assert app.game is not None
        before = app.game.turn
        await pilot.press("e")
        # End-turn paints "thinking…" first and runs the engine on a short
        # timer (so the status line shows during synchronous AI turns).
        await pilot.pause(0.2)
        after = app.game.turn
    assert after == before + 1, f"turn did not advance: {before} -> {after}"


async def test_help_modal_opens_and_dismisses() -> None:
    from empire.tui.modals import HelpModal
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpModal)
        # Any key dismisses HelpModal.
        await pilot.press("space")
        await pilot.pause()
        assert isinstance(app.screen, PlayScreen)


async def test_auto_turn_advances_without_keypress() -> None:
    """With auto-turn on (the shipped default) and nothing needing orders,
    turns advance on their own."""
    app, _, _ = _build_app(auto_turn=True)
    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        assert app.game is not None
        before = app.game.turn
        # Auto beat (0.4s) + thinking tick (0.05s) per turn; give it ~2 turns.
        await pilot.pause(1.2)
        after = app.game.turn
    assert after > before, "auto-turn did not advance the game"


async def test_end_turn_with_doomed_army_asks_confirmation() -> None:
    """An army parked on its own city would disband (§5.4); ending the turn
    must go through a ConfirmModal instead of silently eating the unit.
    Declining leaves the turn unadvanced AND lands the player on the at-risk
    unit."""
    from empire.core.identity import UnitId
    from empire.core.unit import Army
    from empire.tui.modals import ConfirmModal
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    army = Army(UnitId(900), human, capital.coord)
    game.map.place_unit(army, capital.coord)

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        before = game.turn
        # Skip the army's orders, then end the turn: the confirm must appear.
        await pilot.press("n", "e")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        # Decline: turn must NOT have advanced, and the cursor lands on the unit.
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert game.turn == before
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        assert screen._selected_unit_id == army.id  # pyright: ignore[reportPrivateUsage]


async def test_manual_move_revokes_standing_order_no_double_step() -> None:
    """Playtest 'free move' bug: a unit with a heading that the player then
    moves manually must NOT also take its standing-order step in the same
    round — direct control revokes the order."""
    from empire.core.coord import Coord, Direction
    from empire.core.identity import UnitId
    from empire.core.standing_order import Heading
    from empire.core.tile import TerrainKind
    from empire.core.unit import Army

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)

    # Find a 3-cell eastward land run for a clean walk.
    spot = None
    for dx in range(-6, 7):
        for dy in range(-6, 7):
            cells = [
                Coord(capital.coord.x + dx + i, capital.coord.y + dy)
                for i in range(3)
            ]
            if all(
                game.map.in_bounds(c)
                and game.map.terrain_at(c) is TerrainKind.LAND
                and not game.map.units_at(c)
                for c in cells
            ):
                spot = cells
                break
        if spot:
            break
    assert spot is not None
    a, b, _c = spot
    army = Army(UnitId(902), human, a)
    game.map.place_unit(army, a)
    army.standing_order = Heading(direction=Direction.E)

    from empire.tui.screens.play_screen import PlayScreen

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._cursor = a  # pyright: ignore[reportPrivateUsage]
        await pilot.press("u", "6")  # select; manual step east
        await pilot.pause()
        assert army.coord == b
        assert army.standing_order is None  # direct control revoked the order
        await pilot.press("e")
        await pilot.pause(0.3)
        # The engine round must not have stepped it again.
        assert army.coord == b


async def test_setting_heading_steps_immediately_then_walks_next_round() -> None:
    """User expectation: 'setting the heading moves the unit immediately.'
    And it must not double-step in the round the order was set (the order
    rides the plan's set_orders, activating after this round's SO phase)."""
    from empire.core.coord import Coord
    from empire.core.identity import UnitId
    from empire.core.tile import TerrainKind
    from empire.core.unit import Army
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)

    spot = None
    for dx in range(-6, 7):
        for dy in range(-6, 7):
            cells = [
                Coord(capital.coord.x + dx + i, capital.coord.y + dy)
                for i in range(4)
            ]
            if all(
                game.map.in_bounds(c)
                and game.map.terrain_at(c) is TerrainKind.LAND
                and not game.map.units_at(c)
                for c in cells
            ):
                spot = cells
                break
        if spot:
            break
    assert spot is not None
    a, b, c, _d4 = spot
    army = Army(UnitId(903), human, a)
    game.map.place_unit(army, a)

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._cursor = a  # pyright: ignore[reportPrivateUsage]
        await pilot.press("u", "d", "6")  # select; set heading east
        await pilot.pause()
        assert army.coord == b, "setting the heading must step immediately"
        await pilot.press("e")
        await pilot.pause(0.3)
        # Same round: NOT stepped again (no free move).
        assert army.coord == b
        await pilot.press("e")
        await pilot.pause(0.3)
        # Next round: the heading walks it.
        assert army.coord == c


async def test_capturing_a_city_prompts_for_production() -> None:
    """Walking an army onto a neutral city captures it and immediately opens
    the production picker for the new city (playtest request)."""
    import dataclasses

    from empire.core.city import City
    from empire.core.coord import Coord
    from empire.core.identity import CityId, UnitId
    from empire.core.tile import TerrainKind, Tile
    from empire.core.unit import Army
    from empire.tui.modals import ProductionModal

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    game.rules = dataclasses.replace(
        game.rules, army_capture_city_deterministic=True
    )
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)

    # An adjacent land pair: army on `a`, neutral city on `b` (east of a).
    spot = None
    for dx in range(-5, 6):
        for dy in range(-5, 6):
            a = Coord(capital.coord.x + dx, capital.coord.y + dy)
            b = Coord(a.x + 1, a.y)
            if (
                game.map.in_bounds(a)
                and game.map.in_bounds(b)
                and game.map.terrain_at(a) is TerrainKind.LAND
                and game.map.terrain_at(b) is TerrainKind.LAND
                and not game.map.units_at(a)
                and not game.map.units_at(b)
            ):
                spot = (a, b)
                break
        if spot:
            break
    assert spot is not None
    a, b = spot
    city = City(id=CityId(77), coord=b, owner=None)
    game.map._tiles[b] = Tile(  # pyright: ignore[reportPrivateUsage]
        coord=b, terrain=TerrainKind.CITY, city=city
    )
    army = Army(UnitId(905), human, a)
    game.map.place_unit(army, a)

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        from empire.tui.screens.play_screen import PlayScreen

        assert isinstance(screen, PlayScreen)
        screen._cursor = a  # pyright: ignore[reportPrivateUsage]
        await pilot.press("u", "6")  # select; step east onto the city
        await pilot.pause()
        assert city.owner is human  # captured
        assert isinstance(app.screen, ProductionModal)  # picker opened
        # Keyboard-first picker: Enter selects the cursor row, dismissing it.
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PlayScreen)


async def test_free_cursor_hint_names_city_actions() -> None:
    """Parking the free cursor on an own city advertises p/k in the hint."""
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        # Drop to free cursor and place it on the capital.
        await pilot.press("escape")
        screen._cursor = capital.coord  # pyright: ignore[reportPrivateUsage]
        hint = screen._cursor_context_hint()  # pyright: ignore[reportPrivateUsage]
        assert "'p'" in hint and "production" in hint


async def test_production_modal_esc_dismisses_without_change() -> None:
    """The '^p' picker is keyboard-first: Esc closes it (was hard to dismiss)
    and leaves production unchanged."""
    from empire.tui.modals import ProductionModal
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    before = capital.production.building

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        await pilot.press("escape")  # free the cursor
        screen._cursor = capital.coord  # pyright: ignore[reportPrivateUsage]
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ProductionModal)
        await pilot.press("escape")  # Esc must close it
        await pilot.pause()
        assert isinstance(app.screen, PlayScreen)
        # Esc = cancel: the live production target is unchanged (any queued
        # value is the current one — a no-op, same as the old modal's cancel).
        assert capital.production.building == before
        queued = screen._pending_production.get(  # pyright: ignore[reportPrivateUsage]
            capital.id, before
        )
        assert queued == before


async def test_production_modal_picks_a_kind() -> None:
    """Down to a kind, Enter selects it, queued for end-of-turn."""
    from empire.core.unit import UnitKind
    from empire.tui.modals import ProductionModal
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        await pilot.press("escape")
        screen._cursor = capital.coord  # pyright: ignore[reportPrivateUsage]
        await pilot.press("p")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ProductionModal)
        # Cursor starts on the current kind; Enter selects it (a valid kind).
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PlayScreen)
        queued = screen._pending_production.get(capital.id)  # pyright: ignore[reportPrivateUsage]
        assert isinstance(queued, UnitKind), "Enter should queue the current kind"


async def test_wake_via_cursor_without_selecting_works_for_multiple() -> None:
    """The exact playtest repro: wake by pointing the cursor at a sentried
    unit and pressing 'w' WITHOUT selecting it first — for several units in a
    row. Each must actually wake (it stayed sentried before, because wake
    only acted on the *selected* unit)."""
    from empire.core.coord import Coord
    from empire.core.identity import UnitId
    from empire.core.standing_order import Sentry
    from empire.core.tile import TerrainKind
    from empire.core.unit import Army
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app(auto_turn=False)
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    cap = next(c for c in game.map.cities() if c.owner is human)
    spots = []
    for dx in range(-4, 5):
        for dy in range(-4, 5):
            c = Coord(cap.coord.x + dx, cap.coord.y + dy)
            if (
                game.map.in_bounds(c)
                and game.map.terrain_at(c) is TerrainKind.LAND
                and not game.map.units_at(c)
            ):
                spots.append(c)
        if len(spots) >= 2:
            break
    a, b = spots[0], spots[1]
    army_a = Army(UnitId(811), human, a)
    game.map.place_unit(army_a, a)
    army_b = Army(UnitId(812), human, b)
    game.map.place_unit(army_b, b)
    # Both already sentried (as if from a previous turn).
    army_a.standing_order = Sentry()
    army_b.standing_order = Sentry()

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        # Point-and-wake, no 'u' select, for both in a row.
        screen._cursor = a  # pyright: ignore[reportPrivateUsage]
        await pilot.press("w")
        screen._cursor = b  # pyright: ignore[reportPrivateUsage]
        await pilot.press("w")
        await pilot.pause()
        assert army_a.standing_order is None, "cursor-wake left A sentried"
        assert army_b.standing_order is None, "cursor-wake left B sentried"
        # Both are back in the order queue with full moves this turn.
        needs = {int(u.id) for u in screen._units_needing_orders()}  # pyright: ignore[reportPrivateUsage]
        assert needs == {811, 812}


async def test_sentrying_everything_still_auto_ends_the_turn() -> None:
    """Auto-end must still fire when every unit is sentried — sentry defers
    the unit but does not stop the turn from completing (user's model)."""
    from empire.core.coord import Coord
    from empire.core.identity import UnitId
    from empire.core.tile import TerrainKind
    from empire.core.unit import Army
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app(auto_turn=True)  # the shipped default
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    cap = next(c for c in game.map.cities() if c.owner is human)
    spot = next(
        Coord(cap.coord.x + dx, cap.coord.y)
        for dx in range(1, 6)
        if game.map.in_bounds(Coord(cap.coord.x + dx, cap.coord.y))
        and game.map.terrain_at(Coord(cap.coord.x + dx, cap.coord.y))
        is TerrainKind.LAND
        and not game.map.units_at(Coord(cap.coord.x + dx, cap.coord.y))
    )
    army = Army(UnitId(814), human, spot)
    game.map.place_unit(army, spot)

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        turn0 = game.turn
        screen._cursor = spot  # pyright: ignore[reportPrivateUsage]
        await pilot.press("u", "full_stop")  # sentry the only unit
        await pilot.pause(0.8)  # past the auto-end beat
        assert game.turn > turn0, "sentrying everything should still auto-end"


async def test_skip_defers_without_forfeiting_moves() -> None:
    """'n' skip is a defer, not a forfeit: the unit keeps its moves and can
    be woken/revisited to act this turn."""
    from empire.core.coord import Coord
    from empire.core.identity import UnitId
    from empire.core.tile import TerrainKind
    from empire.core.unit import Army
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app(auto_turn=False)
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    cap = next(c for c in game.map.cities() if c.owner is human)
    spot = next(
        Coord(cap.coord.x + dx, cap.coord.y)
        for dx in range(1, 6)
        if game.map.in_bounds(Coord(cap.coord.x + dx, cap.coord.y))
        and game.map.terrain_at(Coord(cap.coord.x + dx, cap.coord.y))
        is TerrainKind.LAND
        and not game.map.units_at(Coord(cap.coord.x + dx, cap.coord.y))
    )
    army = Army(UnitId(813), human, spot)
    game.map.place_unit(army, spot)

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._cursor = spot  # pyright: ignore[reportPrivateUsage]
        await pilot.press("u", "n")  # select then skip (defer)
        await pilot.pause()
        # Moves not forfeited.
        assert screen._moves_used.get(army.id, 0) == 0  # pyright: ignore[reportPrivateUsage]
        # Revisit and it's actionable again.
        screen._cursor = spot  # pyright: ignore[reportPrivateUsage]
        await pilot.press("u")
        await pilot.pause()
        assert screen._selected_unit_id == army.id  # pyright: ignore[reportPrivateUsage]
        assert army.id in {
            u.id for u in screen._units_needing_orders()  # pyright: ignore[reportPrivateUsage]
        }


async def test_load_command_snaps_adjacent_and_waits_then_wakes() -> None:
    """'l' on a transport snaps an adjacent army aboard, enters loading mode,
    waits (does not pull a distant army), and wakes when an army walks on to
    fill it."""
    from empire.core.coord import Coord
    from empire.core.identity import UnitId
    from empire.core.standing_order import Loading
    from empire.core.tile import TerrainKind
    from empire.core.unit import Army, Transport
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app(auto_turn=False)
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    # A water cell with two adjacent land cells for armies.
    spot = None
    for x in range(1, game.map.width - 1):
        for y in range(1, game.map.height - 1):
            w = Coord(x, y)
            la, lb = Coord(x - 1, y), Coord(x + 1, y)
            if (
                game.map.terrain_at(w) is TerrainKind.WATER
                and game.map.in_bounds(la)
                and game.map.in_bounds(lb)
                and game.map.terrain_at(la) is TerrainKind.LAND
                and game.map.terrain_at(lb) is TerrainKind.LAND
                and not game.map.units_at(w)
                and not game.map.units_at(la)
                and not game.map.units_at(lb)
            ):
                spot = (w, la, lb)
                break
        if spot:
            break
    assert spot is not None, "need a water cell flanked by land"
    water, land_a, land_b = spot
    transport = Transport(UnitId(820), human, water)  # capacity 6
    game.map.place_unit(transport, water)
    near = Army(UnitId(821), human, land_a)  # adjacent → snaps aboard
    game.map.place_unit(near, land_a)
    far = Army(UnitId(822), human, land_b)  # adjacent on the other side too
    game.map.place_unit(far, land_b)
    human.view.visible = {
        Coord(x, y) for x in range(game.map.width) for y in range(game.map.height)
    }

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._cursor = water  # pyright: ignore[reportPrivateUsage]
        await pilot.press("u", "l")  # select transport, loading mode
        await pilot.pause()
        # Both flanking armies were adjacent → snapped aboard immediately.
        assert near.id in transport.cargo
        assert far.id in transport.cargo
        # Not full (cap 6) → transport is waiting in Loading mode.
        assert isinstance(transport.standing_order, Loading)


async def test_load_command_full_does_not_enter_loading() -> None:
    """If snapping adjacent cargo fills the carrier, it doesn't sentry —
    it's free to sail."""
    from empire.core.coord import Coord
    from empire.core.identity import UnitId
    from empire.core.standing_order import Loading
    from empire.core.tile import TerrainKind
    from empire.core.unit import Carrier, Fighter
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app(auto_turn=False)
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    # Carrier carries fighters; find a water cell with an adjacent cell.
    spot = None
    for x in range(1, game.map.width - 1):
        for y in range(1, game.map.height - 1):
            w = Coord(x, y)
            if game.map.terrain_at(w) is TerrainKind.WATER and not game.map.units_at(w):
                spot = w
                break
        if spot:
            break
    assert spot is not None

    carrier = Carrier(UnitId(830), human, spot)
    game.map.place_unit(carrier, spot)
    # Fill capacity with adjacent fighters (fighters fly, any neighbor works).
    cap = carrier.effective_capacity()
    placed = 0
    fid = 831
    for nb in spot.neighbors():
        if placed >= cap:
            break
        if game.map.in_bounds(nb) and not game.map.units_at(nb):
            f = Fighter(UnitId(fid), human, nb)
            game.map.place_unit(f, nb)
            fid += 1
            placed += 1
    assert placed == cap  # enough neighbors to fill it

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._cursor = spot  # pyright: ignore[reportPrivateUsage]
        await pilot.press("u", "l")
        await pilot.pause()
        assert len(carrier.cargo) == cap
        assert not isinstance(carrier.standing_order, Loading)  # not waiting


async def test_satellite_launch_prompt_and_orbit_lockout() -> None:
    """An UNLAUNCHED satellite enters the order cycle exactly once — for its
    launch prompt: the direction key launches it (sets the one-way orbit),
    never moves it. A LAUNCHED satellite never re-enters the queue (spec
    §2.4 — no manual control after launch)."""
    from empire.core.coord import Direction
    from empire.core.identity import UnitId
    from empire.core.unit import Satellite
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    sat = Satellite(UnitId(950), human, capital.coord)
    game.map.place_unit(sat, capital.coord)
    start = sat.coord

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        needing = screen._units_needing_orders(  # pyright: ignore[reportPrivateUsage]
            include_handled=True
        )
        assert sat.id in {u.id for u in needing}  # awaiting its launch prompt

        # Select it and press a direction: LAUNCH, not a move.
        screen._selected_unit_id = sat.id  # pyright: ignore[reportPrivateUsage]
        await pilot.press("8")  # north
        assert sat.orbit_direction is Direction.N
        assert sat.coord == start  # launching is not a step

        # Launched: locked out of the queue and of manual movement.
        needing = screen._units_needing_orders(  # pyright: ignore[reportPrivateUsage]
            include_handled=True
        )
        assert sat.id not in {u.id for u in needing}


async def test_city_report_opens_and_dismisses() -> None:
    """The 'c' city report pops a read-only modal that any key closes."""
    from empire.tui.modals import CityReportModal
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("escape")  # free cursor (don't intercept 'c' elsewhere)
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CityReportModal)
        await pilot.press("space")  # any key closes
        await pilot.pause()
        assert isinstance(app.screen, PlayScreen)


@pytest.mark.parametrize(
    ("key", "survives"),
    [("y", False), ("n", True)],
    ids=["confirm-removes", "cancel-keeps"],
)
async def test_disband_confirm_or_cancel(key: str, survives: bool) -> None:
    """'x' on an own unit pops a confirm: 'y' removes it, 'n' keeps it."""
    from empire.core.identity import UnitId
    from empire.core.unit import Army
    from empire.tui.modals import ConfirmModal
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    army = Army(UnitId(960), human, capital.coord)
    game.map.place_unit(army, capital.coord)

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        await pilot.press("escape")  # free cursor
        screen._cursor = capital.coord  # pyright: ignore[reportPrivateUsage]
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press(key)
        await pilot.pause()
        assert game.map.unit_by_id(army.id) is (army if survives else None)


async def test_patrol_route_sets_looping_cycle() -> None:
    """'t' on a ship + endpoint + Enter: the ship steps out immediately and
    the pending order is a LOOPING round-trip (start<->endpoint) that the
    engine shuttles until woken."""
    from empire.core.coord import Coord
    from empire.core.identity import UnitId
    from empire.core.standing_order import PatrolPath
    from empire.core.tile import TerrainKind
    from empire.core.unit import Patrol
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    # Find a 3-cell horizontal water run for the patrol leg.
    run = None
    for y in range(game.map.height):
        for x in range(game.map.width - 2):
            cells = [Coord(x + i, y) for i in range(3)]
            if all(
                game.map.terrain_at(c) is TerrainKind.WATER
                and game.map.tile(c).on_board
                for c in cells
            ):
                run = cells
                break
        if run:
            break
    assert run is not None, "map should have a 3-cell water run"
    a, mid, b = run
    ship = Patrol(UnitId(960), human, a)
    game.map.place_unit(ship, a)
    # Full visibility so the goto/patrol BFS can route over the water.
    human.view.visible = {
        Coord(x, y) for x in range(game.map.width) for y in range(game.map.height)
    }

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._selected_unit_id = ship.id  # pyright: ignore[reportPrivateUsage]
        screen.action_patrol_route()
        assert screen._awaiting_patrol_target  # pyright: ignore[reportPrivateUsage]
        screen._cursor = b  # pyright: ignore[reportPrivateUsage]
        screen.action_confirm()
        await pilot.pause()

        assert ship.coord == mid  # immediate first step, like go-to
        order = screen._pending_orders[ship.id]  # pyright: ignore[reportPrivateUsage]
        assert isinstance(order, PatrolPath)
        assert order.loop is True
        assert order.original == (mid, b, mid, a)  # full round trip
        assert order.remaining == (b, mid, a)  # first cell already stepped


async def test_explore_verb_sets_order_and_skips_cycle() -> None:
    """'v' puts the unit into Explore; it leaves the order queue (the engine
    drives it until it wakes)."""
    from empire.core.identity import UnitId
    from empire.core.standing_order import Explore
    from empire.core.unit import Army
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    # A land cell next to the capital for the scout.
    from empire.core.tile import TerrainKind
    spot = next(
        c for c in capital.coord.neighbors()
        if game.map.in_bounds(c)
        and game.map.terrain_at(c) is TerrainKind.LAND
        and not game.map.units_at(c)
    )
    scout = Army(UnitId(970), human, spot)
    game.map.place_unit(scout, spot)

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._selected_unit_id = scout.id  # pyright: ignore[reportPrivateUsage]
        screen.action_explore()
        await pilot.pause()
        assert isinstance(scout.standing_order, Explore)
        needing = screen._units_needing_orders(  # pyright: ignore[reportPrivateUsage]
            include_handled=True
        )
        assert scout.id not in {u.id for u in needing}


async def test_rtb_verb_sets_order_or_warns() -> None:
    """'b' on a fighter: sets ReturnToBase when a base is reachable; with no
    base in fuel range it WARNS and aborts (no order — the player keeps
    control to choose where to fly or crash)."""
    from empire.core.identity import UnitId
    from empire.core.standing_order import ReturnToBase
    from empire.core.unit import Fighter
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    from empire.core.tile import TerrainKind
    spot = next(
        c for c in capital.coord.neighbors()
        if game.map.in_bounds(c)
        and game.map.terrain_at(c) is TerrainKind.LAND
        and not game.map.units_at(c)
    )
    jet = Fighter(UnitId(980), human, spot)
    game.map.place_unit(jet, spot)

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._selected_unit_id = jet.id  # pyright: ignore[reportPrivateUsage]

        # Base adjacent: RTB commits.
        screen.action_return_to_base()
        assert isinstance(jet.standing_order, ReturnToBase)

        # Out of fuel range of everything: warn + abort, no order.
        jet.standing_order = None
        jet.range = 0
        screen._selected_unit_id = jet.id  # pyright: ignore[reportPrivateUsage]
        screen.action_return_to_base()
        assert jet.standing_order is None
        assert "NO BASE IN RANGE" in screen._hint  # pyright: ignore[reportPrivateUsage]


async def test_explore_verb_steps_off_own_city_immediately() -> None:
    """'v' on an army standing on its own city must step it OFF right away
    (like go-to/heading's immediate first step): explore otherwise only acts
    next segment, and §5.4 disbands a city-sitting army at THIS segment's end
    — the order looked like it 'did nothing' and the army died (playtest)."""
    from empire.core.identity import UnitId
    from empire.core.standing_order import Explore
    from empire.core.unit import Army
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    scout = Army(UnitId(971), human, capital.coord)
    game.map.place_unit(scout, capital.coord)  # fresh produce, on the city

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        screen._selected_unit_id = scout.id  # pyright: ignore[reportPrivateUsage]
        screen.action_explore()
        await pilot.pause()
        assert isinstance(scout.standing_order, Explore)
        assert scout.coord != capital.coord, (
            "the immediate first step must carry the army off the city "
            "(no disband trap)"
        )


async def test_production_tile_present_and_lists_own_cities() -> None:
    """The production tile mounts beside the board and reports exactly the
    human's cities, each with ETA == current turn + turns-left."""
    from empire.tui.screens.play_screen import PlayScreen
    from empire.tui.widgets import ProductionPanel

    app, _, _ = _build_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        assert screen.query_one(ProductionPanel) is not None
        game = app.game
        assert game is not None
        own = [c for c in game.map.cities() if c.owner is screen._human]  # pyright: ignore[reportPrivateUsage]
        rows = screen._production_rows()  # pyright: ignore[reportPrivateUsage]
        assert len(rows) == len(own)
        for r in rows:
            if r.turns_left is None:
                assert r.eta is None
            else:
                assert r.eta == game.turn + r.turns_left


async def test_production_rows_reflect_pending_switch_and_idle_sorts_last() -> None:
    """A queued (not-yet-applied) production switch shows immediately; an idle
    city has no ETA and sorts to the bottom."""
    from empire.core.unit import UnitKind
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        game = app.game
        assert game is not None
        city = next(c for c in game.map.cities() if c.owner is screen._human)  # pyright: ignore[reportPrivateUsage]

        screen._pending_production[city.id] = UnitKind.DESTROYER  # pyright: ignore[reportPrivateUsage]
        row = next(
            r for r in screen._production_rows()  # pyright: ignore[reportPrivateUsage]
            if r.city_id == int(city.id)
        )
        assert row.task == UnitKind.DESTROYER.value

        screen._pending_production[city.id] = None  # idle  # pyright: ignore[reportPrivateUsage]
        rows = screen._production_rows()  # pyright: ignore[reportPrivateUsage]
        idle = next(r for r in rows if r.city_id == int(city.id))
        assert idle.turns_left is None and idle.eta is None
        assert rows[-1].city_id == idle.city_id  # idle sinks to the bottom


def test_production_state_render_handles_idle_and_imminent() -> None:
    """`ProductionState.render` builds the 4-column table over mixed rows
    (idle + imminent) without error."""
    from empire.core.coord import Coord
    from empire.tui.widgets import ProductionRow, ProductionState

    rows = [
        ProductionRow(Coord(1, 1), 1, "Landfall", "Army", 1, 153),  # imminent (bold)
        ProductionRow(Coord(2, 2), 2, "Cape Mercy", "Destroyer", 5, 157),
        ProductionRow(Coord(3, 3), 3, "Saltmarsh", "idle", None, None),  # idle (dim)
    ]
    table = ProductionState(rows).render()
    assert table.row_count == 3
    assert [c.header for c in table.columns] == ["City", "Building", "Left", "Done"]
    # City cell shows "Name (x,y)".
    assert "Landfall (1,1)" in ProductionRow(Coord(1, 1), 1, "Landfall", "Army", 1, 153).where()


async def test_production_tile_content_area_fits_longest_row() -> None:
    """Regression for the truncation bug: the tile's actual content area must
    be wide enough to render the widest row (longest unit name + headers)
    without clipping. Measures the panel's inner width in a live layout, then
    renders the table at that width and checks nothing is cut."""
    from rich.console import Console

    from empire.core.unit import UnitKind
    from empire.tui.screens.play_screen import PlayScreen
    from empire.tui.widgets import ProductionPanel

    longest = max(UnitKind, key=lambda k: len(k.value)).value  # "battleship"

    app, _, _ = _build_app()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlayScreen)
        game = app.game
        assert game is not None
        city = next(c for c in game.map.cities() if c.owner is screen._human)  # pyright: ignore[reportPrivateUsage]
        screen._pending_production[city.id] = UnitKind.BATTLESHIP  # widest name  # pyright: ignore[reportPrivateUsage]
        screen._refresh_view()  # pyright: ignore[reportPrivateUsage]
        await pilot.pause()

        panel = screen.query_one(ProductionPanel)
        body = panel.query_one("#production-body")
        width = body.content_size.width
        assert width > 0, "panel content area not laid out"

        console = Console(width=width, legacy_windows=False)
        with console.capture() as cap:
            console.print(screen._production_state().render())  # pyright: ignore[reportPrivateUsage]
        out = cap.get()
        assert "Building" in out, f"header clipped at content width {width}: {out!r}"
        assert longest in out, f"'{longest}' clipped at content width {width}: {out!r}"

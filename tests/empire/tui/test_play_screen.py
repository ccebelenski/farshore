"""Headless smoke tests for `PlayScreen`. Boots `EmpireApp` via Textual's
pilot, runs scripted input, and checks state at the end. No screenshots —
the goal here is "does the game advance without crashing under TUI
control."
"""

from __future__ import annotations

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
    app, _, _ = _build_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        # Any key dismisses HelpModal.
        await pilot.press("space")
        await pilot.pause()


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
    must go through a ConfirmModal instead of silently eating the unit."""
    from empire.core.identity import UnitId
    from empire.core.unit import Army
    from empire.tui.modals import ConfirmModal

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
        # Decline: turn must NOT have advanced.
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert game.turn == before


async def test_declining_disband_confirm_selects_the_doomed_army() -> None:
    """Declining the §5.4 confirm should put the player ON the unit at risk."""
    from empire.core.identity import UnitId
    from empire.core.unit import Army
    from empire.tui.modals import ConfirmModal
    from empire.tui.screens.play_screen import PlayScreen

    app, _, _ = _build_app()
    assert app.game is not None
    game = app.game
    human = next(p for p in game.players if not p.is_ai)
    capital = next(c for c in game.map.cities() if c.owner is human)
    army = Army(UnitId(901), human, capital.coord)
    game.map.place_unit(army, capital.coord)

    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        await pilot.press("n", "e")  # skip the army, try to end the turn
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("escape")  # decline
        await pilot.pause()
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
        assert queued is None or isinstance(queued, UnitKind)

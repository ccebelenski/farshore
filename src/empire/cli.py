"""Command-line utilities for development and playtesting.

Invoked via `python -m empire <command>`. Commands:

  dump-map  Generate a map for a given profile/seed and print it as ASCII.
  play      Run a game for N turns with NullController on both sides;
            useful for verifying engine mechanics (production tick, scan
            updates, no crashes).

Examples:
    python -m empire dump-map --profile STANDARD --seed 0
    python -m empire play --profile STANDARD --seed 0 --turns 10
"""

from __future__ import annotations

import argparse
import random
import sys

from empire.core.coord import Coord
from empire.core.map import Map
from empire.core.ruleset import LARGE, SMALL, STANDARD_PROFILE, MapProfile
from empire.core.tile import TerrainKind
from empire.mapgen.height_field import HeightFieldMapGenerator

_PROFILES: dict[str, MapProfile] = {
    "SMALL": SMALL,
    "STANDARD": STANDARD_PROFILE,
    "LARGE": LARGE,
}


def render_ascii(real_map: Map) -> str:
    """Render a `Map` as an ASCII grid suitable for terminal inspection.

    Legend:
        ``.``  on-board land
        ``~``  on-board water
        ``*``  city (any owner; mapgen produces all-neutral cities)
        ``#``  off-board border tile (unwalkable per spec §1.1)
    """
    lines: list[str] = []
    for y in range(real_map.height):
        chars: list[str] = []
        for x in range(real_map.width):
            tile = real_map.tile(Coord(x, y))
            if not tile.on_board:
                chars.append("#")
            elif tile.terrain is TerrainKind.CITY:
                chars.append("*")
            elif tile.terrain is TerrainKind.LAND:
                chars.append(".")
            else:
                chars.append("~")
        lines.append("".join(chars))
    return "\n".join(lines)


def dump_map(profile_name: str, seed: int, *, show_legend: bool = True) -> str:
    """Generate a map and return its ASCII rendering with a header."""
    profile = _PROFILES[profile_name]
    gen = HeightFieldMapGenerator()
    real_map, cities = gen.generate(profile, random.Random(seed))

    header = [
        f"# {profile_name} {profile.width}x{profile.height} "
        f"water={profile.water_ratio}% smooth={profile.smooth_iterations} "
        f"min_city_dist={profile.min_city_distance} seed={seed}",
        f"# {len(cities)} cities placed; "
        f"{gen.last_regen_count} regenerations needed",
        "",
    ]
    parts = [*header, render_ascii(real_map)]
    if show_legend:
        parts.extend([
            "",
            "# Legend: . land  ~ water  * city  # border",
        ])
    return "\n".join(parts)


def play_game(
    profile_name: str,
    seed: int,
    turns: int,
    *,
    show_final_map: bool = True,
    controller: str = "null",
) -> str:
    """Run a game for `turns` rounds with NullControllers, then summarize.

    Returns a multi-line string with the per-turn event count, final
    city ownership, and (optionally) the rendered map.
    """
    from empire.ai.baseline import BaselineAI
    from empire.combat.resolver import CombatResolver
    from empire.contracts.controller import AIController, NullController
    from empire.core.events import (
        CityCapturedEvent,
        TurnAdvancedEvent,
        UnitMovedEvent,
        UnitPlacedEvent,
        UnitRemovedEvent,
    )
    from empire.core.game import Game
    from empire.core.identity import PlayerId
    from empire.core.map import ViewMap
    from empire.core.player import Player
    from empire.core.ruleset import STANDARD
    from empire.events.bus import EventBus
    from empire.mapgen.height_field import HeightFieldMapGenerator

    profile = _PROFILES[profile_name]
    gen = HeightFieldMapGenerator()
    real_map, cities = gen.generate(profile, random.Random(seed))

    players = [
        Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap(), color="red"),
        Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap(), color="blue"),
    ]

    # Assign the two largest-continent cities as starting capitals (a stand-in
    # for full capital-selection logic in planning/01 §9.2 — those checks
    # land with the game-setup phase).
    from empire.core.tile import TerrainKind

    visited: set[tuple[int, int]] = set()
    continents: list[tuple[int, set[tuple[int, int]]]] = []  # (size, cells)
    for y in range(real_map.height):
        for x in range(real_map.width):
            if (x, y) in visited:
                continue
            tile = real_map.tile(_Coord(x, y))
            if tile.terrain not in {TerrainKind.LAND, TerrainKind.CITY}:
                continue
            comp: set[tuple[int, int]] = set()
            stack: list[tuple[int, int]] = [(x, y)]
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in comp or not real_map.in_bounds(_Coord(cx, cy)):
                    continue
                t = real_map.terrain_at(_Coord(cx, cy))
                if t not in {TerrainKind.LAND, TerrainKind.CITY}:
                    continue
                comp.add((cx, cy))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((cx + dx, cy + dy))
            visited.update(comp)
            continents.append((len(comp), comp))
    continents.sort(reverse=True)

    assigned: list[bool] = [False] * len(players)
    for size, comp in continents:
        del size
        cities_on = [c for c in cities if (c.coord.x, c.coord.y) in comp]
        if not cities_on:
            continue
        for i, player in enumerate(players):
            if assigned[i]:
                continue
            already_taken_continent = any(
                (other_city.coord.x, other_city.coord.y) in comp
                for other_city in cities
                if other_city.owner is not None and other_city.owner is not player
            )
            if already_taken_continent:
                continue
            cities_on[0].owner = player
            cities_on[0].production = type(cities_on[0].production)(
                building=None, work=0,
            )
            # Start them building Army (simplest default).
            from empire.core.unit import UnitKind
            cities_on[0].production.building = UnitKind.ARMY
            assigned[i] = True
            break
        if all(assigned):
            break

    bus = EventBus()
    events: dict[str, int] = {
        "turn_advanced": 0,
        "unit_placed": 0,
        "unit_moved": 0,
        "unit_removed": 0,
        "city_captured": 0,
    }
    bus.subscribe(TurnAdvancedEvent, lambda _e: events.__setitem__("turn_advanced", events["turn_advanced"] + 1))
    bus.subscribe(UnitPlacedEvent, lambda _e: events.__setitem__("unit_placed", events["unit_placed"] + 1))
    bus.subscribe(UnitMovedEvent, lambda _e: events.__setitem__("unit_moved", events["unit_moved"] + 1))
    bus.subscribe(UnitRemovedEvent, lambda _e: events.__setitem__("unit_removed", events["unit_removed"] + 1))
    bus.subscribe(CityCapturedEvent, lambda _e: events.__setitem__("city_captured", events["city_captured"] + 1))

    g = Game(
        rules=STANDARD,
        real_map=real_map,
        players=players,
        seed=seed,
        event_bus=bus,
        combat_resolver=CombatResolver(),
    )

    def _make_controller() -> AIController:
        if controller == "baseline":
            return BaselineAI()
        return NullController()

    for p in players:
        g.attach_controller(p.id, _make_controller())

    for _ in range(turns):
        g.run_turn()
        if g.is_over():
            break

    p1_cities = sum(1 for c in real_map.cities() if c.owner is players[0])
    p2_cities = sum(1 for c in real_map.cities() if c.owner is players[1])
    neutral = sum(1 for c in real_map.cities() if c.owner is None)
    total_units = sum(1 for _ in real_map.all_units())

    lines = [
        f"# {profile_name} seed={seed} after {g.turn} turns",
        f"# Cities: P1={p1_cities}  P2={p2_cities}  neutral={neutral}",
        f"# Total units on map: {total_units}",
        f"# Events: {events}",
    ]
    if show_final_map:
        lines.extend(["", render_ascii(real_map)])
    return "\n".join(lines)


# Local import alias to avoid name shadowing inside play_game.
from empire.core.coord import Coord as _Coord  # noqa: E402


def _launch_tui(profile_name: str, seed: int, *, auto_advance: float | None) -> None:
    """Build a game, attach controllers, and run `EmpireApp`."""
    from empire.ai.baseline import BaselineAI
    from empire.core.engine import scan_set_for_player
    from empire.events.bus import EventBus
    from empire.setup import build_game
    from empire.tui import EmpireApp, HumanController

    profile = _PROFILES[profile_name]
    bus = EventBus()
    # Build the game with the event bus wired in.
    game, players = build_game(
        profile,
        seed,
        p1_is_ai=auto_advance is not None,
        p2_is_ai=True,
    )
    game.event_bus = bus

    human_player = players[0]
    if auto_advance is None:
        human_ctrl = HumanController()
        game.attach_controller(human_player.id, human_ctrl)
    else:
        # Viewer mode: BaselineAI on both sides. The "human" slot still
        # owns the rendering perspective (P1's fog of war), but the
        # HumanController is a stub that never receives a plan.
        human_ctrl = HumanController()
        game.attach_controller(human_player.id, BaselineAI())
    game.attach_controller(players[1].id, BaselineAI())

    # Initial scan for the rendered player so the opening view isn't blank.
    scanned = scan_set_for_player(human_player, game.map)
    human_player.view.update_from_scan(scanned, game.map, game.turn)

    app = EmpireApp(
        game=game,
        human_player=human_player,
        human_controller=human_ctrl,
        event_bus=bus,
        auto_advance_seconds=auto_advance,
    )
    app.run()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="empire",
        description="Empire development CLI",
    )
    subs = parser.add_subparsers(dest="command")

    dump = subs.add_parser(
        "dump-map",
        help="Generate an ASCII map for visual inspection",
    )
    dump.add_argument(
        "--profile",
        choices=list(_PROFILES.keys()),
        default="STANDARD",
        help="Map profile to use (default: STANDARD)",
    )
    dump.add_argument(
        "--seed",
        type=int,
        default=0,
        help="RNG seed (default: 0)",
    )
    dump.add_argument(
        "--no-legend",
        action="store_true",
        help="Suppress the legend footer",
    )

    play = subs.add_parser(
        "play",
        help="Run a game for N turns with NullController on both sides",
    )
    play.add_argument(
        "--profile",
        choices=list(_PROFILES.keys()),
        default="STANDARD",
        help="Map profile to use (default: STANDARD)",
    )
    play.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")
    play.add_argument("--turns", type=int, default=10, help="Number of turns to run")
    play.add_argument(
        "--no-map",
        action="store_true",
        help="Skip rendering the final map",
    )
    play.add_argument(
        "--controller",
        choices=("null", "baseline"),
        default="null",
        help="AI controller for both players (default: null)",
    )

    play_tui = subs.add_parser(
        "play-tui",
        help="Launch the Textual TUI: human vs BaselineAI",
    )
    play_tui.add_argument(
        "--profile",
        choices=list(_PROFILES.keys()),
        default="SMALL",
        help="Map profile (default: SMALL)",
    )
    play_tui.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")

    viewer = subs.add_parser(
        "viewer",
        help="Watch BaselineAI vs BaselineAI in the TUI",
    )
    viewer.add_argument(
        "--profile",
        choices=list(_PROFILES.keys()),
        default="SMALL",
        help="Map profile (default: SMALL)",
    )
    viewer.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")
    viewer.add_argument(
        "--delay", type=float, default=0.4,
        help="Seconds between auto-advanced turns (default: 0.4)",
    )

    validate = subs.add_parser(
        "validate",
        help="Phase 10 gate: N seeded BaselineAI vs BaselineAI games + save/load check",
    )
    validate.add_argument(
        "--profile",
        choices=list(_PROFILES.keys()),
        default="SMALL",
        help="Map profile (default: SMALL — fast enough for 50+ games)",
    )
    validate.add_argument(
        "--games", type=int, default=50,
        help="Number of games to run (default: 50)",
    )
    validate.add_argument(
        "--turn-cap", type=int, default=500,
        help="Hard turn cap per game (default: 500)",
    )
    validate.add_argument(
        "--base-seed", type=int, default=0,
        help="First seed; later games use base_seed+i (default: 0)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. `argv=None` means use `sys.argv[1:]`."""
    if argv is None:
        argv = sys.argv[1:]
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "dump-map":
        print(dump_map(args.profile, args.seed, show_legend=not args.no_legend))
        return 0

    if args.command == "play":
        print(play_game(
            args.profile,
            args.seed,
            args.turns,
            show_final_map=not args.no_map,
            controller=args.controller,
        ))
        return 0

    if args.command == "validate":
        from empire._validation import run_validation

        profile = _PROFILES[args.profile]
        print(run_validation(
            profile=profile,
            num_games=args.games,
            turn_cap=args.turn_cap,
            base_seed=args.base_seed,
        ))
        return 0

    if args.command == "play-tui":
        _launch_tui(args.profile, args.seed, auto_advance=None)
        return 0

    if args.command == "viewer":
        _launch_tui(args.profile, args.seed, auto_advance=args.delay)
        return 0

    parser.print_help()
    return 0

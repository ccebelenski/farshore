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
    from empire.setup import land_continents

    continents = sorted(
        ((len(comp), comp) for comp in land_continents(real_map)), reverse=True
    )

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


def _launch_tui(
    profile_name: str,
    seed: int,
    *,
    auto_advance: float | None,
    opponent: str = "baseline",
    brawl: bool = False,
    fortified: bool = False,
) -> None:
    """Build a game per the flags, attach controllers, run `EmpireApp`.

    `brawl` puts both capitals on one continent — the meaningful way to
    playtest AIs on a shared continent.
    """
    from empire.ai.baseline import BaselineAI
    from empire.core.engine import refresh_player_view
    from empire.events.bus import EventBus
    from empire.tui import EmpireApp, HumanController
    from empire.tui.launching import GameConfig, GameLauncher

    if profile_name == "BRAWL":
        brawl = True  # the arena profile only makes sense as a brawl
        profile_name = "SMALL"  # launcher maps non-STANDARD/LARGE brawls to arena
    config = GameConfig(
        opponent=opponent,
        fortified=fortified,
        brawl=brawl,
        profile_name=profile_name,
        seed=seed,
    )
    try:
        launched = GameLauncher().build(config)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    game, human_player = launched.game, launched.human

    bus = EventBus()
    game.event_bus = bus
    human_ctrl = HumanController()
    if auto_advance is None:
        game.attach_controller(human_player.id, human_ctrl)
    else:
        # Viewer mode: AI on both sides. The "human" slot still owns the
        # rendering perspective (P1's fog of war), but the HumanController
        # is a stub that never receives a plan.
        human_player.is_ai = True
        game.attach_controller(human_player.id, BaselineAI())

    # Initial scan for the rendered player so the opening view isn't blank.
    refresh_player_view(human_player, game.map, game.turn)

    app = EmpireApp(
        game=game,
        human_player=human_player,
        human_controller=human_ctrl,
        event_bus=bus,
        auto_advance_seconds=auto_advance,
        opponent=opponent,
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

    subs.add_parser(
        "tui",
        help="Launch the game via the menu screen (new game / load / options)",
    )

    play_tui = subs.add_parser(
        "play-tui",
        help="Launch the Textual TUI directly into a game (flag-driven)",
    )
    play_tui.add_argument(
        "--profile",
        choices=[*_PROFILES.keys(), "BRAWL"],
        default="SMALL",
        help="Map profile (default: SMALL; BRAWL = the 28x18 arena profile)",
    )
    play_tui.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")
    play_tui.add_argument(
        "--opponent",
        choices=("baseline", "portfolio"),
        default="baseline",
        help="AI opponent (default: baseline)",
    )
    play_tui.add_argument(
        "--brawl",
        action="store_true",
        help="Shared-continent setup (required to actually fight a land-only "
             "AI like 'search'; classic setup puts capitals on separate "
             "continents)",
    )
    play_tui.add_argument(
        "--fortified",
        action="store_true",
        help="FORTIFIED_CITIES ruleset (city artillery); requires --brawl",
    )

    viewer = subs.add_parser(
        "viewer",
        help="Watch BaselineAI vs an AI opponent in the TUI",
    )
    viewer.add_argument(
        "--profile",
        choices=[*_PROFILES.keys(), "BRAWL"],
        default="SMALL",
        help="Map profile (default: SMALL; BRAWL = the 28x18 arena profile)",
    )
    viewer.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")
    viewer.add_argument(
        "--delay", type=float, default=0.4,
        help="Seconds between auto-advanced turns (default: 0.4)",
    )
    viewer.add_argument(
        "--opponent",
        choices=("baseline", "portfolio"),
        default="baseline",
        help="AI in the P2 slot (default: baseline)",
    )
    viewer.add_argument(
        "--brawl",
        action="store_true",
        help="Shared-continent setup",
    )
    viewer.add_argument(
        "--fortified",
        action="store_true",
        help="FORTIFIED_CITIES ruleset; requires --brawl",
    )

    validate = subs.add_parser(
        "validate",
        help="N seeded BaselineAI vs BaselineAI games + save/load check",
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

    if args.command == "tui":
        from empire.tui import EmpireApp

        EmpireApp().run()
        return 0

    if args.command == "play-tui":
        _launch_tui(
            args.profile, args.seed, auto_advance=None,
            opponent=args.opponent, brawl=args.brawl, fortified=args.fortified,
        )
        return 0

    if args.command == "viewer":
        _launch_tui(
            args.profile, args.seed, auto_advance=args.delay,
            opponent=args.opponent, brawl=args.brawl, fortified=args.fortified,
        )
        return 0

    parser.print_help()
    return 0

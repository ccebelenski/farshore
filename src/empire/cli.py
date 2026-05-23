"""Command-line utilities for development and playtesting.

Invoked via `python -m empire <command>`. Currently exposes:

  dump-map  Generate a map for a given profile/seed and print it as ASCII.
            Useful for eyeballing what `HeightFieldMapGenerator` produces.

Example:
    python -m empire dump-map --profile STANDARD --seed 0
    python -m empire dump-map --profile SMALL --seed 42 --no-legend
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

    parser.print_help()
    return 0

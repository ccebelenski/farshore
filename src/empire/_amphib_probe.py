"""Amphibious capability probe — does the SearchAI land-and-HOLD an invasion,
and if not, WHERE does the chain break?

The naval arena measures the outcome (ever-projected ~1/16) but not the cause.
This isolates the pipeline by REMOVING the scouting variable: it pre-reveals the
nearest overseas coastal city to the AI, so a `Role.INVADE` plan is available
from turn 0, then instruments every link of the chain:

    target known -> build >=2 transports -> load armies -> sail -> LAND (army on
    the target landmass) -> CAPTURE the city -> HOLD it to the end.

Run with --reveal vs without to separate "never finds a target" (scouting) from
"can't execute a known invasion" (pipeline). Defender is BaselineAI.

Run:  uv run python -m empire._amphib_probe --seeds 8 --cap 200 --fortified --reveal
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass

from empire._naval_arena import NAVAL_PROFILE, build_two_continent, _home_continent
from empire.ai.baseline import BaselineAI
from empire.ai.search import SearchAI
from empire.core.coord import Coord
from empire.core.map import RememberedTile
from empire.core.ruleset import FORTIFIED_CITIES, SMALL, STANDARD, MapProfile, RuleSet
from empire.core.unit import UnitKind
from empire.setup import land_continents

_PROFILES: dict[str, MapProfile] = {"small": SMALL, "naval": NAVAL_PROFILE}


@dataclass
class Probe:
    built: bool = False              # built >= 2 transports at any point
    loaded: bool = False             # >= 1 army ever aboard a transport
    landed: bool = False             # own army ever on the target landmass
    captured: bool = False           # target city ever became ours
    held_end: bool = False           # we still hold the target at the end
    transports_peak: int = 0


def _target_and_landmass(game, p0):
    """Nearest overseas (off-home) coastal city to p0's home, and the set of
    cells of the landmass it sits on. None if there is no such target."""
    home = _home_continent(game, p0)
    comps = land_continents(game.map)
    hx = sum(c.coord.x for c in game.map.cities() if c.owner is p0)
    hy = sum(c.coord.y for c in game.map.cities() if c.owner is p0)
    n = max(1, sum(1 for c in game.map.cities() if c.owner is p0))
    home_centroid = Coord(hx // n, hy // n)

    best = None
    best_d = 10**9
    for city in game.map.cities():
        if city.owner is p0:
            continue
        if (city.coord.x, city.coord.y) in home:
            continue
        # coastal?
        if not any(
            game.map.in_bounds(nb) and game.map.tile(nb).terrain.name == "WATER"
            for nb in city.coord.neighbors()
        ):
            continue
        d = city.coord.chebyshev_to(home_centroid)
        if d < best_d:
            best, best_d = city, d
    if best is None:
        return None, None
    landmass = next(
        (comp for comp in comps if (best.coord.x, best.coord.y) in comp), set()
    )
    return best, landmass


def _reveal(p0, game, target: Coord) -> None:
    """Drop the target city + its ring into p0's remembered map so the generator
    treats it as a known ocean-coastal invade target from the start."""
    for c in (target, *target.neighbors()):
        if not game.map.in_bounds(c):
            continue
        p0.view.remembered[c] = RememberedTile(
            coord=c, terrain=game.map.tile(c).terrain, remembered_at=0
        )


def run_one(
    seed: int, cap: int, rules: RuleSet, profile: MapProfile, reveal: bool
) -> Probe | None:
    built = build_two_continent(profile, seed, rules)
    if built is None:
        return None
    game, players = built
    p0, p1 = players
    game.attach_controller(p0.id, SearchAI())
    game.attach_controller(p1.id, BaselineAI())

    target, landmass = _target_and_landmass(game, p0)
    if target is None:
        return None  # no overseas coastal target on this map; skip
    tcoord = target.coord
    if reveal:
        _reveal(p0, game, tcoord)

    pr = Probe()
    for _ in range(cap):
        game.run_turn()
        transports = [
            u for u in game.map.all_units()
            if u.owner is p0 and u.kind is UnitKind.TRANSPORT
        ]
        pr.transports_peak = max(pr.transports_peak, len(transports))
        if len(transports) >= 2:
            pr.built = True
        if any(t.cargo for t in transports):
            pr.loaded = True
        if landmass and any(
            u.owner is p0 and u.kind is UnitKind.ARMY
            and (u.coord.x, u.coord.y) in landmass
            for u in game.map.all_units()
        ):
            pr.landed = True
        city_now = game.map.city_by_id(target.id)
        if city_now is not None and city_now.owner is p0:
            pr.captured = True
        if game.is_over():
            break
    city_end = game.map.city_by_id(target.id)
    pr.held_end = city_end is not None and city_end.owner is p0
    return pr


def main() -> None:
    ap = argparse.ArgumentParser(description="Amphibious land-and-hold capability probe")
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--cap", type=int, default=200)
    ap.add_argument("--fortified", action="store_true")
    ap.add_argument("--reveal", action="store_true", help="pre-reveal the target (remove scouting)")
    ap.add_argument("--profile", choices=tuple(_PROFILES), default="naval")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 1) - 1))
    args = ap.parse_args()
    rules = FORTIFIED_CITIES if args.fortified else STANDARD
    profile = _PROFILES[args.profile]

    print(
        f"amphib probe: seeds={args.seeds} cap={args.cap} "
        f"rules={'FORTIFIED' if rules is FORTIFIED_CITIES else 'STANDARD'} "
        f"reveal={args.reveal} profile={args.profile}",
        flush=True,
    )
    agg = {k: 0 for k in ("n", "built", "loaded", "landed", "captured", "held_end")}
    start = time.time()

    def consume(pr: Probe | None) -> None:
        if pr is None:
            return
        agg["n"] += 1
        for k in ("built", "loaded", "landed", "captured", "held_end"):
            agg[k] += int(getattr(pr, k))

    if args.jobs <= 1:
        for s in range(args.seeds):
            consume(run_one(s, args.cap, rules, profile, args.reveal))
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = [
                ex.submit(run_one, s, args.cap, rules, profile, args.reveal)
                for s in range(args.seeds)
            ]
            for f in as_completed(futs):
                consume(f.result())

    n = agg["n"]
    print(f"\n{n} games with an overseas coastal target ({time.time()-start:.0f}s):")
    if n:
        for k in ("built", "loaded", "landed", "captured", "held_end"):
            print(f"  {k:10s}: {agg[k]}/{n}  ({100*agg[k]/n:.0f}%)")
        print("  (the first big drop between stages is where the pipeline breaks)")


if __name__ == "__main__":
    main()

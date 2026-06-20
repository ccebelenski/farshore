"""Aggression A/B: SearchAI(aggression>0) vs SearchAI(aggression=0), head to
head, to validate the aggression-bias-with-reversion change
(planning/06-aggression-bias.md).

Aggression-off is the exact pre-change behavior, so this is a clean controlled
A/B — the only difference between the two players is the temperament scalar.

Two maps, two questions:
  * two-continent (SMALL/NAVAL): does aggression BREAK THE STALEMATE? The plain
    AI can't project across water (~6% / 0 in the diagnosis), so head-to-head it
    can only draw/stalemate. If aggression converts those into wins + off-home
    captures, the bootstrap fix works.
  * land-brawl (ARENA): regression guard (the standing lesson — any shared
    selection change must not hurt the land game). On a shared continent the
    aggressive side should at least hold even vs its plain self.

Run:  uv run python -m empire._aggr_ab --map naval-small --seeds 8 --fortified --jobs 14
      uv run python -m empire._aggr_ab --map land-brawl --seeds 12 --jobs 14
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass

from empire._arena import ARENA_PROFILE, build_land_brawl
from empire._naval_arena import NAVAL_PROFILE, build_two_continent, _home_continent
from empire.ai.search import SearchAI
from empire.ai.search.ai import DEFAULT_AGGRESSION
from empire.core.player import Player
from empire.core.ruleset import FORTIFIED_CITIES, SMALL, STANDARD, MapProfile, RuleSet

_MAPS: dict[str, tuple[str, MapProfile]] = {
    "naval-small": ("two", SMALL),
    "naval-arena": ("two", NAVAL_PROFILE),
    "land-brawl": ("land", ARENA_PROFILE),
}


@dataclass
class Result:
    aggr: int = 0
    plain: int = 0
    draw: int = 0
    unfinished: int = 0


def _build(kind: str, profile: MapProfile, seed: int, rules: RuleSet):
    if kind == "two":
        return build_two_continent(profile, seed, rules)
    return build_land_brawl(profile, seed, rules)


def play_match(
    map_key: str, seed: int, aggr_first: bool, cap: int, rules: RuleSet, aggression: float
) -> tuple[str, int, int] | None:
    """One game; return (winner, turns, aggr_peak_off_home). winner in
    {'aggr','plain','draw','unfinished'}."""
    kind, profile = _MAPS[map_key]
    built = _build(kind, profile, seed, rules)
    if built is None:
        return None
    game, players = built
    ai = 0 if aggr_first else 1
    game.attach_controller(players[ai].id, SearchAI(aggression=aggression))
    game.attach_controller(players[1 - ai].id, SearchAI(aggression=0.0))
    aggr_player: Player = players[ai]

    # Off-home projection telemetry only makes sense on the two-continent map.
    home: set[tuple[int, int]] | None = None
    if kind == "two":
        home = _home_continent(game, aggr_player)

    def off_home() -> int:
        if home is None:
            return 0
        return sum(
            1 for c in game.map.cities()
            if c.owner is aggr_player and (c.coord.x, c.coord.y) not in home
        )

    peak_off = 0
    for _ in range(cap):
        game.run_turn()
        peak_off = max(peak_off, off_home())
        if game.is_over():
            break

    if not game.is_over():
        return ("unfinished", game.turn, peak_off)
    w = game.winner()
    if w is None:
        return ("draw", game.turn, peak_off)
    return ("aggr" if w is aggr_player else "plain", game.turn, peak_off)


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggression A/B (search vs search)")
    ap.add_argument("--map", choices=tuple(_MAPS), default="naval-small")
    ap.add_argument("--seeds", type=int, default=8, help="seeds, each played both ways")
    ap.add_argument("--cap", type=int, default=250)
    ap.add_argument("--aggression", type=float, default=DEFAULT_AGGRESSION)
    ap.add_argument("--fortified", action="store_true")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 1) - 1))
    args = ap.parse_args()
    rules = FORTIFIED_CITIES if args.fortified else STANDARD

    res = Result()
    ever_projected = 0
    held = 0
    start = time.time()
    specs = [(s, cf) for s in range(args.seeds) for cf in (True, False)]

    def consume(out: tuple[str, int, int] | None) -> None:
        nonlocal ever_projected, held
        if out is None:
            return
        label, _turns, peak_off = out
        setattr(res, label, getattr(res, label) + 1)
        if peak_off > 0:
            ever_projected += 1

    print(
        f"A/B map={args.map} rules={'FORTIFIED' if rules is FORTIFIED_CITIES else 'STANDARD'} "
        f"aggression={args.aggression} seeds={args.seeds} (x2 sides) cap={args.cap}",
        flush=True,
    )
    if args.jobs <= 1:
        for seed, cf in specs:
            consume(play_match(args.map, seed, cf, args.cap, rules, args.aggression))
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = [
                ex.submit(play_match, args.map, s, cf, args.cap, rules, args.aggression)
                for s, cf in specs
            ]
            step = max(1, len(futs) // 10)
            for done, f in enumerate(as_completed(futs), 1):
                consume(f.result())
                if done % step == 0 or done == len(futs):
                    print(
                        f"  {done}/{len(futs)}: aggr={res.aggr} plain={res.plain} "
                        f"draw={res.draw} unfin={res.unfinished} "
                        f"projected={ever_projected} ({time.time()-start:.0f}s)",
                        flush=True,
                    )

    decided = res.aggr + res.plain
    total = decided + res.draw + res.unfinished
    print(
        f"\nRESULT: aggr={res.aggr} plain={res.plain} draw={res.draw} "
        f"unfinished={res.unfinished}  (n={total})"
    )
    if decided:
        print(f"  decided games: aggression wins {res.aggr}/{decided} "
              f"({100*res.aggr/decided:.0f}%)")
    print(f"  games where aggression ever projected off-home: {ever_projected}/{total}")


if __name__ == "__main__":
    main()

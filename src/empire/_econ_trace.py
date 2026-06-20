"""Economy trace: per-turn military/economy trajectory of both sides in a
two-continent game, to diagnose the playtest finding that the SearchAI ends
with *zero units* against a competent opponent.

The naval arena (`empire._naval_arena`) pits the challenger against a land-only
BaselineAI that can never reach the challenger's home island, so it measures
offense only — it structurally cannot reproduce "the enemy landed and wiped me
out." This runs SearchAI vs SearchAI (self-play) on the same two-continent
setup `build_game` produces, so BOTH sides can project force, and records each
side's cities / armies / ships / fighters / total-alive and a cumulative
units-ever-built count per turn. The question it answers: does the loser ever
field a standing army, and if so, at what turn does it collapse?

Read-only — never changes a decision.

Run:  uv run python -m empire._econ_trace --seed 16 --cap 250 --fortified
      uv run python -m empire._econ_trace --seeds 6 --cap 250 --fortified --mode selfplay
"""

from __future__ import annotations

import argparse

from empire._naval_arena import (
    NAVAL_PROFILE,
    build_two_continent,
    _challenger,
    _home_continent,
)
from empire.ai.baseline import BaselineAI
from empire.ai.search import SearchAI
from empire.ai.search.naval import SEA_KINDS
from empire.core.game import Game
from empire.core.player import Player
from empire.core.ruleset import FORTIFIED_CITIES, SMALL, STANDARD, MapProfile, RuleSet
from empire.core.unit import UnitKind

# play-tui's default board (50x30, 10 cities) — what the human playtest used —
# vs the compact 28x18 arena map. The big board has more water to cross and
# fewer cities per area, so consolidation + projection look different on it.
_PROFILES: dict[str, MapProfile] = {"small": SMALL, "naval": NAVAL_PROFILE}


def _counts(game: Game, who: Player) -> dict[str, int]:
    cities = sum(1 for c in game.map.cities() if c.owner is who)
    army = ships = fighters = total = 0
    for u in game.map.all_units():
        if u.owner is not who:
            continue
        total += 1
        if u.kind is UnitKind.ARMY:
            army += 1
        elif u.kind is UnitKind.FIGHTER:
            fighters += 1
        elif u.kind in SEA_KINDS:
            ships += 1
    return {"cities": cities, "army": army, "ships": ships, "air": fighters, "alive": total}


def trace_game(
    seed: int, cap: int, rules: RuleSet, mode: str, verbose: bool,
    profile: MapProfile = NAVAL_PROFILE,
    aggression: float | None = None,
) -> dict[str, object] | None:
    """Run one game, sampling both sides' economy every turn. Returns a summary
    dict; prints a per-turn table when `verbose`. `aggression` overrides the
    SearchAI temperament on BOTH search sides (None = library default)."""
    built = build_two_continent(profile, seed, rules)
    if built is None:
        return None
    game, players = built
    p0, p1 = players

    def _search():
        return SearchAI() if aggression is None else SearchAI(aggression=aggression)

    game.attach_controller(p0.id, _search())
    if mode == "selfplay":
        game.attach_controller(p1.id, _search())
    else:
        game.attach_controller(p1.id, BaselineAI())

    # Cumulative units-ever-seen per side, to separate "never built" from "built
    # then lost": a rising-then-crashing alive-count with a high ever-built means
    # it fielded an army and lost it; a flat-low ever-built means it never built.
    ever: dict[int, set[int]] = {0: set(), 1: set()}

    def record_ever() -> None:
        for u in game.map.all_units():
            idx = 0 if u.owner is p0 else 1
            ever[idx].add(int(u.id))

    peak = {0: 0, 1: 0}  # peak armies alive
    at100 = {0: 0, 1: 0}  # armies alive at ~t100 (when the human invaded)
    record_ever()
    if verbose:
        print(
            f"\n=== seed {seed}  mode={mode}  rules="
            f"{'FORTIFIED' if rules is FORTIFIED_CITIES else 'STANDARD'} ===",
            flush=True,
        )
        print(
            "turn | P0 cit arm shp air alv built | "
            "P1 cit arm shp air alv built",
            flush=True,
        )

    for _ in range(cap):
        game.run_turn()
        record_ever()
        c0, c1 = _counts(game, p0), _counts(game, p1)
        peak[0] = max(peak[0], c0["army"])
        peak[1] = max(peak[1], c1["army"])
        if game.turn == 100:
            at100 = {0: c0["army"], 1: c1["army"]}
        if verbose and (game.turn % 10 == 0 or game.is_over()):
            print(
                f"{game.turn:4d} | "
                f"{c0['cities']:3d} {c0['army']:3d} {c0['ships']:3d} {c0['air']:3d} "
                f"{c0['alive']:3d} {len(ever[0]):5d} | "
                f"{c1['cities']:3d} {c1['army']:3d} {c1['ships']:3d} {c1['air']:3d} "
                f"{c1['alive']:3d} {len(ever[1]):5d}",
                flush=True,
            )
        if game.is_over():
            break

    winner = game.winner() if game.is_over() else None
    win_idx = None if winner is None else (0 if winner is p0 else 1)
    final0, final1 = _counts(game, p0), _counts(game, p1)
    summary = {
        "seed": seed,
        "turns": game.turn,
        "over": game.is_over(),
        "winner": win_idx,
        "peak_army": dict(peak),
        "army_t100": dict(at100),
        "ever_built": {0: len(ever[0]), 1: len(ever[1])},
        "final": {0: final0, 1: final1},
    }
    if verbose:
        print(
            f"  -> winner={win_idx} turns={game.turn} "
            f"peak_army={peak} ever_built={summary['ever_built']} "
            f"final_alive=({final0['alive']},{final1['alive']})",
            flush=True,
        )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-turn economy trace (diagnosis)")
    ap.add_argument("--seed", type=int, default=None, help="single seed (verbose)")
    ap.add_argument("--seeds", type=int, default=6, help="seeds 0..N-1 (summary)")
    ap.add_argument("--cap", type=int, default=250)
    ap.add_argument("--fortified", action="store_true")
    ap.add_argument(
        "--mode", choices=("selfplay", "vs-baseline"), default="selfplay"
    )
    ap.add_argument(
        "--profile", choices=tuple(_PROFILES), default="small",
        help="map profile: 'small' = play-tui's 50x30 board (default), "
        "'naval' = the 28x18 arena board",
    )
    ap.add_argument(
        "--aggression", type=float, default=None,
        help="override SearchAI aggression on both sides (default: library default)",
    )
    args = ap.parse_args()
    rules = FORTIFIED_CITIES if args.fortified else STANDARD
    profile = _PROFILES[args.profile]

    if args.seed is not None:
        trace_game(args.seed, args.cap, rules, args.mode, verbose=True,
                   profile=profile, aggression=args.aggression)
        return

    # Summary sweep: how often does the loser end with ~zero army, and did it
    # ever build one?
    print(
        f"sweep: {args.seeds} seeds, mode={args.mode}, profile={args.profile}, "
        f"cap={args.cap}",
        flush=True,
    )
    no_navy = 0  # games where NEITHER side ever built a ship
    decided = 0
    total = 0
    for seed in range(args.seeds):
        s = trace_game(seed, args.cap, rules, args.mode, verbose=False,
                       profile=profile, aggression=args.aggression)
        if s is None:
            continue
        total += 1
        f0 = s["final"][0]  # type: ignore[index]
        f1 = s["final"][1]  # type: ignore[index]
        ships_built = f0["ships"] + f1["ships"]  # final-frame proxy
        if ships_built == 0:
            no_navy += 1
        win = s["winner"]
        if win is not None:
            decided += 1
        a100 = s["army_t100"]  # type: ignore[index]
        pk = s["peak_army"]  # type: ignore[index]
        print(
            f"  seed {seed}: winner={('P' + str(win)) if win is not None else 'NONE'} "
            f"turns={s['turns']} army@100=({a100[0]},{a100[1]}) "
            f"peak_army=({pk[0]},{pk[1]}) "
            f"final_ships=({f0['ships']},{f1['ships']}) "
            f"final_cities=({f0['cities']},{f1['cities']})",
            flush=True,
        )
    if total:
        print(
            f"\n{total} games ({decided} decided): "
            f"both sides ended with 0 ships in {no_navy}/{total} "
            f"(stalemate signature: no navy => no projection => no winner)",
            flush=True,
        )


if __name__ == "__main__":
    main()

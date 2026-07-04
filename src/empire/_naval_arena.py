"""Multi-continent arena: the validation gate for naval/air projection
(Phase 15.9).

Unlike the land-brawl arena (`empire._arena`), which drops both capitals on
ONE continent so land AIs can fight, this places them on SEPARATE landmasses
with open ocean between — the setup `build_game` already produces (spec §9.2).
A land-only AI is physically stuck on its island here, so the *only* way to
win is to project force across the water. That makes win-rate (and the
"captured a city off my home continent" telemetry) a direct measure of naval
capability: it starts at zero for every current AI and is what the naval work
must drive up.

Run:  `python -m empire._naval_arena --seeds 25 --cap 400 --ai search`

A private CLI helper, like `empire._arena` / `empire._validation`.
"""

from __future__ import annotations

import argparse
import os
import time

from empire._arena import ArenaResult
from empire.ai.baseline import BaselineAI
from empire.ai.search.naval import SEA_KINDS
from empire.contracts.controller import AIController
from empire.core.game import Game
from empire.core.player import Player
from empire.core.ruleset import FORTIFIED_CITIES, STANDARD, MapProfile, RuleSet
from empire.core.unit import UnitKind
from empire.setup import build_game, land_continents

# Sea unit kinds (telemetry: did the challenger build a navy at all).
_SHIP_KINDS = SEA_KINDS


def _challenger(kind: str) -> AIController:
    """The AI under test on the naval gate. ('strategic' retired.)"""
    del kind
    from empire.ai.search import SearchAI

    return SearchAI()

# Two-continent map: enough water to separate the landmasses, enough cities
# that each continent is worth contesting. Verified to build 40/40 seeds with
# both home continents holding >= 3 cities.
NAVAL_PROFILE = MapProfile(
    width=28, height=18, water_ratio=50, smooth_iterations=5,
    num_cities=12, min_city_distance=3,
)


def build_two_continent(
    profile: MapProfile, seed: int, rules: RuleSet = STANDARD
) -> tuple[Game, list[Player]] | None:
    """A game with the two capitals on separate ocean-coastal continents.

    Reuses `build_game` (which already enforces capital eligibility — a
    continent with >= 3 cities and an ocean coast — per player). Returns
    None if no qualifying map turns up for this seed."""
    try:
        return build_game(profile, seed, p1_is_ai=True, p2_is_ai=True, rules=rules)
    except RuntimeError:
        return None


def _home_continent(game: Game, player: Player) -> set[tuple[int, int]]:
    cap = next(c for c in game.map.cities() if c.owner is player)
    return next(
        comp for comp in land_continents(game.map)
        if (cap.coord.x, cap.coord.y) in comp
    )


def play_match(
    profile: MapProfile, seed: int, challenger_first: bool, cap: int,
    rules: RuleSet = STANDARD, ai: str = "search",
) -> tuple[str, int, int, int, int, int] | None:
    """Run one game; return (outcome, turns, peak_off_home, end_off_home,
    peak_ships, peak_fighters).
    Outcome is 'strategic' | 'baseline' | 'draw' | 'unfinished' (labels match
    the land-brawl arena so `ArenaResult` is reusable).

    Two projection telemetry numbers, because they answer different questions:
    `peak_off_home` is the most off-home cities the challenger held at ANY turn
    (> 0 means it crossed water and took ground at least once — measures defect
    (a), did it ever project); `end_off_home` is what it still holds at the cap
    (> 0 means the beachhead stuck — measures defect (b), sustained waves). The
    old end-only metric read 0 for a game that captured and then lost a city
    the next turn, hiding all early naval progress."""
    built = build_two_continent(profile, seed, rules)
    if built is None:
        return None
    game, players = built
    ci = 0 if challenger_first else 1
    game.attach_controller(players[ci].id, _challenger(ai))
    game.attach_controller(players[1 - ci].id, BaselineAI())
    chal = players[ci]
    home = _home_continent(game, chal)

    def off_home_now() -> int:
        return sum(
            1 for c in game.map.cities()
            if c.owner is chal and (c.coord.x, c.coord.y) not in home
        )

    def own_count(kind: UnitKind) -> int:
        return sum(
            1 for u in game.map.all_units()
            if u.owner is chal and u.kind is kind
        )

    peak_off_home = 0
    peak_ships = 0  # any sea unit (transport/patrol/warship) — naval involvement
    peak_fighters = 0  # air involvement
    for _ in range(cap):
        game.run_turn()
        peak_off_home = max(peak_off_home, off_home_now())
        peak_ships = max(peak_ships, sum(own_count(k) for k in _SHIP_KINDS))
        peak_fighters = max(peak_fighters, own_count(UnitKind.FIGHTER))
        if game.is_over():
            break

    end_off_home = off_home_now()
    extra = (peak_ships, peak_fighters)
    if not game.is_over():
        return ("unfinished", game.turn, peak_off_home, end_off_home, *extra)
    winner = game.winner()
    if winner is None:
        return ("draw", game.turn, peak_off_home, end_off_home, *extra)
    label = "strategic" if winner is chal else "baseline"
    return (label, game.turn, peak_off_home, end_off_home, *extra)


def run_arena(
    seeds: int, cap: int, profile: MapProfile = NAVAL_PROFILE, verbose: bool = True,
    rules: RuleSet = STANDARD, jobs: int = 1, ai: str = "search",
) -> tuple[ArenaResult, int, int, int, int]:
    """Each seed played twice (sides swapped) to cancel which side gets the
    larger continent. Returns the win/loss tally plus projection counts
    (`ever_projected`, `held_at_cap`) and involvement counts (`built_navy`,
    `built_air` — games where the challenger built any ship / any fighter, so we
    can SEE naval/air are actually being used, not just the win outcome)."""
    result = ArenaResult()
    turns: list[int] = []
    ever_projected = 0
    held_at_cap = 0
    built_navy = 0
    built_air = 0
    start = time.time()

    def consume(outcome: tuple[str, int, int, int, int, int] | None) -> None:
        nonlocal ever_projected, held_at_cap, built_navy, built_air
        if outcome is None:
            return
        label, played, peak_off_home, end_off_home, peak_ships, peak_fighters = outcome
        setattr(result, label, getattr(result, label) + 1)
        if label in ("strategic", "baseline"):
            turns.append(played)
        if peak_off_home > 0:
            ever_projected += 1
        if end_off_home > 0:
            held_at_cap += 1
        if peak_ships > 0:
            built_navy += 1
        if peak_fighters > 0:
            built_air += 1

    specs = [(seed, cf) for seed in range(seeds) for cf in (True, False)]
    if jobs <= 1:
        for seed, cf in specs:
            consume(play_match(profile, seed, cf, cap, rules, ai))
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futures = [
                ex.submit(play_match, profile, seed, cf, cap, rules, ai)
                for seed, cf in specs
            ]
            step = max(1, len(futures) // 10)
            for done, fut in enumerate(as_completed(futures), 1):
                consume(fut.result())
                if verbose and (done % step == 0 or done == len(futures)):
                    print(
                        f"  {done}/{len(futures)} games: S={result.strategic} "
                        f"B={result.baseline} draw={result.draw} "
                        f"unfin={result.unfinished} ever={ever_projected} "
                        f"held={held_at_cap} navy={built_navy} air={built_air} "
                        f"({time.time() - start:.0f}s)",
                        flush=True,
                    )

    result.mean_turns = sum(turns) / len(turns) if turns else 0.0
    return result, ever_projected, held_at_cap, built_navy, built_air


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Multi-continent (naval) arena: challenger vs land-only BaselineAI"
    )
    parser.add_argument("--seeds", type=int, default=25, help="seeds (each played both ways)")
    parser.add_argument("--cap", type=int, default=400, help="per-game turn cap (naval is slow)")
    parser.add_argument(
        "--ai", choices=("search",), default="search",
        help="the challenger under test",
    )
    parser.add_argument(
        "--fortified", action="store_true",
        help="use the FORTIFIED_CITIES ruleset instead of STANDARD",
    )
    parser.add_argument(
        "--jobs", type=int, default=max(1, (os.cpu_count() or 1) - 1),
        help="parallel worker processes (default: cores-1; 1 = sequential)",
    )
    args = parser.parse_args()

    rules = FORTIFIED_CITIES if args.fortified else STANDARD
    print(f"ruleset: {rules.name}  jobs: {args.jobs}  ai: {args.ai}  cap: {args.cap}")
    result, ever_projected, held_at_cap, built_navy, built_air = run_arena(
        args.seeds, args.cap, rules=rules, jobs=args.jobs, ai=args.ai
    )
    total = 2 * args.seeds
    print(f"\n{total} games: S={result.strategic} B={result.baseline} "
          f"draw={result.draw} unfinished={result.unfinished}")
    print(f"involvement — built a navy (any ship): {built_navy}/{total} games; "
          f"built air (any fighter): {built_air}/{total} games")
    print(f"projection — ever crossed water (held an off-home city at any "
          f"turn): {ever_projected}/{total} games")
    print(f"projection — beachhead stuck (still held at cap): "
          f"{held_at_cap}/{total} games")
    if result.decided:
        print(f"{args.ai} win-rate among decided: {result.strategic}/{result.decided} "
              f"= {result.strategic / result.decided:.1%}")
        print(f"one-sided binomial p (better than baseline): {result.binomial_p():.4f}")
    print(f"mean decided-game length: {result.mean_turns:.0f} turns")


if __name__ == "__main__":
    main()

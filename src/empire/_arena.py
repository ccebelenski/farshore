"""Land-brawl arena: head-to-head `StrategicAI` vs `BaselineAI` on a *shared*
continent, with sides swapped per seed and a binomial significance test.

This is the tuning instrument for the Phase-15.5 force-economy redesign (see
`planning/05-implementation-plan.md`). Unlike `build_game`, which places
capitals on *separate* continents (spec §9.2), this drops both capitals on the
largest landmass so the two AIs can actually fight on land — isolating "is the
strategy smarter?" from "can it sail?".

Run:  `python -m empire._arena --seeds 25 --cap 250`

Not a layered package — a private CLI helper, like `empire._validation`.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import time
from dataclasses import dataclass

from empire.ai.baseline import BaselineAI
from empire.ai.strategic.ai import StrategicAI
from empire.combat.resolver import CombatResolver
from empire.contracts.controller import AIController
from empire.core.city import City
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.map import ViewMap
from empire.core.player import Player
from empire.core.ruleset import FORTIFIED_CITIES, STANDARD, MapProfile, RuleSet
from empire.core.unit import UnitKind
from empire.mapgen.height_field import HeightFieldMapGenerator
from empire.setup import land_continents

# A compact map whose largest continent reliably holds a dozen-ish cities, so
# both capitals + a contestable pool of neutrals share one landmass.
ARENA_PROFILE = MapProfile(
    width=28, height=18, water_ratio=40, smooth_iterations=5,
    num_cities=10, min_city_distance=3,
)
_GEN_ATTEMPTS = 80
_MIN_CONTINENT_CITIES = 5


def _farthest_pair(cities: list[City]) -> tuple[City, City]:
    best: tuple[City, City] = (cities[0], cities[1])
    best_d = -1
    for i in range(len(cities)):
        for j in range(i + 1, len(cities)):
            d = cities[i].coord.chebyshev_to(cities[j].coord)
            if d > best_d:
                best_d, best = d, (cities[i], cities[j])
    return best


def build_land_brawl(
    profile: MapProfile, seed: int, rules: RuleSet = STANDARD
) -> tuple[Game, list[Player]] | None:
    """A game with both capitals on the largest continent, far apart. None if
    no map with a big-enough shared continent turns up. `rules` selects the
    ruleset (STANDARD vs FORTIFIED_CITIES) for the A/B artillery comparison."""
    gen = HeightFieldMapGenerator()
    master = random.Random(seed)
    for _ in range(_GEN_ATTEMPTS):
        try:
            real_map, cities = gen.generate(profile, random.Random(master.randrange(2**31)))
        except Exception:
            continue
        components = land_continents(real_map)
        if not components:
            continue
        largest = max(components, key=len)
        on_continent = [c for c in cities if (c.coord.x, c.coord.y) in largest]
        if len(on_continent) < _MIN_CONTINENT_CITIES:
            continue
        cap1, cap2 = _farthest_pair(on_continent)
        players = [
            Player(id=PlayerId(1), name="P1", is_ai=True, view=ViewMap(), color="red"),
            Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap(), color="blue"),
        ]
        for cap, player in ((cap1, players[0]), (cap2, players[1])):
            cap.owner = player
            cap.production.building = UnitKind.ARMY
            cap.production.work = 0
        game = Game(
            rules=rules, real_map=real_map, players=players,
            seed=seed, combat_resolver=CombatResolver(),
        )
        return game, players
    return None


def _smart_ai(kind: str) -> AIController:
    """The challenger under test: 'strategic' (Phase 15) or 'search' (15.8)."""
    if kind == "search":
        from empire.ai.search import SearchAI

        return SearchAI()
    return StrategicAI()


def play_match(
    profile: MapProfile, seed: int, strategic_first: bool, cap: int,
    rules: RuleSet = STANDARD, ai: str = "strategic",
) -> tuple[str, int] | None:
    """Run one game; return (outcome, turns). Outcome is
    'strategic' | 'baseline' | 'draw' | 'unfinished' ('strategic' = the
    challenger named by `ai`, whichever implementation that is)."""
    built = build_land_brawl(profile, seed, rules)
    if built is None:
        return None
    game, players = built
    si = 0 if strategic_first else 1
    strategic: AIController = _smart_ai(ai)
    baseline: AIController = BaselineAI()
    game.attach_controller(players[si].id, strategic)
    game.attach_controller(players[1 - si].id, baseline)
    strat_player = players[si]

    for _ in range(cap):
        game.run_turn()
        if game.is_over():
            break
    if not game.is_over():
        return ("unfinished", game.turn)
    winner = game.winner()
    if winner is None:
        return ("draw", game.turn)
    return ("strategic" if winner is strat_player else "baseline", game.turn)


@dataclass
class ArenaResult:
    strategic: int = 0
    baseline: int = 0
    draw: int = 0
    unfinished: int = 0
    mean_turns: float = 0.0

    @property
    def decided(self) -> int:
        return self.strategic + self.baseline

    def binomial_p(self) -> float:
        """One-sided P(X >= strategic wins | decided, p=0.5): the chance of
        doing this well or better by coin flip. Small ⇒ genuinely better."""
        k, n = self.strategic, self.decided
        if n == 0:
            return 1.0
        return sum(math.comb(n, i) for i in range(k, n + 1)) / 2**n


def run_arena(
    seeds: int, cap: int, profile: MapProfile = ARENA_PROFILE, verbose: bool = True,
    rules: RuleSet = STANDARD, jobs: int = 1, ai: str = "strategic",
) -> ArenaResult:
    """Each seed played twice (sides swapped) to cancel positional bias.

    Every match is a pure function of (profile, seed, side, cap, rules) — map
    gen and the game RNG are both seeded from `seed` — so playing them across
    `jobs` processes is bit-identical to sequential, just faster. Matches are
    CPU-bound (the GIL rules out threads) and tiny in memory, so the worker
    count can safely be the core count. `jobs <= 1` keeps the in-process path.
    """
    result = ArenaResult()
    turns: list[int] = []
    start = time.time()

    def consume(outcome: tuple[str, int] | None) -> None:
        if outcome is None:
            return
        label, played = outcome
        setattr(result, label, getattr(result, label) + 1)
        if label in ("strategic", "baseline"):
            turns.append(played)

    specs = [(seed, sf) for seed in range(seeds) for sf in (True, False)]
    if jobs <= 1:
        for seed, sf in specs:
            consume(play_match(profile, seed, sf, cap, rules, ai))
            if verbose and sf is False:  # both sides of this seed done
                print(
                    f"  seed {seed}: S={result.strategic} B={result.baseline} "
                    f"draw={result.draw} unfin={result.unfinished} "
                    f"({time.time() - start:.0f}s)",
                    flush=True,
                )
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futures = [
                ex.submit(play_match, profile, seed, sf, cap, rules, ai)
                for seed, sf in specs
            ]
            step = max(1, len(futures) // 10)
            for done, fut in enumerate(as_completed(futures), 1):
                consume(fut.result())
                if verbose and (done % step == 0 or done == len(futures)):
                    print(
                        f"  {done}/{len(futures)} games: S={result.strategic} "
                        f"B={result.baseline} draw={result.draw} "
                        f"unfin={result.unfinished} ({time.time() - start:.0f}s)",
                        flush=True,
                    )

    result.mean_turns = sum(turns) / len(turns) if turns else 0.0
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="StrategicAI vs BaselineAI land-brawl arena")
    parser.add_argument("--seeds", type=int, default=25, help="seeds (each played both ways)")
    parser.add_argument("--cap", type=int, default=250, help="per-game turn cap")
    parser.add_argument(
        "--fortified", action="store_true",
        help="use the FORTIFIED_CITIES ruleset (city artillery) instead of STANDARD",
    )
    parser.add_argument(
        "--jobs", type=int, default=max(1, (os.cpu_count() or 1) - 1),
        help="parallel worker processes (default: cores-1; 1 = sequential)",
    )
    parser.add_argument(
        "--ai", choices=("strategic", "search"), default="strategic",
        help="the challenger to pit against BaselineAI",
    )
    args = parser.parse_args()

    rules = FORTIFIED_CITIES if args.fortified else STANDARD
    print(f"ruleset: {rules.name}  jobs: {args.jobs}  ai: {args.ai}")
    r = run_arena(args.seeds, args.cap, rules=rules, jobs=args.jobs, ai=args.ai)
    print(f"\n{2 * args.seeds} games: S={r.strategic} B={r.baseline} "
          f"draw={r.draw} unfinished={r.unfinished}")
    if r.decided:
        print(f"{args.ai} win-rate among decided: {r.strategic}/{r.decided} "
              f"= {r.strategic / r.decided:.1%}")
        print(f"one-sided binomial p (better than baseline): {r.binomial_p():.4f}")
    print(f"mean decided-game length: {r.mean_turns:.0f} turns")


if __name__ == "__main__":
    main()

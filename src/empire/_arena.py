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
from empire.core.ruleset import STANDARD, MapProfile
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


def build_land_brawl(profile: MapProfile, seed: int) -> tuple[Game, list[Player]] | None:
    """A game with both capitals on the largest continent, far apart. None if
    no map with a big-enough shared continent turns up."""
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
            rules=STANDARD, real_map=real_map, players=players,
            seed=seed, combat_resolver=CombatResolver(),
        )
        return game, players
    return None


def play_match(
    profile: MapProfile, seed: int, strategic_first: bool, cap: int
) -> tuple[str, int] | None:
    """Run one game; return (outcome, turns). Outcome is
    'strategic' | 'baseline' | 'draw' | 'unfinished'."""
    built = build_land_brawl(profile, seed)
    if built is None:
        return None
    game, players = built
    si = 0 if strategic_first else 1
    strategic: AIController = StrategicAI()
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
    seeds: int, cap: int, profile: MapProfile = ARENA_PROFILE, verbose: bool = True
) -> ArenaResult:
    """Each seed played twice (sides swapped) to cancel positional bias."""
    result = ArenaResult()
    turns: list[int] = []
    start = time.time()
    for seed in range(seeds):
        for strategic_first in (True, False):
            outcome = play_match(profile, seed, strategic_first, cap)
            if outcome is None:
                continue
            label, played = outcome
            setattr(result, label, getattr(result, label) + 1)
            if label in ("strategic", "baseline"):
                turns.append(played)
        if verbose:
            print(
                f"  seed {seed}: S={result.strategic} B={result.baseline} "
                f"draw={result.draw} unfin={result.unfinished} "
                f"({time.time() - start:.0f}s)",
                flush=True,
            )
    result.mean_turns = sum(turns) / len(turns) if turns else 0.0
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="StrategicAI vs BaselineAI land-brawl arena")
    parser.add_argument("--seeds", type=int, default=25, help="seeds (each played both ways)")
    parser.add_argument("--cap", type=int, default=250, help="per-game turn cap")
    args = parser.parse_args()

    r = run_arena(args.seeds, args.cap)
    print(f"\n{2 * args.seeds} games: S={r.strategic} B={r.baseline} "
          f"draw={r.draw} unfinished={r.unfinished}")
    if r.decided:
        print(f"StrategicAI win-rate among decided: {r.strategic}/{r.decided} "
              f"= {r.strategic / r.decided:.1%}")
        print(f"one-sided binomial p (better than baseline): {r.binomial_p():.4f}")
    print(f"mean decided-game length: {r.mean_turns:.0f} turns")


if __name__ == "__main__":
    main()

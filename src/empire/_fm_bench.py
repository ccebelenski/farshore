"""Forward-model benchmark: the Phase-15.8 search-budget instrument.

Measures, at early/mid/late states of a real land-brawl game, the two costs
that bound `SearchAI`'s per-turn budget:

1. **clone** — `PlayoutModel.clone` (schema-v1 round-trip + rewiring), and
2. **playout** — H simulated turns with `BaselineAI` driving both sides
   (the opponent-model cost dominates; see `planning/03-ai-design.md` §9.3).

Run:  `python -m empire._fm_bench [--fortified] [--seed N] [--horizon H]`

Like `empire._arena`, a private CLI helper — not part of the layered package.
"""

from __future__ import annotations

import argparse
import statistics
import time

from empire._arena import ARENA_PROFILE, build_land_brawl
from empire.ai.baseline import BaselineAI
from empire.ai.search import PlayoutModel
from empire.contracts.controller import AIController
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.ruleset import FORTIFIED_CITIES, STANDARD, RuleSet

_CHECKPOINTS: dict[int, str] = {10: "early", 60: "mid", 120: "late"}
_CLONE_REPS = 20
_PLAYOUT_REPS = 5


def _baseline_controllers(game: Game) -> dict[PlayerId, AIController]:
    return {p.id: BaselineAI() for p in game.players}


def bench_state(model: PlayoutModel, game: Game, horizon: int, label: str) -> float:
    """Print clone + playout cost at `game`'s current state; return total ms."""
    n_units = sum(1 for _ in game.map.all_units())

    clone_times: list[float] = []
    for _ in range(_CLONE_REPS):
        t0 = time.perf_counter()
        model.clone(game, _baseline_controllers(game))
        clone_times.append(time.perf_counter() - t0)
    clone_ms = statistics.median(clone_times) * 1000

    playout_times: list[float] = []
    for _ in range(_PLAYOUT_REPS):
        sim = model.clone(game, _baseline_controllers(game))
        t0 = time.perf_counter()
        for _ in range(horizon):
            sim.run_turn()
            if sim.is_over():
                break
        playout_times.append(time.perf_counter() - t0)
    playout_ms = statistics.median(playout_times) * 1000

    total_ms = clone_ms + playout_ms
    print(
        f"{label:>6}: units={n_units:3d}  clone={clone_ms:6.1f}ms  "
        f"{horizon}-turn playout={playout_ms:7.1f}ms  -> total={total_ms:7.1f}ms"
    )
    return total_ms


def run_bench(rules: RuleSet, seed: int, horizon: int) -> None:
    built = build_land_brawl(ARENA_PROFILE, seed, rules)
    if built is None:
        raise SystemExit(f"no land-brawl map for seed {seed}")
    game, players = built
    for p in players:
        game.attach_controller(p.id, BaselineAI())

    model = PlayoutModel()
    mid_ms: float | None = None
    for turn_target in sorted(_CHECKPOINTS):
        while game.turn < turn_target and not game.is_over():
            game.run_turn()
        if game.is_over():
            print(f"game ended at turn {game.turn}; stopping checkpoints")
            break
        label = _CHECKPOINTS[turn_target]
        total = bench_state(model, game, horizon, label)
        if label == "mid":
            mid_ms = total

    if mid_ms is not None:
        per_candidate_s = mid_ms / 1000
        print(
            f"\nmid-game: {1 / per_candidate_s:5.1f} candidate evaluations/sec"
            f" -> 32 candidates ≈ {32 * per_candidate_s:.1f}s/turn (single core)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="SearchAI forward-model benchmark")
    parser.add_argument("--seed", type=int, default=3, help="land-brawl map seed")
    parser.add_argument("--horizon", type=int, default=15, help="playout turns")
    parser.add_argument(
        "--fortified", action="store_true",
        help="use the FORTIFIED_CITIES ruleset instead of STANDARD",
    )
    args = parser.parse_args()
    rules = FORTIFIED_CITIES if args.fortified else STANDARD
    print(f"ruleset: {rules.name}  seed: {args.seed}  horizon: {args.horizon}")
    run_bench(rules, args.seed, args.horizon)


if __name__ == "__main__":
    main()

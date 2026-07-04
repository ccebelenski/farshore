"""Validation harness: N-game `BaselineAI` self-play plus a save/load check.

Not a real package — a private CLI helper. Lives outside the layered
packages so it can freely depend on `mapgen`, `combat`, `ai`, and
`persistence`.

Outputs:
  - per-game outcome row (seed, turn, winner, captures, etc.)
  - summary (terminated %, win-rate, mean turns)
  - save/load identity check (one randomly-chosen game)
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from empire.ai.baseline import BaselineAI
from empire.contracts.controller import AIController
from empire.core.game import Game
from empire.core.player import Player
from empire.core.ruleset import MapProfile
from empire.persistence.save_manager import SaveManager
from empire.persistence.schema_v1 import V1Serializer
from empire.setup import build_game


@dataclass(frozen=True, slots=True)
class GameOutcome:
    """One game's result."""

    seed: int
    turns_played: int
    terminated: bool       # True if `is_over()` fired before turn cap
    winner: str            # "P1", "P2", "draw", or "" if not terminated
    p1_cities: int
    p2_cities: int
    neutral_cities: int


def _build_self_play(profile: MapProfile, seed: int) -> tuple[Game, list[Player]]:
    """Build a Game with BaselineAI attached to both sides."""
    game, players = build_game(profile, seed, p1_is_ai=True, p2_is_ai=True)
    controller: AIController
    for p in players:
        controller = BaselineAI()
        game.attach_controller(p.id, controller)
    return game, players


def _run_one_game(profile: MapProfile, seed: int, turn_cap: int) -> GameOutcome:
    g, players = _build_self_play(profile, seed)
    real_map = g.map
    for _ in range(turn_cap):
        g.run_turn()
        if g.is_over():
            break

    p1_cities = sum(1 for c in real_map.cities() if c.owner is players[0])
    p2_cities = sum(1 for c in real_map.cities() if c.owner is players[1])
    neutral = sum(1 for c in real_map.cities() if c.owner is None)
    terminated = g.is_over()
    winner_player = g.winner() if terminated else None
    if not terminated:
        winner = ""
    elif winner_player is players[0]:
        winner = "P1"
    elif winner_player is players[1]:
        winner = "P2"
    else:
        winner = "draw"

    return GameOutcome(
        seed=seed,
        turns_played=g.turn,
        terminated=terminated,
        winner=winner,
        p1_cities=p1_cities,
        p2_cities=p2_cities,
        neutral_cities=neutral,
    )


def _save_load_identity(profile: MapProfile, seed: int, snapshot_turn: int) -> bool:
    """Build a game, run `snapshot_turn` turns, save, load, compare payloads.

    The check is intentionally payload-equality: V1Serializer is bijective by
    construction, so a save→load→re-save round-trip must produce byte-identical
    JSON. This is the same equality used in the persistence test suite, scaled
    up to a real BaselineAI mid-game state.
    """
    g, _ = _build_self_play(profile, seed)
    for _ in range(snapshot_turn):
        g.run_turn()
        if g.is_over():
            break

    serializer = V1Serializer()
    payload_before = serializer.to_dict(g)
    mgr = SaveManager()
    with tempfile.TemporaryDirectory() as td:
        save_path = Path(td) / "snapshot.json"
        mgr.save(g, save_path)
        reloaded = mgr.load(save_path)
    payload_after = serializer.to_dict(reloaded)
    return json.dumps(payload_before, sort_keys=True) == json.dumps(
        payload_after, sort_keys=True,
    )


def run_validation(
    profile: MapProfile,
    num_games: int,
    turn_cap: int,
    *,
    base_seed: int = 0,
    save_load_seed: int | None = None,
    save_load_turn: int = 25,
) -> str:
    """Run the Phase-10 gate: `num_games` self-play games + one save/load check.

    Returns a multi-line human-readable summary.
    """
    outcomes: list[GameOutcome] = []
    for i in range(num_games):
        outcomes.append(_run_one_game(profile, base_seed + i, turn_cap))

    terminated = sum(1 for o in outcomes if o.terminated)
    wins_p1 = sum(1 for o in outcomes if o.winner == "P1")
    wins_p2 = sum(1 for o in outcomes if o.winner == "P2")
    draws = sum(1 for o in outcomes if o.winner == "draw")
    unfinished = num_games - terminated
    mean_turns = sum(o.turns_played for o in outcomes) / num_games

    sl_seed = save_load_seed if save_load_seed is not None else base_seed
    sl_ok = _save_load_identity(profile, sl_seed, save_load_turn)

    lines: list[str] = []
    lines.append(
        f"# Validation: {num_games} games, profile={profile.width}x{profile.height} "
        f"cap={turn_cap} base_seed={base_seed}",
    )
    lines.append("")
    lines.append("seed  turns  term  winner  P1  P2  neutral")
    lines.append("----  -----  ----  ------  --  --  -------")
    for o in outcomes:
        lines.append(
            f"{o.seed:>4d}  {o.turns_played:>5d}  "
            f"{'Y' if o.terminated else 'N'}     "
            f"{o.winner:>6s}  "
            f"{o.p1_cities:>2d}  {o.p2_cities:>2d}  {o.neutral_cities:>7d}",
        )
    lines.append("")
    lines.append("# Summary")
    lines.append(f"#   terminated: {terminated}/{num_games}  (unfinished: {unfinished})")
    lines.append(f"#   P1 wins: {wins_p1}   P2 wins: {wins_p2}   draws: {draws}")
    lines.append(f"#   mean turns: {mean_turns:.1f}")
    lines.append(f"#   save/load identity (seed={sl_seed}, turn={save_load_turn}): "
                 f"{'PASS' if sl_ok else 'FAIL'}")
    return "\n".join(lines)

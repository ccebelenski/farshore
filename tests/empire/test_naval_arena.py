"""Smoke tests for the multi-continent (naval) arena harness (Phase 15.9).

Not a balance run — just that the gate is wired correctly: it builds
two-continent games, the projection telemetry computes, and a land-only
challenger scores zero projection (it can't cross water — the baseline the
naval work must move).
"""

from __future__ import annotations

from empire._naval_arena import NAVAL_PROFILE, build_two_continent, play_match
from empire.core.game import Game
from empire.core.player import Player
from empire.setup import land_continents


def _home(game: Game, player: Player) -> set[tuple[int, int]]:
    cap = next(c for c in game.map.cities() if c.owner is player)
    return next(
        comp for comp in land_continents(game.map)
        if (cap.coord.x, cap.coord.y) in comp
    )


def test_builds_two_separate_capital_continents() -> None:
    built = build_two_continent(NAVAL_PROFILE, seed=0)
    assert built is not None
    game, players = built
    homes = [_home(game, p) for p in players]
    # Two capitals, on DISTINCT landmasses (the whole point of this arena).
    assert homes[0] != homes[1]
    assert homes[0].isdisjoint(homes[1])
    # Each capital's continent satisfies the >=3-city eligibility rule.
    for home in homes:
        n = sum(1 for c in game.map.cities() if (c.coord.x, c.coord.y) in home)
        assert n >= 3


def test_land_only_challenger_cannot_project() -> None:
    """A short game: BaselineAI (land-only) confined to its island ends
    unfinished with zero off-home captures — the gate's starting point."""
    out = play_match(
        NAVAL_PROFILE, seed=1, challenger_first=True, cap=40, ai="strategic"
    )
    assert out is not None
    outcome, turns, peak_off_home, end_off_home, peak_ships, peak_fighters = out
    # A land-only AI can neither reach nor hold ground across water, so both
    # the ever-projected (peak) and held-at-cap (end) counts must be zero.
    assert peak_off_home == 0, "a land-only AI must not capture across water"
    assert end_off_home == 0
    # 40 turns is far too short to conquer two continents; expect unfinished.
    assert outcome in ("unfinished", "strategic", "baseline", "draw")
    del turns, peak_ships, peak_fighters

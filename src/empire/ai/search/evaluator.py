"""`Evaluator`: static position score at a playout horizon.

The search keeps its lookahead short — beyond ~10-20 turns the simulation
diverges from reality (fog, stochastic combat, opponent-model error) — so
the long-term judgment lives here: what is this position *worth*?

Terms, in dominance order (see `planning/03-ai-design.md` §9.1):
- **Decided game**: an outright win/loss at the horizon dwarfs everything.
- **Cities**: the production base; the win condition is "all of them".
- **Production in flight**: a city 9/10 of the way to an army is worth most
  of an army — without this term the search can't see value building up.
- **Material**: armies on the board.

Deliberately absent in v1 (add only if the arena demands them): a Lanchester
concentration term (massed > scattered at equal count) and a frontier/intel
term. Weights are a frozen value type so variants are explicit objects, and
the arena — not intuition — is what tunes them.
"""

from __future__ import annotations

from dataclasses import dataclass

from empire.core.city import City
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.unit import UNIT_REGISTRY


@dataclass(frozen=True, slots=True)
class EvalWeights:
    """Per-term weights. The defaults make one city ≈ ten armies — losing a
    city to win a skirmish should never look like progress."""

    win: float = 100_000.0
    city: float = 100.0
    army: float = 10.0
    production: float = 8.0  # one *completed* build ≈ most of an army


class Evaluator:
    """Scores a game state from one player's perspective (zero-sum-ish:
    opponent assets count against us symmetrically)."""

    def __init__(self, weights: EvalWeights | None = None) -> None:
        self._w: EvalWeights = weights if weights is not None else EvalWeights()

    def evaluate(self, game: Game, player_id: PlayerId) -> float:
        """Higher is better for `player_id`. Deterministic in the game state."""
        w = self._w
        if game.is_over():
            victor = game.winner()
            if victor is None:
                return 0.0  # mutual ruin — neither outcome to chase nor fear
            return w.win if victor.id == player_id else -w.win

        score = 0.0
        for city in game.map.cities():
            if city.owner is None:
                continue  # neutral cities are *opportunity*, not assets
            sign = 1.0 if city.owner.id == player_id else -1.0
            score += sign * w.city
            score += sign * w.production * self._production_progress(city)

        for unit in game.map.all_units():
            sign = 1.0 if unit.owner.id == player_id else -1.0
            score += sign * w.army

        return score

    @staticmethod
    def _production_progress(city: City) -> float:
        """Fraction of the current build already paid for, in [0, 1)."""
        building = city.production.building
        if building is None:
            return 0.0
        build_time = UNIT_REGISTRY[building].build_time
        if build_time <= 0:
            return 0.0
        # Production-change penalties can drive `work` negative (spec §5.2).
        return max(0.0, min(city.production.work / build_time, 1.0))

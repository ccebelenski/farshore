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

- **Intel / frontier** (Phase 15.9): an unexplored *reachable* frontier is a
  standing penalty, so a position that has pushed back the fog scores higher.
  Without it a scouting plan and a sit-still plan are indistinguishable to the
  search (same cities, same material), so exploration never wins a playout —
  which is why naval projection didn't start until the land game was fully
  exhausted (the enemy continent stayed undiscovered). The term is *one-sided*
  (only our own fog; it is not zero-sum) and self-flattening (it vanishes once
  the reachable map is known), and it is bounded by a perimeter so it stays
  small by construction. See `_frontier_penalty`.

- **Holdability / local force balance** (Phase 15.9+ §10): a city's worth is
  conditioned on the armies in its neighborhood — an own city outnumbered
  locally is discounted (recapture risk), and own force massed at an enemy/
  neutral city is credited (a campaign-in-progress is worth something before
  the city flips). This is what makes "invade and hold" a value the search can
  *sustain*: the lean middle of a campaign reads as progress, not waste. See
  `_contested_balance`.

Deliberately absent in v1 (add only if the arena demands them): a Lanchester
concentration term (massed > scattered at equal count). Weights are a frozen
value type so variants are explicit objects, and the arena — not intuition —
is what tunes them.
"""

from __future__ import annotations

from dataclasses import dataclass

from empire.core.city import City
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.unit import UNIT_REGISTRY, UnitKind

# Chebyshev radius and per-city cap for the local-force-balance (holdability)
# term. A city's fate turns on the armies in its immediate neighborhood, not
# the global count; the cap keeps one lopsided cluster from dominating.
_CONTEST_RADIUS = 3
_CONTEST_CAP = 3


@dataclass(frozen=True, slots=True)
class EvalWeights:
    """Per-term weights. The defaults make one city ≈ ten armies — losing a
    city to win a skirmish should never look like progress."""

    win: float = 100_000.0
    city: float = 100.0
    army: float = 10.0
    production: float = 8.0  # one *completed* build ≈ most of an army
    # Penalty per unexplored tile bordering our seen region (the reachable
    # frontier). Small — a typical mid-game frontier is tens of tiles, so at
    # 0.15 the whole term is worth a couple of armies at most and never rivals
    # a city. The one term most likely to need arena tuning.
    intel: float = 0.15
    # Holdability / local force balance (Phase 15.9+ §10): conditions a city's
    # worth on whether the local situation supports keeping (own) or taking
    # (enemy/neutral) it. `recapture_risk` discounts an own city per net enemy
    # army in its neighborhood — so a beachhead taken while outnumbered reads as
    # the precarious thing it is, and bringing force to even the balance is
    # visible progress. `pressure` credits own armies massed at an enemy/neutral
    # city — a campaign-in-progress (force projected to contact) scores as value
    # before the city flips, which is what lets the search *sustain* an invasion
    # through its lean middle instead of abandoning it. Kept below `city` so
    # neither ever inverts the basic territory calculus.
    recapture_risk: float = 8.0
    pressure: float = 4.0


class Evaluator:
    """Scores a game state from one player's perspective. Material/territory
    terms are zero-sum (opponent assets count against us symmetrically); the
    intel/frontier term is the one exception — it scores only our own fog."""

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

        score -= w.intel * self._frontier_penalty(game, player_id)
        score += self._contested_balance(game, player_id, w)
        return score

    @staticmethod
    def _contested_balance(game: Game, player_id: PlayerId, w: EvalWeights) -> float:
        """Local force balance around contested cities (holdability, §10).

        For each own city locally outnumbered by enemy armies, a recapture-risk
        discount; for each enemy/neutral city we have massed armies beside, a
        pressure credit. Only land armies contest cities. Distances are
        Chebyshev within `_CONTEST_RADIUS`; counts are capped so one big stack
        can't run the score away."""
        own_armies: list = []
        enemy_armies: list = []
        for u in game.map.all_units():
            if u.kind is not UnitKind.ARMY:
                continue
            (own_armies if u.owner.id == player_id else enemy_armies).append(u.coord)
        if not own_armies and not enemy_armies:
            return 0.0

        def near(coords: list, c) -> int:
            return sum(1 for x in coords if x.chebyshev_to(c) <= _CONTEST_RADIUS)

        adj = 0.0
        for city in game.map.cities():
            own_n = near(own_armies, city.coord)
            enemy_n = near(enemy_armies, city.coord)
            if city.owner is not None and city.owner.id == player_id:
                deficit = enemy_n - own_n
                if deficit > 0:
                    adj -= w.recapture_risk * min(deficit, _CONTEST_CAP)
            else:
                surplus = own_n - enemy_n
                if surplus > 0:
                    adj += w.pressure * min(surplus, _CONTEST_CAP)
        return adj

    @staticmethod
    def _frontier_penalty(game: Game, player_id: PlayerId) -> float:
        """Count of unexplored tiles bordering this player's seen region —
        the *reachable* frontier (fog we are poised to uncover, as opposed to
        deep interior fog across an ocean we can't yet reach, which never
        touches a seen cell). Pushing a scout out converts frontier tiles to
        seen, shrinking this; it reaches zero once the reachable map is fully
        explored, so the term self-flattens and never punishes a player who has
        nowhere left to look."""
        player = game.player_by_id(player_id)
        if player is None:
            return 0.0
        view = player.view
        seen = view.visible | view.remembered.keys()
        frontier: set = set()
        for c in seen:
            for n in c.neighbors():
                if n not in frontier and game.map.in_bounds(n) and not view.seen(n):
                    frontier.add(n)
        return float(len(frontier))

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

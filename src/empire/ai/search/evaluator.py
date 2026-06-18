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

- **Exploration / information** (Phase 15.9+ §10.2): seen area is rewarded —
  land worth more than sea (cities live on land; open ocean is the low-prior
  side of the frontier). This *replaces* the original perimeter-penalty `intel`
  term, which we measured to be counterproductive: a perimeter count *rises* as
  you push the fog back (a growing seen region has a growing boundary), so it
  penalised the very scouting it was meant to reward, and a sea-ringed continent
  never reaches the self-flattening zero. Rewarding seen-area instead is monotone
  in exploration and one-sided (only our own fog; not zero-sum). The legacy
  `intel` weight defaults to 0; `_frontier_penalty` is kept for comparison only.

- **Opportunity** (Phase 15.9+ §10.2): a known, unowned, *reachable* city carries
  a distance-discounted fraction of city value — so discovering an overseas
  landmass is a value *spike* (not the penalty the old surface produced) and
  bringing force toward it is a value *ramp* the short-horizon search can climb
  to the capture payoff. Distance is projection-aware: a same-landmass target is
  discounted by land-march distance; an overseas target pays a ferry surcharge
  plus the crossing. Always kept below `city` so capturing a prize strictly beats
  loitering beside it. See `_opportunity_and_exploration`.

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

from collections import deque
from dataclasses import dataclass

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.tile import TerrainKind
from empire.core.unit import UNIT_REGISTRY, UnitKind
from empire.pathfinding.cost import AIR, ARMY
from empire.pathfinding.distance_field import PassabilityGrid

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
    # Legacy perimeter-penalty (Phase 15.9). Measured counterproductive — it
    # penalised scouting and never self-flattened on a sea-ringed continent — so
    # it is disabled (0.0) and superseded by the exploration term below. Kept as
    # a weight so the old behavior can still be A/B'd in the curve simulator.
    intel: float = 0.0
    # Exploration / information (§10.2): reward per seen cell, land worth more
    # than sea. Small — a ~100-cell home continent at 0.5/land ≈ 50, well under a
    # city; the point is a monotone scouting gradient, not territory rivalry.
    explore_land: float = 0.5
    explore_sea: float = 0.05
    # Opportunity (§10.2): a known unowned reachable city is worth up to
    # `opportunity` when our force is adjacent, decaying with projection
    # distance. Below `city` so capturing always beats loitering. `opp_scale`
    # sets the reach of the ramp (turns of distance per e-fold of `opp_decay`);
    # `ferry_penalty` is the extra projection cost charged to an overseas
    # (water-crossing) target on top of the crossing distance.
    opportunity: float = 50.0
    opp_decay: float = 0.85
    opp_scale: float = 5.0
    ferry_penalty: float = 6.0
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

        if w.intel:
            score -= w.intel * self._frontier_penalty(game, player_id)
        score += self._contested_balance(game, player_id, w)
        score += self._opportunity_and_exploration(game, player_id, w)
        return score

    @staticmethod
    def _multi_source_bfs(origins: list[Coord], grid: PassabilityGrid) -> list[int]:
        """Min step count from the nearest of `origins` to every cell (-1 if
        unreachable), one 8-connected BFS seeded with all sources at once. The
        sources themselves are distance 0 regardless of their own passability
        (a unit standing on a city/coast still counts as 'here')."""
        width, height = grid.width, grid.height
        dist = [-1] * (width * height)
        flags = grid.flags
        queue: deque[int] = deque()
        for o in origins:
            if 0 <= o.x < width and 0 <= o.y < height:
                i = o.y * width + o.x
                if dist[i] != 0:
                    dist[i] = 0
                    queue.append(i)
        while queue:
            i = queue.popleft()
            nd = dist[i] + 1
            x, y = i % width, i // width
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        j = ny * width + nx
                        if dist[j] < 0 and flags[j]:
                            dist[j] = nd
                            queue.append(j)
        return dist

    def _opportunity_and_exploration(
        self, game: Game, player_id: PlayerId, w: EvalWeights
    ) -> float:
        """Exploration (seen-area, land>sea) + opportunity (distance-discounted
        value of known unowned reachable cities). See class docstring §10.2."""
        player = game.player_by_id(player_id)
        if player is None:
            return 0.0
        view = player.view
        real_map = game.map
        total = 0.0

        if w.explore_land or w.explore_sea:
            land = sea = 0
            for c in view.visible | view.remembered.keys():
                if real_map.terrain_at(c) is TerrainKind.WATER:
                    sea += 1
                else:
                    land += 1
            total += w.explore_land * land + w.explore_sea * sea

        if not w.opportunity:
            return total

        # Anchors: every own force we could project from (cities + land armies).
        anchors = [
            c.coord for c in real_map.cities()
            if c.owner is not None and c.owner.id == player_id
        ]
        anchors += [
            u.coord for u in real_map.all_units()
            if u.owner.id == player_id and u.kind is UnitKind.ARMY
        ]
        if not anchors:
            return total

        # Known unowned cities only — can't value what we haven't found.
        targets = [
            city for city in real_map.cities()
            if not (city.owner is not None and city.owner.id == player_id)
            and view.seen(city.coord)
        ]
        if not targets:
            return total

        width = real_map.width
        land_d = self._multi_source_bfs(anchors, PassabilityGrid(real_map, ARMY))
        any_d = self._multi_source_bfs(anchors, PassabilityGrid(real_map, AIR))
        for city in targets:
            idx = city.coord.y * width + city.coord.x
            march = land_d[idx]
            if march >= 0:
                cost = float(march)  # same landmass — just walk there
            else:
                crossing = any_d[idx]
                if crossing < 0:  # AIR-unreachable (enclosed) — straight-line
                    crossing = min(city.coord.chebyshev_to(a) for a in anchors)
                cost = float(crossing) + w.ferry_penalty  # overseas — ferry it
            total += w.opportunity * (w.opp_decay ** (cost / w.opp_scale))
        return total

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

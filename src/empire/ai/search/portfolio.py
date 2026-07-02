"""`PortfolioAI`: the stateful multi-focus successor to `SearchAI`
(planning/07-portfolio-director.md).

`SearchAI` picks ONE plan per turn — so naval can only happen by abandoning the
land game, and "defend home AND invade overseas AND scout" can't be expressed.
`PortfolioAI` instead maintains a PERSISTENT set of single-purpose objectives
(the portfolio) and, each turn, hill-climbs it: drop objectives that no longer
earn their keep, add the one that most improves the whole-portfolio playout —
then hands the combined multi-objective plan to the same `PlanFollower`, which
allocates units across the objectives. Contention is priced for free by the
shared playout (a unit given to one focus is unavailable to another in the same
sim). Stickiness (add/drop margins) keeps the strategy from thrashing as scores
wobble — deliberate change, not churn.

It reuses `SearchAI`'s belief, playout, evaluator, scoring, base value, and
follower wholesale; only the SELECTION layer changes (one plan -> a maintained
portfolio). Determinism is preserved (same staged position -> same portfolio),
so the set-piece tests apply unchanged.
"""

from __future__ import annotations

from empire.ai.search.ai import SearchAI
from empire.ai.search.belief import BeliefBuilder
from empire.ai.search.follower import PlanFollower
from empire.ai.search.generator import fleet_production
from empire.ai.search.plan import Objective, Plan, PlanGoal, Role, SurplusPolicy
from empire.ai.vision import sea_frontier_cells
from empire.contracts.turn_plan import TurnPlan
from empire.contracts.world_view import WorldView
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.unit import UnitKind

# A focus must improve the whole-portfolio score by this much to be ADDED, and a
# member must cost this much to justify DROPPING it — the hysteresis band that
# makes change deliberate (planning/07 "deliberate abandonment"). In evaluator
# units (city=100, army=10).
ADD_MARGIN = 8.0
DROP_MARGIN = 8.0
# Scouts to field for sea discovery before reverting production to armies. A
# couple of patrols recon the ocean concurrently with the land game; building
# them indefinitely would starve army production and abandon home (a real
# failure observed in v1's discovery mode).
SCOUT_QUOTA = 2
# Don't scout the sea until at least this many cities are held — establishing a
# base first protects the early army buildup (scouting from the lone capital
# regressed the land game on shared continents).
MIN_CITIES_TO_SCOUT = 3
# Cap concurrent foci so the per-turn hill-climb stays cheap and the follower
# isn't fragmenting force across too many objectives at once.
MAX_FOCI = 4


def _key(obj: Objective) -> tuple[int, int, str]:
    return (obj.target.x, obj.target.y, obj.role.value)


class PortfolioAI(SearchAI):
    """Maintains a persistent portfolio of objectives; satisfies `AIController`."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._belief_builder = BeliefBuilder()
        # The persisted portfolio: objectives carried across turns, each tagged
        # with the goal that earns its horizon-free base value.
        self._portfolio: tuple[Objective, ...] = ()
        self._goal_of: dict[tuple[int, int, str], PlanGoal] = {}
        # Discovery-driven naval doctrine, computed per turn (see _naval_warrants).
        # _discovery: scout the sea now (cheap, CONCURRENT with the land game —
        # the portfolio's whole point). _invade_ok: credit invasions, set only
        # once an enemy city is actually found OVERSEAS (so island sideshows on a
        # shared continent are never funded). Neither depends on home being fully
        # explored — that gate stalled SearchAI into a turtle and must not bind
        # the portfolio.
        self._discovery: bool = False
        self._invade_ok: bool = False

    def name(self) -> str:
        return "Portfolio"

    # ---- AIController ---------------------------------------------------------

    def plan_turn(self, view: WorldView) -> TurnPlan:
        self._portfolio = self._maintain(view)
        plan = self._to_plan(self._portfolio, view)
        self._committed = PlanFollower(plan)
        return self._committed.plan_turn(view)

    # ---- portfolio maintenance -----------------------------------------------

    def _candidate_objectives(
        self, view: WorldView
    ) -> dict[tuple[int, int, str], tuple[Objective, PlanGoal]]:
        """The objective pool to draw foci from: every distinct objective the
        generator proposes this turn, tagged with its plan's goal (so an INVADE
        objective from a naval-warranted plan keeps its INVADE credit). Deduped by
        (target, role); an INVADE tag wins over NONE for the same cell."""
        pool: dict[tuple[int, int, str], tuple[Objective, PlanGoal]] = {}
        plans = self._generator.generate(view)
        for plan in plans:
            for obj in plan.objectives:
                k = _key(obj)
                prev = pool.get(k)
                if prev is None or (prev[1] is PlanGoal.NONE and plan.goal is not PlanGoal.NONE):
                    pool[k] = (obj, plan.goal)
        return pool

    def _naval_warrants(self, view: WorldView) -> None:
        """Set the discovery-driven naval flags for this turn (planning/07).

        Uses the generator's land/sea assessment (one flood) to classify the
        enemy: reachable by land, found overseas, or not yet found.
          - `_invade_ok`: an enemy city is known OVERSEAS — invasions toward the
            discovered enemy are now worth their cost. False on a shared continent
            (enemy is land-reachable, not overseas) -> no island sideshows, the
            land-brawl guard, WITHOUT needing home fully explored.
          - `_discovery`: there's unexplored sea, no enemy is land-reachable, and
            we haven't found the enemy overseas yet -> scout to find them, NOW and
            concurrently with the land game (not gated behind home exploration,
            which never completes and turtled SearchAI)."""
        land_targets, overseas, _ = self._generator._assess(view)
        land = {(c.x, c.y) for c in land_targets}
        sea = {(c.x, c.y) for c in overseas}
        enemy = [(c.coord.x, c.coord.y) for c in view.known_enemy_cities]
        enemy_land = any(e in land for e in enemy)
        enemy_overseas = any(e in sea for e in enemy)
        self._invade_ok = enemy_overseas
        # Don't scout from the lone capital: building patrols before a base exists
        # sacrifices the critical early army buildup and loses winnable land games
        # (the real land-brawl regression). Establish a base first, then scout.
        established = len(view.own_cities) >= MIN_CITIES_TO_SCOUT
        self._discovery = (
            established
            and bool(sea_frontier_cells(view))
            and not enemy_land
            and not enemy_overseas
        )

    def _valid(self, obj: Objective, view: WorldView) -> bool:
        """Relevance check (planning/07): does the goal still matter? Assault/
        invade targets must still be a known enemy/neutral city we don't own;
        defend targets must still be our city."""
        enemy = {(c.coord.x, c.coord.y) for c in view.known_enemy_cities}
        neutral = {(c.coord.x, c.coord.y) for c in view.neutral_cities}
        own = {(c.coord.x, c.coord.y) for c in view.own_cities}
        t = (obj.target.x, obj.target.y)
        if obj.role is Role.DEFEND:
            return t in own
        return t in enemy or t in neutral

    def _maintain(self, view: WorldView) -> tuple[Objective, ...]:
        """Validate the persisted portfolio, then greedily drop-then-add against
        the whole-portfolio playout score, with hysteresis margins."""
        pool = self._candidate_objectives(view)
        self._goal_of = {k: g for k, (_, g) in pool.items()}
        self._naval_warrants(view)
        belief = self._belief_builder.build(view)
        me = view.own_player.id

        # Start from what survived: persisted objectives still relevant AND still
        # on offer (a vanished candidate dissolves the focus).
        current: list[Objective] = [
            obj for obj in self._portfolio
            if self._valid(obj, view) and _key(obj) in pool
        ]

        def score(objs: list[Objective]) -> float:
            return self._score_portfolio(belief, objs, view, me)

        cur_score = score(current)

        # DROP: remove any focus whose absence improves the portfolio (reallocate-
        # then-rescore — the follower reassigns its units when it's gone).
        changed = True
        while changed and current:
            changed = False
            for obj in list(current):
                trial = [o for o in current if o is not obj]
                if score(trial) >= cur_score + DROP_MARGIN:
                    current, cur_score = trial, score(trial)
                    changed = True
                    break

        # ADD: bring in the single best new focus that clears the margin; repeat
        # up to MAX_FOCI. Greedy, deterministic (pool iteration order is stable).
        present = {_key(o) for o in current}
        while len(current) < MAX_FOCI:
            best_obj: Objective | None = None
            best_score = cur_score + ADD_MARGIN
            for k, (obj, _goal) in pool.items():
                if k in present:
                    continue
                trial = [*current, obj]
                s = score(trial)
                if s > best_score:
                    best_obj, best_score = obj, s
            if best_obj is None:
                break
            current.append(best_obj)
            present.add(_key(best_obj))
            cur_score = best_score

        return tuple(current)

    def _score_portfolio(
        self, belief: Game, objs: list[Objective], view: WorldView, me: PlayerId
    ) -> float:
        """Whole-portfolio score: the 12-turn playout of the combined plan PLUS
        the horizon-free base value of each focus's goal (the split-score, summed
        across foci)."""
        plan = self._to_plan(tuple(objs), view)
        raw = self._score(belief, plan, me)
        bonus = 0.0
        # Invasion credit when an enemy has been found overseas (discovery-driven,
        # not the generator's home-explored gate) — so the fleet gets committed
        # once we know where the enemy is.
        if self._invade_ok and self._invade_base > 0.0:
            bonus += self._invade_base * sum(1 for o in objs if o.role is Role.INVADE)
        # Discovery base value, when the rendered plan is in sea-scout mode.
        if plan.goal is PlanGoal.SCOUT_SEA and self._explore_base > 0.0:
            bonus += self._explore_base
        return raw + bonus

    def _to_plan(self, objs: tuple[Objective, ...], view: WorldView) -> Plan:
        """Render the portfolio as one multi-objective Plan for the follower.

        Production serves the portfolio: if any focus is an overseas invasion,
        build transports (coastal) + armies (inland) until the fleet exists; else
        build armies. Surplus scouts so spare units explore. The goal tag carries
        the dominant naval credit for any single-plan consumers."""
        has_invade = any(o.role is Role.INVADE for o in objs)
        goal = PlanGoal.NONE
        production = UnitKind.ARMY
        if has_invade:
            goal = PlanGoal.INVADE
            production = fleet_production(view)
        elif self._discovery:
            # No target to invade yet but the enemy is plausibly overseas: scout.
            # Build patrols only up to the recon quota, then revert to armies so
            # the land game continues — the scouts keep ranging meanwhile. This is
            # the production-level concurrency (recon AND home), not all-in scouting.
            goal = PlanGoal.SCOUT_SEA
            n_patrols = sum(1 for u in view.own_units if u.kind is UnitKind.PATROL)
            production = UnitKind.PATROL if n_patrols < SCOUT_QUOTA else UnitKind.ARMY
        return Plan(
            objectives=objs,
            surplus=SurplusPolicy.SCOUT,
            production=production,
            goal=goal,
        )

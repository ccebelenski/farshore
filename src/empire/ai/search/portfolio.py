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
from empire.ai.search.generator import FLEET_TRANSPORTS
from empire.ai.search.plan import Objective, Plan, PlanGoal, Role, SurplusPolicy
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
        # Whether the generator deems sea-scouting warranted this turn (the
        # SCOUT_SEA discovery focus lives in production, not an objective, so the
        # portfolio carries it as a flag rather than a member).
        self._discovery: bool = False

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
        # The discovery focus is production-borne (build a patrol to find the
        # enemy), not an objective — capture it as a flag so `_to_plan` can scout.
        self._discovery = any(p.goal is PlanGoal.SCOUT_SEA for p in plans)
        for plan in plans:
            for obj in plan.objectives:
                k = _key(obj)
                prev = pool.get(k)
                if prev is None or (prev[1] is PlanGoal.NONE and plan.goal is not PlanGoal.NONE):
                    pool[k] = (obj, plan.goal)
        return pool

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
        for obj in objs:
            goal = self._goal_of.get(_key(obj), PlanGoal.NONE)
            if goal is PlanGoal.INVADE and self._invade_base > 0.0:
                bonus += self._invade_base
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
            n_transports = sum(
                1 for u in view.own_units if u.kind is UnitKind.TRANSPORT
            )
            production = (
                UnitKind.TRANSPORT if n_transports < FLEET_TRANSPORTS else UnitKind.ARMY
            )
        elif self._discovery:
            # No target to invade yet but the enemy is plausibly overseas: build a
            # patrol and scout the sea to find it, concurrent with any land foci.
            goal = PlanGoal.SCOUT_SEA
            production = UnitKind.PATROL
        return Plan(
            objectives=objs,
            surplus=SurplusPolicy.SCOUT,
            production=production,
            goal=goal,
        )

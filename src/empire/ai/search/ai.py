"""`SearchAI`: plan-space lookahead (design §9 of 03-ai-design).

Each turn, rolling horizon:

1. `CandidateGenerator` proposes K plausible `Plan`s from the fog view.
2. `BeliefBuilder` materializes the searcher's knowledge as a playable game.
3. Each candidate is played out on a clone of that belief — our side driven
   by `PlanFollower(candidate)`, the enemy by the literal `BaselineAI`
   opponent model — for `horizon` turns, then scored by `Evaluator`.
4. The argmax plan's follower produces this turn's real `TurnPlan`.

The horizon is short by design: beyond ~10-20 turns the simulation diverges
from reality (fog, stochastic combat, opponent-model error), so the
evaluator carries the long-term judgment.

Stochasticity is handled two ways at once: each candidate is scored as the
*mean of several samples* (combat and capture rolls are coin flips — one
sample turns a 50% capture into all-or-nothing), and sample *i* of every
candidate shares the same RNG seed (common random numbers), so between-plan
differences come from the plans, not the dice. Ties resolve to the
earliest-generated candidate.
"""

from __future__ import annotations

from empire.ai.baseline import BaselineAI
from empire.ai.search.belief import BeliefBuilder
from empire.ai.search.evaluator import Evaluator
from empire.ai.search.follower import PlanFollower
from empire.ai.search.generator import CandidateGenerator
from empire.ai.search.plan import Plan, PlanGoal, Role
from empire.ai.search.playout import PlayoutModel
from empire.contracts.controller import AIController
from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.game import Game
from empire.core.identity import PlayerId, UnitId
from empire.core.unit import UNIT_REGISTRY, UnitKind

DEFAULT_HORIZON = 12
DEFAULT_SAMPLES = 3
# Decorrelates sample streams while keeping them deterministic.
_SAMPLE_STRIDE = 104_729
# A challenger must beat the incumbent plan's score by this much to displace
# it (≈ one army's worth). Without it, sampling noise flips near-tied plans
# every turn — without it, assault strength oscillates
# 3↔5 each turn, reshuffling fist membership so no storm ever cohered.
SWITCH_MARGIN = 10.0
# Investment-scaled commitment stickiness (§10): a campaign part-way through a
# slow strategic build resists abandonment so the search sees the 30-turn hull
# through instead of oscillating off it. COMMIT_SLOW_BUILD is the build_time at
# which production counts as a "campaign" (patrol 15 and up; armies build 5 and
# stay freely switchable). COMMIT_BASE is the front-loaded stickiness the moment
# any progress exists; COMMIT_SCALE ramps it with build fraction. A bar, not a
# lock — an urgent higher-value plan still overrides by paying the cost.
COMMIT_SLOW_BUILD = 15
COMMIT_BASE = 25.0
COMMIT_SCALE = 30.0
# Aggression temperament (planning/06-aggression-bias.md). A single scalar that
# leans plan selection toward BOLD plans — those whose payoff tends to land past
# the search horizon (assault/invade objectives; building projection units) — so
# the search commits to expansion and naval projection it otherwise can't see
# the payoff of. The lean REVERTS to honest evaluation when a caution signal
# fires: a bold plan scoring more than CAUTION_TOL below the best stand-pat
# (non-bold) option is losing ground in-horizon (known-bad), so the bias is
# withdrawn and the smarter plan wins. Caution is read off the playout itself, so
# every in-horizon danger is caught by construction. aggression=0.0 recovers the
# pre-bias behavior exactly (used as the arena A/B baseline). Defaults are a
# starting guess in evaluator units (city=100, army=10); the self-play arena
# calibrates them.
#
# DISABLED BY DEFAULT (0.0): the uniform-scalar form was tested and FAILED — a
# flat bonus to every bold plan only ever flips passive->bold, never bold->bold,
# so it couldn't redirect land-consolidation toward naval projection (no naval
# help) yet overrode correct passive/defensive choices on land (lost 17-3 to its
# plain self). See planning/06-aggression-bias.md "Result" and memory
# project_aggression_bias_failed. The mechanism is kept (param-exposed) because a
# DIRECTIONAL successor may reuse the bold/reversion machinery, but it ships off.
DEFAULT_AGGRESSION = 0.0
DEFAULT_CAUTION_TOL = 20.0
# Split-score base value (planning/07-portfolio-director.md). The 12-turn playout
# is demoted to a PRIORITY signal; a plan's worth also includes the horizon-FREE
# intrinsic value of achieving its goal. Applied ONLY to past-horizon goals the
# playout structurally cannot see — INVADE objectives, whose overseas city pays
# off 30+ turns out (transport build + ferry). In-horizon goals (home assault,
# defense) are left to the playout, which already values them correctly, so this
# can't inflate land plans / regress the land game (no INVADE objective there =
# no base value). This is what lets a naval plan get STARTED despite zero
# in-horizon payoff — the wall the concurrency gate proved is at selection.
# Roughly a city's worth (evaluator city=100) tempered for distance/uncertainty;
# 0.0 disables. Tuned against the amphibious probe.
INVADE_BASE_VALUE = 60.0
# Exploration base value: the horizon-free worth of SCOUTING THE SEA to discover
# the enemy continent — the goal one level up from INVADE (without a discovered
# target, no invade plan exists, so naval never starts on a real two-continent
# map). Sea recon's payoff (finding a coastal city, then invading it) is also far
# past the horizon, so the playout won't pick it on its own. Credited only when
# it is genuinely the way forward — gated in `_choose_plan` to fire ONLY when no
# land target remains AND home is fully explored — so it cannot pull the AI off
# the land game or off scouting its own continent first (the regressions in
# project_naval_regressed_land). Kept below INVADE_BASE_VALUE so a discovered
# target supersedes more scouting. 0.0 disables.
EXPLORE_BASE_VALUE = 30.0


class SearchAI:
    """Greedy plan search over playouts; satisfies `AIController`."""

    def __init__(
        self,
        horizon: int = DEFAULT_HORIZON,
        samples: int = DEFAULT_SAMPLES,
        generator: CandidateGenerator | None = None,
        evaluator: Evaluator | None = None,
        aggression: float = DEFAULT_AGGRESSION,
        caution_tol: float = DEFAULT_CAUTION_TOL,
        invade_base: float = INVADE_BASE_VALUE,
        explore_base: float = EXPLORE_BASE_VALUE,
    ) -> None:
        self._horizon: int = horizon
        self._samples: int = max(1, samples)
        self._aggression: float = aggression
        self._caution_tol: float = caution_tol
        self._invade_base: float = invade_base
        self._explore_base: float = explore_base
        self._generator: CandidateGenerator = (
            generator if generator is not None else CandidateGenerator()
        )
        self._evaluator: Evaluator = (
            evaluator if evaluator is not None else Evaluator()
        )
        self._belief_builder: BeliefBuilder = BeliefBuilder()
        self._playout: PlayoutModel = PlayoutModel()
        # The follower executing the currently committed plan; consulted by
        # revise_move between plan_turn calls.
        self._committed: PlanFollower = PlanFollower(Plan.hold())

    def name(self) -> str:
        return "Search"

    # ---- AIController --------------------------------------------------------

    def plan_turn(self, view: WorldView) -> TurnPlan:
        best = self._choose_plan(view)
        self._committed = PlanFollower(best)
        return self._committed.plan_turn(view)

    def revise_move(
        self, unit_id: UnitId, surprise: Surprise, view: WorldView
    ) -> UnitMove:
        return self._committed.revise_move(unit_id, surprise, view)

    # ---- search core ----------------------------------------------------------

    def scored_candidates(
        self, view: WorldView
    ) -> list[tuple[Plan, float, float]]:
        """Introspection: every candidate with its (raw playout score, effective
        score). The numbers behind a decision — so a choice can be shown to win
        ON THE NUMBERS, by a stated margin, deterministically, rather than by a
        tiebreak or luck. Used by the set-piece tests and any decision tooling."""
        candidates = self._generator.generate(view)
        if not candidates:
            return []
        belief = self._belief_builder.build(view)
        me = view.own_player.id
        # Honest playout score (the 12-turn PRIORITY signal), then add the
        # horizon-free base value of any past-horizon goal the playout can't see.
        raw = [self._score(belief, plan, me) for plan in candidates]
        aggr = self._apply_aggression(candidates, raw)
        eff = [aggr[i] + self._base_value(candidates[i]) for i in range(len(candidates))]
        return [(candidates[i], raw[i], eff[i]) for i in range(len(candidates))]

    def _choose_plan(self, view: WorldView) -> Plan:
        candidates = self._generator.generate(view)
        if len(candidates) == 1:
            return candidates[0]
        scored = self.scored_candidates(view)
        eff = [s[2] for s in scored]

        incumbent = self._committed.plan
        best_plan = candidates[0]
        best_score = float("-inf")
        incumbent_score: float | None = None
        for plan, score in zip(candidates, eff):
            if score > best_score:
                best_plan, best_score = plan, score
            if plan == incumbent:
                incumbent_score = score
        # Hysteresis: stick with the incumbent unless the challenger clearly
        # beats it. The bar is SWITCH_MARGIN PLUS an investment-scaled commitment
        # bonus — a campaign with sunk investment in a slow strategic build (a
        # half-built transport / warship) resists abandonment in proportion to
        # what's already committed, so the search sees a 30-turn build through
        # instead of oscillating away from it. It is a bar, not a lock: an urgent,
        # higher-value plan (e.g. defending a threatened home city) can still pay
        # the cost and override. Only applies while the incumbent is still on
        # offer — a vanished target dissolves the commitment.
        if (
            incumbent_score is not None
            and best_plan != incumbent
            and best_score
            < incumbent_score + SWITCH_MARGIN + self._commitment_bonus(view, incumbent)
        ):
            return incumbent
        return best_plan

    def _apply_aggression(
        self, candidates: tuple[Plan, ...], raw: list[float]
    ) -> list[float]:
        """Add the aggression lean to BOLD plans, reverting to flat when a
        caution signal fires (planning/06-aggression-bias.md).

        Caution is derived from the playout, not a hand list: the `floor` is the
        best honest score among the NON-bold (stand-pat) plans, and a bold plan
        scoring more than `caution_tol` below it is losing ground in-horizon
        (known-bad) — so it keeps its flat score. A bold plan at or near the
        floor is merely horizon-blind (slow payoff, no in-horizon loss) and gets
        the bias. `aggression=0` leaves `raw` untouched."""
        if self._aggression <= 0.0:
            return list(raw)
        bold = [self._is_bold(p) for p in candidates]
        passive = [s for s, b in zip(raw, bold) if not b]
        floor = max(passive) if passive else float("-inf")
        cutoff = floor - self._caution_tol
        return [
            s + self._aggression if (b and s >= cutoff) else s
            for s, b in zip(raw, bold)
        ]

    def _base_value(self, plan: Plan) -> float:
        """Horizon-free worth of the plan's PAST-HORIZON goals (planning/07).

        INVADE objectives: their overseas city pays off well past the 12-turn
        horizon, so the playout scores the build cost but never the prize —
        leaving the plan unstartable on playout alone (the concurrency gate proved
        selection is the wall). Crediting the intrinsic city worth lets a combined
        'press-home + build-fleet' plan out-score pure-home (it carries home's
        accurate playout value PLUS the overseas base value).

        SCOUT_SEA goal: discovering the enemy continent — also past-horizon, and
        tagged by the generator (which knows the topology) ONLY when the enemy is
        plausibly overseas, so it can't fire on a land map. Kept below the invade
        base so a discovered target supersedes more scouting.

        In-horizon goals (assault/defend) get nothing — the playout already prices
        them, so land-only games are untouched. Both naval credits come via the
        generator's goal tag, which it sets only when crossing water is the path
        to victory (home explored + no land-reachable enemy), so neither fires on
        a shared-continent map (the land-brawl regression)."""
        value = 0.0
        if self._invade_base > 0.0 and plan.goal is PlanGoal.INVADE:
            value += self._invade_base
        if self._explore_base > 0.0 and plan.goal is PlanGoal.SCOUT_SEA:
            value += self._explore_base
        return value

    @staticmethod
    def _is_bold(plan: Plan) -> bool:
        """A plan is bold if it advances or invests in projection — the actions
        whose payoff tends to land past the search horizon: an ASSAULT/INVADE
        objective, or building a non-army unit (transport/patrol/fighter =
        slow recon/projection investment). Hold, reserve, pure-defense, and
        scout-on-foot-building-armies are the passive baseline, not bold."""
        if any(o.role in (Role.ASSAULT, Role.INVADE) for o in plan.objectives):
            return True
        return plan.production is not UnitKind.ARMY

    @staticmethod
    def _commitment_bonus(view: WorldView, incumbent: Plan) -> float:
        """Investment-scaled stickiness for a campaign mid-way through a slow
        strategic build (build_time >= COMMIT_SLOW_BUILD). Front-loaded so a
        just-started hull isn't dropped next turn, then ramps with build
        progress (the assembled fleet IS progress — §10 'costs are strategy
        costs'). Zero for fast/throwaway production, so ordinary army-building
        stays freely switchable."""
        prod = incumbent.production
        if prod is None:
            return 0.0
        build_time = UNIT_REGISTRY[prod].build_time
        if build_time < COMMIT_SLOW_BUILD:
            return 0.0
        best = 0.0
        for c in view.own_cities:
            if c.production.building is prod and c.production.work > 0:
                best = max(best, min(1.0, c.production.work / build_time))
        if best <= 0.0:
            return 0.0
        return COMMIT_BASE + COMMIT_SCALE * best

    def _score(self, belief: Game, plan: Plan, me: PlayerId) -> float:
        """Mean horizon evaluation of `plan` over `samples` playouts.

        Sample `i` is reseeded identically for every candidate (common
        random numbers), so the same battle luck hits every plan."""
        total = 0.0
        for sample in range(self._samples):
            controllers: dict[PlayerId, AIController] = {}
            for player in belief.players:
                if player.id == me:
                    controllers[player.id] = PlanFollower(plan)
                else:
                    controllers[player.id] = BaselineAI()
            sim = self._playout.clone(belief, controllers)
            sim.rng.seed(sim.turn + sample * _SAMPLE_STRIDE)
            for _ in range(self._horizon):
                sim.run_turn()
                if sim.is_over():
                    break
            total += self._evaluator.evaluate(sim, me)
        return total / self._samples

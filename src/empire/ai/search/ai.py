"""`SearchAI`: plan-space lookahead (Phase 15.8, design §9 of 03-ai-design).

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
from empire.ai.search.plan import Plan, Role
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
# every turn — the Phase 15.8 stall trace showed assault strength oscillating
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
DEFAULT_AGGRESSION = 40.0
DEFAULT_CAUTION_TOL = 20.0


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
    ) -> None:
        self._horizon: int = horizon
        self._samples: int = max(1, samples)
        self._aggression: float = aggression
        self._caution_tol: float = caution_tol
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

    def _choose_plan(self, view: WorldView) -> Plan:
        candidates = self._generator.generate(view)
        if len(candidates) == 1:
            return candidates[0]
        belief = self._belief_builder.build(view)
        me = view.own_player.id

        # Honest playout score per candidate, then the aggression lean.
        raw = [self._score(belief, plan, me) for plan in candidates]
        eff = self._apply_aggression(candidates, raw)

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

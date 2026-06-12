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
from empire.ai.search.plan import Plan
from empire.ai.search.playout import PlayoutModel
from empire.contracts.controller import AIController
from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.game import Game
from empire.core.identity import PlayerId, UnitId

DEFAULT_HORIZON = 12
DEFAULT_SAMPLES = 3
# Decorrelates sample streams while keeping them deterministic.
_SAMPLE_STRIDE = 104_729
# A challenger must beat the incumbent plan's score by this much to displace
# it (≈ one army's worth). Without it, sampling noise flips near-tied plans
# every turn — the Phase 15.8 stall trace showed assault strength oscillating
# 3↔5 each turn, reshuffling fist membership so no storm ever cohered.
SWITCH_MARGIN = 10.0


class SearchAI:
    """Greedy plan search over playouts; satisfies `AIController`."""

    def __init__(
        self,
        horizon: int = DEFAULT_HORIZON,
        samples: int = DEFAULT_SAMPLES,
        generator: CandidateGenerator | None = None,
        evaluator: Evaluator | None = None,
    ) -> None:
        self._horizon: int = horizon
        self._samples: int = max(1, samples)
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

        incumbent = self._committed.plan
        best_plan = candidates[0]
        best_score = float("-inf")
        incumbent_score: float | None = None
        for plan in candidates:
            score = self._score(belief, plan, me)
            if score > best_score:
                best_plan, best_score = plan, score
            if plan == incumbent:
                incumbent_score = score
        # Hysteresis: stick with the incumbent unless the challenger clearly
        # beats it (see SWITCH_MARGIN). Only applies when the incumbent is
        # still on offer — a vanished target dissolves the commitment.
        if (
            incumbent_score is not None
            and best_plan != incumbent
            and best_score < incumbent_score + SWITCH_MARGIN
        ):
            return incumbent
        return best_plan

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

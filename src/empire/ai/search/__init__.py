"""`SearchAI`: plan-space lookahead.

Instead of a hand-designed doctrine, the AI searches a small space of
candidate plans each turn, scoring each by cloning the game and simulating
it forward against a model of the opponent.

Components: `PlayoutModel` (forward model), `Plan`/`PlanFollower`
(candidate course of action + its executor), `Evaluator` (horizon scoring),
`CandidateGenerator` (plan proposals), `BeliefBuilder` (fog-honest world),
and `SearchAI` (the controller tying them together).
"""

from empire.ai.search.ai import SearchAI
from empire.ai.search.playout import PlayoutModel

__all__ = ["PlayoutModel", "SearchAI"]

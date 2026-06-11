"""`SearchAI` (Phase 15.8): plan-space lookahead.

Instead of a hand-designed doctrine, the AI searches a small space of
candidate plans each turn, scoring each by cloning the game and simulating
it forward against a model of the opponent. Design rationale:
`planning/03-ai-design.md` §9.

This package builds up in steps. Step 0 ships `PlayoutModel`, the forward
model every later piece simulates through.
"""

from empire.ai.search.playout import PlayoutModel

__all__ = ["PlayoutModel"]

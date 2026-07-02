"""`BaselineAI`: greedy per-unit AI used as a regression baseline.

See `planning/03-ai-design.md` §2. The package layout splits the
dispatcher (`BaselineAI`) from the
per-unit-kind decision layer (`BaselineTactical`) so each unit's heuristic
sits in one place and is independently tunable.
"""

from empire.ai.baseline.controller import BaselineAI

__all__ = ["BaselineAI"]

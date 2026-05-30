"""`AIMemory`: the strategic AI's cross-turn state (see
`planning/03-ai-design.md` §3.5).

Minimal in Phase 12 — the `DeterministicStrategist` re-plans from intel each
turn, so it carries only the previous turn's goals (for continuity and
anti-thrash heuristics that land in later phases). Active task forces, learned
beliefs, and production history attach here in Phases 13+, and the whole thing
is serialized with the save in Phase 16.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from empire.ai.strategic.goals.base import Goal


@dataclass
class AIMemory:
    """Persistent state the AI carries between turns."""

    last_goals: tuple[Goal, ...] = field(default=())

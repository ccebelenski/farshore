"""`AIController` Protocol — the engine's view of any AI personality.

Any class with these three methods satisfies the Protocol. The engine calls
`plan_turn()` at the start of an AI player's turn, then runs the resulting
`TurnPlan` one move at a time, invoking `revise_move()` only when a
surprise invalidates a planned step.

`NullController` is included as both a proof of Protocol satisfiability and
a useful no-op opponent (e.g., for hotseat without AI, or for engine-only
smoke testing).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.identity import UnitId


@runtime_checkable
class AIController(Protocol):
    """The engine-side contract every AI personality satisfies."""

    def name(self) -> str:
        """Short human-readable identifier; appears in logs and the TUI."""
        ...

    def plan_turn(self, view: WorldView) -> TurnPlan:
        """Produce this turn's intended actions."""
        ...

    def revise_move(
        self,
        unit_id: UnitId,
        surprise: Surprise,
        view: WorldView,
    ) -> UnitMove:
        """Return a single revised step after a mid-turn surprise.

        Scope is intentionally narrow: one unit, one next step. Strategic
        state (goals, task forces) is *not* re-evaluated here; that happens
        at the next turn boundary via `plan_turn`.
        """
        ...


class NullController:
    """A no-op controller. Never plans moves, never wakes up.

    Useful as:
    - A test fixture verifying the engine can drive a turn end-to-end with
      no AI activity.
    - An opponent slot for engine smoke testing without AI complexity.
    - Proof that `AIController` is satisfiable.
    """

    def name(self) -> str:
        return "Null"

    def plan_turn(self, view: WorldView) -> TurnPlan:
        del view
        return TurnPlan()

    def revise_move(
        self,
        unit_id: UnitId,
        surprise: Surprise,
        view: WorldView,
    ) -> UnitMove:
        del surprise, view
        # Empty path = no movement = "stay put" for this revision.
        return UnitMove(unit_id=unit_id)

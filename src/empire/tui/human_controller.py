"""`HumanController`: an `AIController` driven by TUI input.

The TUI assembles a `TurnPlan` interactively (cursor moves, production
modal, etc.), then calls `set_plan()` before invoking `Game.run_turn()`.
The engine drives the round normally; when it asks the human's controller
for `plan_turn()`, the controller returns the pre-loaded plan and clears
its slot.

Mid-turn revision returns an empty `UnitMove` (no further movement *this
turn*). This is **not** the same as putting the unit on persistent
sentry — surprise must never cause a unit to enter sentry. If anything,
a sentried unit hit by a surprise should auto-*wake* so the player can
react. Persistent-sentry mechanics land in Phase 10.6; until then,
revise_move just halts the current path and the next turn's planner
picks up.
"""

from __future__ import annotations

from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.identity import UnitId


class HumanController:
    """Replays a TUI-assembled `TurnPlan` when the engine asks for one."""

    def __init__(self) -> None:
        self._pending: TurnPlan | None = None

    def name(self) -> str:
        return "Human"

    def set_plan(self, plan: TurnPlan) -> None:
        """Stage a plan that the next `plan_turn()` call will return."""
        self._pending = plan

    def plan_turn(self, view: WorldView) -> TurnPlan:
        del view
        plan = self._pending if self._pending is not None else TurnPlan()
        self._pending = None
        return plan

    def revise_move(
        self,
        unit_id: UnitId,
        surprise: Surprise,
        view: WorldView,
    ) -> UnitMove:
        del surprise, view
        # Stop the rest of this turn's path for *this turn only*. Empty path
        # = "no more moves this turn". The unit is NOT put into persistent
        # sentry — auto-sentry on surprise would be exactly backwards from
        # the desired behavior (surprises should *wake* sentried units, not
        # silence active ones).
        return UnitMove(unit_id=unit_id)

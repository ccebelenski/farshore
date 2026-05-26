"""`HumanController`: an `AIController` driven by TUI input.

The TUI assembles a `TurnPlan` interactively (cursor moves, production
modal, etc.), then calls `set_plan()` before invoking `Game.run_turn()`.
The engine drives the round normally; when it asks the human's controller
for `plan_turn()`, the controller returns the pre-loaded plan and clears
its slot.

Mid-turn revision is a no-op for now — surprises during the human's plan
execution become sentry/skip events. A future iteration could pause the
engine and pop up a "your path was blocked, where to?" prompt.
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
        # Skip the rest of this unit's path. The next turn boundary will let
        # the human re-plan against the new state.
        return UnitMove(unit_id=unit_id)

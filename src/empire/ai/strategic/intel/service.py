"""`IntelService`: assembles a frozen `IntelReport` from a `WorldView`.

The service is stateless — it just runs the four independent assessments and
packages the results. Holding no state is what makes its output reproducible:
the same `WorldView` always yields an equal report (Phase 11 purity gate).
"""

from __future__ import annotations

from empire.ai.strategic.intel.chokepoints import find_chokepoints
from empire.ai.strategic.intel.opportunities import assess_opportunities
from empire.ai.strategic.intel.report import IntelReport
from empire.ai.strategic.intel.theaters import detect_theaters
from empire.ai.strategic.intel.threats import assess_threats
from empire.contracts.world_view import WorldView


class IntelService:
    """Situation assessment for the strategist (see `03-ai-design.md` §3.1)."""

    def assess(self, view: WorldView) -> IntelReport:
        """Produce the frozen intel report for `view`'s player at its turn."""
        return IntelReport(
            turn=view.turn,
            threats=assess_threats(view),
            opportunities=assess_opportunities(view),
            chokepoints=find_chokepoints(view),
            theaters=detect_theaters(view),
        )

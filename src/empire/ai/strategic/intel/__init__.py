"""Situation assessment for the strategic AI.

`IntelService.assess(view)` turns a live `WorldView` into a frozen
`IntelReport` of threats, opportunities, choke points, and theaters. See
`planning/03-ai-design.md` §3.1 and `planning/05-implementation-plan.md`
Phase 11.
"""

from empire.ai.strategic.intel.report import (
    ChokeAxis,
    ChokePoint,
    ChokePointKind,
    IntelReport,
    Opportunity,
    OpportunityKind,
    Theater,
    TheaterState,
    Threat,
)
from empire.ai.strategic.intel.service import IntelService

__all__ = [
    "ChokeAxis",
    "ChokePoint",
    "ChokePointKind",
    "IntelReport",
    "IntelService",
    "Opportunity",
    "OpportunityKind",
    "Theater",
    "TheaterState",
    "Threat",
]

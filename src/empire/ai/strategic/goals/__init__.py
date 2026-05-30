"""The strategist's `Goal` hierarchy (see `planning/03-ai-design.md` §3.2)."""

from empire.ai.strategic.goals.base import (
    ForceComposition,
    Goal,
    GoalKind,
    ResourceBudget,
)
from empire.ai.strategic.goals.concrete import (
    BuildForcesGoal,
    CaptureCityGoal,
    DefendCityGoal,
    DenyContinentGoal,
    ExploreAreaGoal,
    ProjectPowerGoal,
    goal_from_dict,
)

__all__ = [
    "BuildForcesGoal",
    "CaptureCityGoal",
    "DefendCityGoal",
    "DenyContinentGoal",
    "ExploreAreaGoal",
    "ForceComposition",
    "Goal",
    "GoalKind",
    "ProjectPowerGoal",
    "ResourceBudget",
    "goal_from_dict",
]

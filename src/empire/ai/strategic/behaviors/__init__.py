"""Per-unit behaviors and the `behavior_for` registry (see
`planning/03-ai-design.md` §3.4)."""

from empire.ai.strategic.behaviors.air import (
    FighterScoutBehavior,
    FighterStrikeBehavior,
)
from empire.ai.strategic.behaviors.army import (
    ArmyAssaultBehavior,
    ArmyGarrisonBehavior,
)
from empire.ai.strategic.behaviors.base import Behavior, DefaultBehavior
from empire.ai.strategic.behaviors.naval import (
    ShipEscortBehavior,
    ShipPatrolBehavior,
    SubAmbushBehavior,
    TransportFerryBehavior,
)
from empire.ai.strategic.behaviors.registry import DEFAULT_BEHAVIOR, behavior_for

__all__ = [
    "DEFAULT_BEHAVIOR",
    "ArmyAssaultBehavior",
    "ArmyGarrisonBehavior",
    "Behavior",
    "DefaultBehavior",
    "FighterScoutBehavior",
    "FighterStrikeBehavior",
    "ShipEscortBehavior",
    "ShipPatrolBehavior",
    "SubAmbushBehavior",
    "TransportFerryBehavior",
    "behavior_for",
]

"""`behavior_for(kind, role)` — the (UnitKind, Role) → Behavior lookup.

This is the AI-layer stand-in for the design's `Unit.behavior_for` (which
`core` can't host, since it may not depend on `ai`). Each unit kind's roles
map to the stateless behavior singletons defined in this package; any pair
without an entry resolves to `DefaultBehavior` (hold position), so the lookup
is total over every (kind, role).
"""

from __future__ import annotations

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
from empire.ai.strategic.operational import Role
from empire.core.unit import UnitKind

DEFAULT_BEHAVIOR = DefaultBehavior()

# Stateless singletons.
_ASSAULT = ArmyAssaultBehavior()
_GARRISON = ArmyGarrisonBehavior()
_STRIKE = FighterStrikeBehavior()
_SCOUT = FighterScoutBehavior()
_FERRY = TransportFerryBehavior()
_ESCORT = ShipEscortBehavior()
_PATROL = ShipPatrolBehavior()
_AMBUSH = SubAmbushBehavior()

_WARSHIP_ROLES: dict[Role, Behavior] = {
    Role.ESCORT: _ESCORT,
    Role.ASSAULT: _PATROL,
    Role.SCOUT: _PATROL,
}

_REGISTRY: dict[UnitKind, dict[Role, Behavior]] = {
    UnitKind.ARMY: {
        Role.ASSAULT: _ASSAULT,
        Role.GARRISON: _GARRISON,
        Role.SCOUT: _ASSAULT,  # marching toward the explore target IS scouting
    },
    UnitKind.FIGHTER: {
        Role.ASSAULT: _STRIKE,
        Role.ESCORT: _STRIKE,
        Role.SCOUT: _SCOUT,
    },
    UnitKind.TRANSPORT: {Role.TRANSPORT: _FERRY},
    UnitKind.CARRIER: {Role.TRANSPORT: _FERRY, Role.ESCORT: _PATROL},
    UnitKind.PATROL: _WARSHIP_ROLES,
    UnitKind.DESTROYER: _WARSHIP_ROLES,
    UnitKind.BATTLESHIP: _WARSHIP_ROLES,
    UnitKind.SUBMARINE: {
        Role.ESCORT: _AMBUSH,
        Role.ASSAULT: _AMBUSH,
        Role.SCOUT: _AMBUSH,
    },
    UnitKind.SATELLITE: {},
}


def behavior_for(kind: UnitKind, role: Role) -> Behavior:
    """The behavior for a unit of `kind` filling `role`, or `DefaultBehavior`."""
    return _REGISTRY.get(kind, {}).get(role, DEFAULT_BEHAVIOR)

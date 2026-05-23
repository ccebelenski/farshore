"""`Surprise`: the tagged-union of events that can invalidate a planned `UnitMove`
mid-turn.

When the engine encounters one of these during turn execution, it delegates
to `AIController.revise_move()` for a single revised step. The AI is not
expected to re-strategize at this layer; that happens at the next turn
boundary (see `planning/03-ai-design.md` §1 reactivity boundary).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from empire.contracts.world_view import KnownEnemyUnit
from empire.core.coord import Coord
from empire.core.identity import CityId, UnitId


class BlockedBy(Enum):
    """Why a planned step is no longer passable."""

    OWN_UNIT = "own_unit"          # a friendly unit sits in the target cell
    ENEMY_UNIT = "enemy_unit"      # an enemy now occupies the target cell
    TERRAIN = "terrain"            # target's terrain is illegal for this unit
    OUT_OF_BOUNDS = "out_of_bounds"


class Surprise:
    """Marker base for surprise events. Concrete subclasses carry payload.

    Not declared as a frozen dataclass so subclasses can each be frozen with
    their own fields; this class itself has no payload.
    """


@dataclass(frozen=True, slots=True)
class EnemySighted(Surprise):
    """An enemy appeared in or near the unit's planned path."""

    enemy: KnownEnemyUnit
    at: Coord


@dataclass(frozen=True, slots=True)
class PathBlocked(Surprise):
    """The next cell on the path is no longer passable for this unit."""

    blocked_at: Coord
    by: BlockedBy


@dataclass(frozen=True, slots=True)
class TargetLost(Surprise):
    """A city or unit the mission depended on is gone (destroyed, captured)."""

    # Either a CityId or a UnitId. Both are int newtypes; the consumer knows
    # the goal type and what to look up.
    target_id: CityId | UnitId


@dataclass(frozen=True, slots=True)
class EscortLost(Surprise):
    """An escort (e.g., a destroyer guarding a transport) was destroyed."""

    escort_id: UnitId


@dataclass(frozen=True, slots=True)
class TerrainImpassable(Surprise):
    """Predicted terrain turned out to be illegal for this unit kind.

    Distinct from `PathBlocked(by=TERRAIN)` because this fires when the AI
    planned a path assuming unexplored cells were one terrain and they turned
    out to be another (the predicted-terrain hazard called out in
    `planning/03-ai-design.md` §3.1).
    """

    at: Coord

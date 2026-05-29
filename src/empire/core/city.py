"""City and its production state.

Phase-2 scope: structural only. `ProductionState.tick()` updates the
accumulated work but the actual unit-emission and production-ticking-during-
turn-phase logic lands in Phase 8 (see `planning/05-implementation-plan.md`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from empire.core.coord import Coord
from empire.core.identity import CityId
from empire.core.unit import UNIT_REGISTRY, UnitKind

if TYPE_CHECKING:
    from empire.core.player import Player


class OrderKind(Enum):
    """Default per-city, per-unit-kind order issued on production (spec §5.3)."""

    SENTRY = "sentry"
    MOVE_TO = "move_to"
    ATTACK_NEAREST_ENEMY = "attack_nearest_enemy"


@dataclass(frozen=True, slots=True)
class DefaultOrder:
    """A city's standing instruction for a freshly produced unit kind.

    `MOVE_TO` carries the destination `target`; the other kinds leave it
    None. Applied by the production tick, which translates the order into
    the unit's initial standing order (see engine `apply_default_order`).
    """

    kind: OrderKind
    target: Coord | None = None


class ProductionState:
    """Tracks what a city is building and how much work is accumulated.

    The classes that mutate this state live in this module's package; the
    actual turn-phase glue lands in Phase 8.
    """

    def __init__(self, building: UnitKind | None = None, work: int = 0) -> None:
        self.building: UnitKind | None = building
        self.work: int = work

    def set_target(self, kind: UnitKind, penalty_divisor: int) -> None:
        """Set the production target.

        If the new target differs from the current one, apply a setback
        proportional to the *current* target's build time divided by
        `penalty_divisor` (see spec §5.2). Setback may take `work` negative.
        """
        if self.building is not None and self.building is not kind:
            current_build_time = UNIT_REGISTRY[self.building].build_time
            self.work -= current_build_time // penalty_divisor
        self.building = kind

    def tick(self) -> None:
        """Add one production point toward the current target."""
        if self.building is not None:
            self.work += 1

    def ready(self) -> bool:
        """True iff accumulated work meets or exceeds the target's build time."""
        if self.building is None:
            return False
        return self.work >= UNIT_REGISTRY[self.building].build_time

    def consume(self) -> None:
        """Consume one build's worth of work, leaving the target unchanged."""
        if self.building is None:
            return
        self.work -= UNIT_REGISTRY[self.building].build_time


def _empty_orders() -> dict[UnitKind, DefaultOrder]:
    return {}


CITY_SCAN_RANGE = 2  # Chebyshev radius a city sees around itself (spec §6.2).


@dataclass(slots=True)
class City:
    """A city on the map: a production center owned by a player or neutral.

    `default_orders` maps a unit kind to the order issued to that kind when
    it rolls off the production line.
    """

    id: CityId
    coord: Coord
    owner: Player | None
    production: ProductionState = field(default_factory=ProductionState)
    default_orders: dict[UnitKind, DefaultOrder] = field(default_factory=_empty_orders)

    @property
    def scan_range(self) -> int:
        """Chebyshev radius of vision a city grants its owner."""
        return CITY_SCAN_RANGE

    def is_neutral(self) -> bool:
        return self.owner is None

    def default_order_for(self, kind: UnitKind) -> DefaultOrder:
        return self.default_orders.get(kind, DefaultOrder(OrderKind.SENTRY))

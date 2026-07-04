"""City and its production state.

`ProductionState` tracks accumulated work; the unit-emission and
turn-phase glue lives in `empire.core.engine` (`run_production_tick`).
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

    The turn-phase glue that drives it lives in `empire.core.engine`.
    """

    def __init__(self, building: UnitKind | None = None, work: int = 0) -> None:
        self.building: UnitKind | None = building
        self.work: int = work

    def set_target(self, kind: UnitKind) -> None:
        """Set the production target.

        Switching to a *different* unit kind discards all accumulated work:
        effort toward one unit does not transfer to another (a half-built
        battleship is not partial progress toward an army — see spec §5.2).
        Re-setting the current target, or setting one from idle, keeps the
        accumulator untouched.
        """
        if self.building is not None and self.building is not kind:
            self.work = 0
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
    # Transient: artillery has one shot per round (spec §4.7). Reset to True
    # at the start of each round; consumed (hit or miss) when the city fires.
    # Not serialized — it is purely within-round state.
    artillery_ready: bool = True

    @property
    def scan_range(self) -> int:
        """Chebyshev radius of vision a city grants its owner."""
        return CITY_SCAN_RANGE

    def is_neutral(self) -> bool:
        return self.owner is None

    def default_order_for(self, kind: UnitKind) -> DefaultOrder:
        return self.default_orders.get(kind, DefaultOrder(OrderKind.SENTRY))

"""Unit hierarchy: the `Unit` ABC, `UnitKind` enum, nine concrete subclasses,
and a `UNIT_REGISTRY` mapping kind to class.

Phase-2 scope: structural only. No movement rules, no combat, no damage
scaling logic — those land in Phase 8. The class-level attributes are our
v1 design choices (see `planning/01-game-rules-spec.md` §2) and are subject
to playtest tuning.

`Unit.coord` is read-only externally; only `Map.move_unit / place_unit`
should mutate position via `_set_coord` (see `planning/04-class-hierarchy.md`
§2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from empire.core.coord import Coord, Direction
from empire.core.identity import UnitId
from empire.core.tile import TerrainKind

if TYPE_CHECKING:
    from empire.core.player import Player
    from empire.core.standing_order import StandingOrder


# A sentinel for units that have no fuel/range concept (ground, sea units).
RANGE_UNLIMITED: int = -1


class UnitKind(Enum):
    """All nine unit kinds. String values for save-file stability."""

    ARMY = "army"
    FIGHTER = "fighter"
    PATROL = "patrol"
    DESTROYER = "destroyer"
    SUBMARINE = "submarine"
    TRANSPORT = "transport"
    CARRIER = "carrier"
    BATTLESHIP = "battleship"
    SATELLITE = "satellite"


class Unit(ABC):
    """Abstract base for all unit kinds.

    Per-kind constants (`max_hits`, `speed`, `strength`, etc.) are ClassVars
    overridden by each concrete subclass. Per-instance state (`hits`,
    `range`) is mutable; coordinate is read-only externally.
    """

    # Per-subclass class-level attributes (must be overridden):
    kind: ClassVar[UnitKind]
    max_hits: ClassVar[int]
    speed: ClassVar[int]
    strength: ClassVar[int]
    capacity: ClassVar[int]
    base_range: ClassVar[int]
    build_time: ClassVar[int]
    legal_terrain: ClassVar[frozenset[TerrainKind]]
    symbol: ClassVar[str]
    scan_range: ClassVar[int]  # Chebyshev radius of vision per spec §6.2
    # The single unit kind this carrier may hold as cargo (spec §2.2), or
    # None for units that carry nothing. Transport=ARMY, Carrier=FIGHTER.
    cargo_kind: ClassVar[UnitKind | None] = None

    def __init__(self, id_: UnitId, owner: Player, coord: Coord) -> None:
        self.id: UnitId = id_
        self.owner: Player = owner
        self._coord: Coord = coord
        self.hits: int = type(self).max_hits
        self.range: int = type(self).base_range
        # Cross-turn order: Heading, PatrolPath, Sentry, or None. Engine
        # applies one step per turn before the controller is consulted.
        self.standing_order: StandingOrder | None = None
        # Cargo (spec §2.2/§3.4). `cargo` holds the IDs of units aboard this
        # carrier, in load order; non-empty only for Transport/Carrier.
        # `carried_by` is the carrier this unit is aboard (None when on the
        # map independently). `loaded_this_turn` blocks a same-turn
        # load→unload round-trip (spec §3.4).
        self.cargo: list[UnitId] = []
        self.carried_by: UnitId | None = None
        self.loaded_this_turn: bool = False
        # Orbital heading for Satellites (spec §2.4); None for everything
        # else. `_moved_this_round` is end-of-round bookkeeping for the
        # repair rule (spec §2.3) — a unit only heals if it stayed put.
        self.orbit_direction: Direction | None = None
        self._moved_this_round: bool = False
        # Transient (within-round): set when city artillery fires on this unit
        # (spec §4.7), cleared at the start of each round. A pinned land/air
        # unit cannot move this round; a pinned naval unit moves at half budget.
        self.pinned: bool = False

    def is_aboard(self) -> bool:
        """True if this unit is currently cargo aboard a carrier."""
        return self.carried_by is not None

    def can_carry(self, other: Unit) -> bool:
        """True if `other` may load onto this carrier right now.

        Requires: this unit is a carrier for `other`'s kind, `other` is
        friendly, and there is free (damage-scaled) capacity. Aboard units
        cannot themselves carry, and a carrier cannot board another carrier.
        """
        cls = type(self)
        if cls.cargo_kind is None or other.kind is not cls.cargo_kind:
            return False
        if other.owner is not self.owner:
            return False
        return len(self.cargo) < self.effective_capacity()

    @property
    def coord(self) -> Coord:
        return self._coord

    def _set_coord(self, c: Coord) -> None:
        """Package-private. Only `Map.move_unit` / `Map.place_unit` may call this."""
        self._coord = c

    def moves_this_turn(self) -> int:
        """Damage-scaled movement budget: `ceil(speed * hits / max_hits)`.

        A unit pinned by city artillery this round (spec §4.7) loses mobility:
        land and air units cannot move at all; naval units move at half budget
        (rounded down). `pinned` is only ever set under the artillery ruleset,
        so this is a no-op for STANDARD play.
        """
        cls = type(self)
        # Integer ceil-division: (a + b - 1) // b.
        budget = (cls.speed * self.hits + cls.max_hits - 1) // cls.max_hits
        if self.pinned:
            if self.kind in (UnitKind.ARMY, UnitKind.FIGHTER):
                return 0
            return budget // 2
        return budget

    def effective_capacity(self) -> int:
        """Damage-scaled cargo capacity: `ceil(capacity * hits / max_hits)`."""
        cls = type(self)
        if cls.capacity == 0:
            return 0
        return (cls.capacity * self.hits + cls.max_hits - 1) // cls.max_hits

    @abstractmethod
    def attack_preferences(self) -> str:
        """Per-kind attack-preference ordering (see spec §4.3).

        Returns a string of unit-kind characters in order from "most prefers
        to fight" to "least prefers to fight." Combat strength against each
        target derives from where the target sits in this ordering. Specific
        formulas land in Phase 6.
        """

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id}, coord={self._coord}, hits={self.hits})"


# -----------------------------------------------------------------------------
# Terrain sets shared across kinds.
# -----------------------------------------------------------------------------

_LAND_OR_CITY: frozenset[TerrainKind] = frozenset({TerrainKind.LAND, TerrainKind.CITY})
_WATER_OR_CITY: frozenset[TerrainKind] = frozenset({TerrainKind.WATER, TerrainKind.CITY})
_ANY_TERRAIN: frozenset[TerrainKind] = frozenset(TerrainKind)


# -----------------------------------------------------------------------------
# Concrete unit subclasses. Class-level attribute values are v1 defaults,
# tunable via playtesting.
# -----------------------------------------------------------------------------


class Army(Unit):
    kind = UnitKind.ARMY
    max_hits = 1
    speed = 1
    strength = 1
    capacity = 0
    base_range = RANGE_UNLIMITED
    build_time = 5
    legal_terrain = _LAND_OR_CITY
    symbol = "A"
    scan_range = 2

    def attack_preferences(self) -> str:
        return "ATFPDCSB"


class Fighter(Unit):
    kind = UnitKind.FIGHTER
    max_hits = 1
    speed = 8
    strength = 1
    capacity = 0
    base_range = 20  # cells; RuleSet.fighter_base_range overrides at construction time
    build_time = 10
    legal_terrain = _ANY_TERRAIN
    symbol = "F"
    scan_range = 5

    def attack_preferences(self) -> str:
        return "FATCDSPB"


class Patrol(Unit):
    kind = UnitKind.PATROL
    max_hits = 2
    speed = 4
    strength = 1
    capacity = 0
    base_range = RANGE_UNLIMITED
    build_time = 15
    legal_terrain = _WATER_OR_CITY
    symbol = "P"
    scan_range = 3

    def attack_preferences(self) -> str:
        return "PTDSCBAF"


class Destroyer(Unit):
    kind = UnitKind.DESTROYER
    max_hits = 3
    speed = 3
    strength = 2
    capacity = 0
    base_range = RANGE_UNLIMITED
    build_time = 20
    legal_terrain = _WATER_OR_CITY
    symbol = "D"
    scan_range = 3

    def attack_preferences(self) -> str:
        return "DSPTCBAF"


class Submarine(Unit):
    kind = UnitKind.SUBMARINE
    max_hits = 2
    speed = 2
    strength = 3
    capacity = 0
    base_range = RANGE_UNLIMITED
    build_time = 25
    legal_terrain = _WATER_OR_CITY
    symbol = "S"
    scan_range = 1

    def attack_preferences(self) -> str:
        return "TCBPDSAF"


class Transport(Unit):
    kind = UnitKind.TRANSPORT
    max_hits = 3
    speed = 2
    strength = 0
    capacity = 6
    base_range = RANGE_UNLIMITED
    build_time = 30
    legal_terrain = _WATER_OR_CITY
    symbol = "T"
    scan_range = 2
    cargo_kind = UnitKind.ARMY

    def attack_preferences(self) -> str:
        return ""  # Transports do not initiate combat


class Carrier(Unit):
    kind = UnitKind.CARRIER
    max_hits = 8
    speed = 2
    strength = 1
    capacity = 8
    base_range = RANGE_UNLIMITED
    build_time = 40
    legal_terrain = _WATER_OR_CITY
    symbol = "C"
    scan_range = 4
    cargo_kind = UnitKind.FIGHTER

    def attack_preferences(self) -> str:
        return "CTPDSBAF"


class Battleship(Unit):
    kind = UnitKind.BATTLESHIP
    max_hits = 18
    speed = 2
    strength = 4
    capacity = 0
    base_range = RANGE_UNLIMITED
    build_time = 50
    legal_terrain = _WATER_OR_CITY
    symbol = "B"
    scan_range = 3

    def attack_preferences(self) -> str:
        return "BCSDPTAF"


class Satellite(Unit):
    kind = UnitKind.SATELLITE
    max_hits = 1
    speed = 1
    strength = 0
    capacity = 0
    base_range = 50  # turns of orbital lifetime; RuleSet.satellite_range overrides
    build_time = 50
    legal_terrain = _ANY_TERRAIN  # orbits over anything
    symbol = "@"  # distinct from the city glyph "*" (they collided — confusing)
    scan_range = 10

    def __init__(self, id_: UnitId, owner: Player, coord: Coord) -> None:
        super().__init__(id_, owner, coord)
        # A freshly launched satellite orbits eastward until it bounces off
        # a map edge (spec §2.4). Save/load preserves the live heading.
        self.orbit_direction = Direction.E

    def attack_preferences(self) -> str:
        return ""  # Satellites cannot attack (or be attacked)


# -----------------------------------------------------------------------------
# Registry: maps UnitKind to its concrete class. Used by save/load and by AI
# code that needs to dispatch on a kind value.
# -----------------------------------------------------------------------------

UNIT_REGISTRY: dict[UnitKind, type[Unit]] = {
    UnitKind.ARMY: Army,
    UnitKind.FIGHTER: Fighter,
    UnitKind.PATROL: Patrol,
    UnitKind.DESTROYER: Destroyer,
    UnitKind.SUBMARINE: Submarine,
    UnitKind.TRANSPORT: Transport,
    UnitKind.CARRIER: Carrier,
    UnitKind.BATTLESHIP: Battleship,
    UnitKind.SATELLITE: Satellite,
}

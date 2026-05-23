"""Engine helpers: movement validation, scan computation, production-phase
glue, and movement-phase resolution.

These live in `core` because `Game`/`TurnManager` is in `core` and these
are the bits that wire together the rule book. To keep `core` free of
import dependencies on `empire.combat`, the combat resolver is supplied
to the engine via a `CombatResolverProtocol` defined here — the concrete
`CombatResolver` from `empire.combat` satisfies it structurally.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, UnitId
from empire.core.map import Map
from empire.core.ruleset import RuleSet
from empire.core.tile import TerrainKind
from empire.core.unit import UNIT_REGISTRY, Unit, UnitKind

if TYPE_CHECKING:
    from empire.core.player import Player


# -----------------------------------------------------------------------------
# Combat resolver protocol
# -----------------------------------------------------------------------------


class CombatResolverProtocol(Protocol):
    """Minimal interface the engine needs from a combat resolver.

    `resolve` runs the fight, mutates the units' `hits` in place, and
    returns an opaque result. The engine inspects HP directly to determine
    outcome — the concrete result type lives in `empire.combat`.
    """

    def resolve(
        self,
        attacker: Unit,
        defender: Unit,
        rng: random.Random,
    ) -> object: ...


NextUnitIdFn = Callable[[], UnitId]


# -----------------------------------------------------------------------------
# Movement validation
# -----------------------------------------------------------------------------


def can_enter_terrain(unit: Unit, terrain: TerrainKind, real_map: Map, to: Coord) -> bool:
    """True if `unit` can legally occupy a cell of the given terrain at `to`.

    Sea units (those whose `legal_terrain` excludes LAND) may enter a CITY
    only when the city is adjacent to water — treated as a port per spec
    §3.2.
    """
    if terrain not in type(unit).legal_terrain:
        return False
    if terrain is TerrainKind.CITY and TerrainKind.LAND not in type(unit).legal_terrain:
        # Sea unit: the city must be a port (adjacent to water).
        for n in to.neighbors():
            if real_map.in_bounds(n) and real_map.terrain_at(n) is TerrainKind.WATER:
                return True
        return False
    return True


def is_legal_step(unit: Unit, to: Coord, real_map: Map, rules: RuleSet) -> bool:
    """Validate a single one-cell step.

    Checks:
    - Destination is in bounds and on-board.
    - Destination terrain is legal for the unit.
    - Step is at most a 1-cell Chebyshev move.
    """
    del rules  # reserved for future rule toggles
    if not real_map.in_bounds(to):
        return False
    tile = real_map.tile(to)
    if not tile.on_board:
        return False
    if unit.coord.chebyshev_to(to) > 1:
        return False
    return can_enter_terrain(unit, tile.terrain, real_map, to)


# -----------------------------------------------------------------------------
# Scan / visibility
# -----------------------------------------------------------------------------


def scan_set_for_player(player: Player, real_map: Map) -> set[Coord]:
    """Compute the set of cells visible to `player` this turn.

    Visible cells come from:
    - Each owned unit's Chebyshev disc of radius `scan_range`.
    - Each owned city's Chebyshev disc of radius `City.scan_range`.

    Cells outside the map bounds are filtered out.
    """
    visible: set[Coord] = set()
    width, height = real_map.width, real_map.height

    def add_disc(center: Coord, radius: int) -> None:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx, ny = center.x + dx, center.y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    visible.add(Coord(nx, ny))

    for unit in real_map.all_units():
        if unit.owner is player:
            add_disc(unit.coord, type(unit).scan_range)
    for city in real_map.cities():
        if city.owner is player:
            add_disc(city.coord, city.scan_range)

    return visible


# -----------------------------------------------------------------------------
# Production
# -----------------------------------------------------------------------------


def run_production_tick(
    player: Player,
    real_map: Map,
    rules: RuleSet,
    next_unit_id_fn: NextUnitIdFn,
) -> list[Unit]:
    """Tick production for every city owned by `player`.

    Each tick advances the city's production by one. When the
    accumulated work reaches the build target, a unit is emitted on the
    city's cell (subject to one-unit-per-cell unless `allow_unit_stacking`)
    and placed via `Map.place_unit`. The accumulator is then consumed by
    one full build-time's worth so excess work carries forward.

    Returns the list of newly-produced units.
    """
    produced: list[Unit] = []
    for city in real_map.cities():
        if city.owner is not player:
            continue
        if city.production.building is None:
            continue
        city.production.tick()
        if not city.production.ready():
            continue
        if not rules.allow_unit_stacking and real_map.units_at(city.coord):
            # Cell occupied; unit waits a turn. Don't consume.
            continue
        kind = city.production.building
        unit_cls = UNIT_REGISTRY[kind]
        new_unit = unit_cls(next_unit_id_fn(), player, city.coord)
        real_map.place_unit(new_unit, city.coord)
        city.production.consume()
        produced.append(new_unit)
    return produced


# -----------------------------------------------------------------------------
# Movement phase: execute one unit's planned path
# -----------------------------------------------------------------------------


class StepOutcome(Enum):
    OK = "ok"
    ILLEGAL = "illegal"
    BLOCKED_BY_FRIENDLY = "blocked_by_friendly"
    OUT_OF_MOVES = "out_of_moves"
    ATTACKER_DIED = "attacker_died"
    CAPTURE_FAILED = "capture_failed"


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    """Result of executing one unit's planned path."""

    last_outcome: StepOutcome
    steps_taken: int
    units_destroyed: tuple[UnitId, ...]
    cities_captured: tuple[CityId, ...]


def execute_unit_path(
    unit: Unit,
    path: tuple[tuple[int, int], ...],
    real_map: Map,
    rules: RuleSet,
    combat_resolver: CombatResolverProtocol,
    rng: random.Random,
) -> MoveOutcome:
    """Apply one unit's planned path step-by-step.

    Each step is validated for terrain legality and stacking. If the
    destination has an enemy unit, the resolver runs combat — winner
    advances, loser is removed. If the destination is an unfriendly
    city, an Army attempts capture per spec §4.5 (50% roll unless
    `army_capture_city_deterministic` is set).
    """
    budget = unit.moves_this_turn()
    steps_taken = 0
    cities_captured: list[CityId] = []
    units_destroyed: list[UnitId] = []
    last_outcome = StepOutcome.OK

    for step in path:
        if steps_taken >= budget:
            last_outcome = StepOutcome.OUT_OF_MOVES
            break
        target = Coord(step[0], step[1])
        if not is_legal_step(unit, target, real_map, rules):
            last_outcome = StepOutcome.ILLEGAL
            break

        occupants = real_map.units_at(target)
        if (
            occupants
            and not rules.allow_unit_stacking
            and any(o.owner is unit.owner for o in occupants)
        ):
            last_outcome = StepOutcome.BLOCKED_BY_FRIENDLY
            break

        # Enemy unit at target: combat.
        defender = next((o for o in occupants if o.owner is not unit.owner), None)
        if defender is not None:
            combat_resolver.resolve(unit, defender, rng)
            if unit.hits <= 0:
                real_map.remove_unit(unit)
                units_destroyed.append(unit.id)
                last_outcome = StepOutcome.ATTACKER_DIED
                return MoveOutcome(
                    last_outcome=last_outcome,
                    steps_taken=steps_taken,
                    units_destroyed=tuple(units_destroyed),
                    cities_captured=tuple(cities_captured),
                )
            real_map.remove_unit(defender)
            units_destroyed.append(defender.id)

        # City handling. If target is a CITY tile owned by someone else
        # (including neutral), attempt capture.
        target_tile = real_map.tile(target)
        if target_tile.city is not None and target_tile.city.owner is not unit.owner:
            if not _try_capture_city(unit, target_tile.city, rules, rng):
                real_map.remove_unit(unit)
                units_destroyed.append(unit.id)
                last_outcome = StepOutcome.CAPTURE_FAILED
                return MoveOutcome(
                    last_outcome=last_outcome,
                    steps_taken=steps_taken,
                    units_destroyed=tuple(units_destroyed),
                    cities_captured=tuple(cities_captured),
                )
            cities_captured.append(target_tile.city.id)

        real_map.move_unit(unit, target)
        steps_taken += 1

    return MoveOutcome(
        last_outcome=last_outcome,
        steps_taken=steps_taken,
        units_destroyed=tuple(units_destroyed),
        cities_captured=tuple(cities_captured),
    )


def _try_capture_city(
    army: Unit,
    city: City,
    rules: RuleSet,
    rng: random.Random,
) -> bool:
    """Attempt to capture `city` for `army`'s owner.

    Per spec §4.5: only Army may capture. Under
    `army_capture_city_deterministic = False`, the capture succeeds on a
    50% roll; under True, always succeeds. On success, ownership transfers
    and existing default orders are cleared (they encoded the previous
    owner's intent).
    """
    if army.kind is not UnitKind.ARMY:
        return False
    if not rules.army_capture_city_deterministic and rng.random() >= 0.5:
        return False
    city.owner = army.owner
    city.default_orders = {}
    return True

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

from empire.core.city import City, OrderKind
from empire.core.coord import Coord, Direction
from empire.core.identity import CityId, UnitId
from empire.core.map import Map
from empire.core.ruleset import RuleSet
from empire.core.standing_order import Heading, PatrolPath, Sentry
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

    for unit in real_map.board_units():
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
    accumulated work reaches the build target, a unit is emitted *on the
    city's cell* and placed via `Map.place_unit`. Stacking on the city cell
    is permitted as a transient state regardless of `allow_unit_stacking`;
    the turn-end disband phase (`disband_overcrowded_city_units`) enforces
    the city's per-category support limits. The accumulator is then consumed
    by one full build-time's worth so excess work carries forward.

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
        kind = city.production.building
        unit_cls = UNIT_REGISTRY[kind]
        new_unit = unit_cls(next_unit_id_fn(), player, city.coord)
        # Apply the ruleset's range knobs to range-limited units (spec §2.1).
        if kind is UnitKind.FIGHTER:
            new_unit.range = rules.fighter_base_range
        elif kind is UnitKind.SATELLITE:
            new_unit.range = rules.satellite_range
        real_map.place_unit(new_unit, city.coord)
        apply_default_order(new_unit, city)
        city.production.consume()
        produced.append(new_unit)
    return produced


def apply_default_order(unit: Unit, city: City) -> None:
    """Translate a city's *explicit* default order for `unit`'s kind into a
    standing order.

    Spec §5.3:
    - No explicit default → no standing order: the freshly-produced unit
      awaits orders (it enters the player's order cycle / the AI commands it).
      Crucially this is NOT auto-SENTRY — produced units must be offered.
    - `SENTRY` → the unit holds in the city.
    - `MOVE_TO(target)` → a `PatrolPath` along a greedy 8-directional line
      toward the target. Core can't depend on the pathfinding layer, so this
      is a straight march that interrupts on obstacles (coast/enemy/block)
      rather than a BFS route — adequate for a default; the player's `g`
      go-to offers true routing. The unit stops on arrival.
    - `ATTACK_NEAREST_ENEMY` → recorded on the city but left for the
      controller/AI layers to act on; no standing order is set here.
    """
    order = city.default_orders.get(unit.kind)
    if order is None:
        return  # unset → unit awaits orders (do not auto-sentry)
    if order.kind is OrderKind.SENTRY:
        unit.standing_order = Sentry()
    elif order.kind is OrderKind.MOVE_TO and order.target is not None:
        cells = _greedy_line(city.coord, order.target)
        if cells:
            unit.standing_order = PatrolPath.new(cells)


def _greedy_line(start: Coord, target: Coord) -> tuple[Coord, ...]:
    """Adjacent-cell chebyshev line from just after `start` through `target`.

    Each step moves one cell toward the target on both axes (diagonal where
    possible), so every cell is a legal one-step move. Returns an empty tuple
    when start == target.
    """
    cells: list[Coord] = []
    cur = start
    while cur != target:
        nx = cur.x + _sign(target.x - cur.x)
        ny = cur.y + _sign(target.y - cur.y)
        cur = Coord(nx, ny)
        cells.append(cur)
    return tuple(cells)


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


# -----------------------------------------------------------------------------
# Movement phase: execute one unit's planned path
# -----------------------------------------------------------------------------


class StepOutcome(Enum):
    OK = "ok"
    ILLEGAL = "illegal"
    FRIENDLY_CITY = "friendly_city"  # an army may not enter a friendly city
    BLOCKED_BY_FRIENDLY = "blocked_by_friendly"
    OUT_OF_MOVES = "out_of_moves"
    ATTACKER_DIED = "attacker_died"
    CAPTURE_FAILED = "capture_failed"
    LOADED = "loaded"  # unit boarded a friendly carrier; its path ends here
    NO_UNLOAD_YET = "no_unload_yet"  # cargo loaded this turn can't unload (spec §3.4)


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

        # Loading (spec §3.4): stepping into a friendly carrier with room is
        # a *load*, not a blocked move — and is legal even onto a water cell
        # the unit could never normally enter. The unit goes aboard and its
        # path ends here.
        carrier = _loadable_carrier_at(unit, target, real_map)
        if carrier is not None:
            real_map.load_cargo(carrier, unit)
            steps_taken += 1
            last_outcome = StepOutcome.LOADED
            if unit.kind is UnitKind.FIGHTER:
                unit.range = rules.fighter_base_range  # refuels aboard a carrier
            break

        if not is_legal_step(unit, target, real_map, rules):
            last_outcome = StepOutcome.ILLEGAL
            break

        # Armies may not enter a friendly city — garrisoning would make cities
        # uncapturable (spec §5.4). Capturing an enemy/neutral city is still a
        # legal move: the city isn't the mover's yet, so this guard misses it.
        if unit.kind is UnitKind.ARMY:
            target_city = real_map.tile(target).city
            if target_city is not None and target_city.owner is unit.owner:
                last_outcome = StepOutcome.FRIENDLY_CITY
                break

        occupants = real_map.units_at(target)
        if (
            occupants
            and not rules.allow_unit_stacking
            and any(
                o.owner is unit.owner and not _is_intangible(o) for o in occupants
            )
        ):
            last_outcome = StepOutcome.BLOCKED_BY_FRIENDLY
            break

        entry, destroyed, captured = _resolve_entry(
            unit, target, real_map, rules, combat_resolver, rng
        )
        units_destroyed.extend(destroyed)
        cities_captured.extend(captured)
        if entry is not StepOutcome.OK:
            return MoveOutcome(
                last_outcome=entry,
                steps_taken=steps_taken,
                units_destroyed=tuple(units_destroyed),
                cities_captured=tuple(cities_captured),
            )

        real_map.move_unit(unit, target)
        steps_taken += 1
        _spend_fighter_fuel(unit, target, real_map, rules)

    return MoveOutcome(
        last_outcome=last_outcome,
        steps_taken=steps_taken,
        units_destroyed=tuple(units_destroyed),
        cities_captured=tuple(cities_captured),
    )


def _resolve_entry(
    unit: Unit,
    target: Coord,
    real_map: Map,
    rules: RuleSet,
    combat_resolver: CombatResolverProtocol,
    rng: random.Random,
) -> tuple[StepOutcome, list[UnitId], list[CityId]]:
    """Resolve combat + city capture for `unit` entering `target`.

    Shared by `execute_unit_path` (on-map step) and `execute_unload`
    (amphibious landing). Does *not* place the unit — the caller does that
    on an `OK` result. On `ATTACKER_DIED`/`CAPTURE_FAILED` the unit has
    already been removed from the game. Assumes terrain legality and the
    absence of a blocking friendly occupant are already checked.
    """
    destroyed: list[UnitId] = []
    captured: list[CityId] = []

    defender = next(
        (
            o
            for o in real_map.units_at(target)
            if o.owner is not unit.owner and not _is_intangible(o)
        ),
        None,
    )
    if defender is not None:
        combat_resolver.resolve(unit, defender, rng)
        if unit.hits <= 0:
            real_map.remove_unit(unit)
            destroyed.append(unit.id)
            return (StepOutcome.ATTACKER_DIED, destroyed, captured)
        real_map.remove_unit(defender)
        destroyed.append(defender.id)

    target_tile = real_map.tile(target)
    if target_tile.city is not None and target_tile.city.owner is not unit.owner:
        if not _try_capture_city(unit, target_tile.city, rules, rng):
            real_map.remove_unit(unit)
            destroyed.append(unit.id)
            return (StepOutcome.CAPTURE_FAILED, destroyed, captured)
        captured.append(target_tile.city.id)

    return (StepOutcome.OK, destroyed, captured)


def _loadable_carrier_at(unit: Unit, target: Coord, real_map: Map) -> Unit | None:
    """The friendly carrier at `target` that `unit` could board, or None."""
    if unit.is_aboard():
        return None
    if not real_map.in_bounds(target) or not real_map.tile(target).on_board:
        return None
    if unit.coord.chebyshev_to(target) > 1:
        return None
    # A ship in dry-dock (on a city cell) can neither load nor unload (spec §5.4).
    if real_map.tile(target).city is not None:
        return None
    for occ in real_map.units_at(target):
        if occ.can_carry(unit):
            return occ
    return None


def _has_adjacent_escort(carrier: Unit, real_map: Map) -> bool:
    """True if a friendly armed warship sits adjacent to `carrier`.

    Gates unloading under `transport_escort_required_for_unload` (spec §10).
    A warship here is any friendly water-capable unit with non-zero strength
    (i.e. not another transport).
    """
    for n in carrier.coord.neighbors():
        if not real_map.in_bounds(n):
            continue
        for occ in real_map.units_at(n):
            cls = type(occ)
            if (
                occ.owner is carrier.owner
                and cls.strength > 0
                and TerrainKind.WATER in cls.legal_terrain
            ):
                return True
    return False


def execute_unload(
    cargo: Unit,
    to: Coord,
    real_map: Map,
    rules: RuleSet,
    combat_resolver: CombatResolverProtocol,
    rng: random.Random,
) -> MoveOutcome:
    """Unload `cargo` from its carrier onto adjacent cell `to` (spec §3.4).

    A unit cannot unload on the same turn it loaded. The landing resolves
    combat / city capture at `to` exactly like a normal step (so an army can
    storm ashore into an enemy or neutral city). On any failure the cargo
    stays aboard, except when it dies in the landing combat.
    """
    carrier = (
        real_map.unit_by_id(cargo.carried_by) if cargo.carried_by is not None else None
    )
    if carrier is None:
        return _unload_fail(StepOutcome.ILLEGAL)
    # A carrier in dry-dock (on a city cell) cannot unload (spec §5.4).
    if real_map.tile(carrier.coord).city is not None:
        return _unload_fail(StepOutcome.ILLEGAL)
    if cargo.loaded_this_turn:
        return _unload_fail(StepOutcome.NO_UNLOAD_YET)
    if cargo.moves_this_turn() < 1:
        return _unload_fail(StepOutcome.OUT_OF_MOVES)
    if (
        not real_map.in_bounds(to)
        or not real_map.tile(to).on_board
        or to == carrier.coord
        or carrier.coord.chebyshev_to(to) > 1
    ):
        return _unload_fail(StepOutcome.ILLEGAL)

    tile = real_map.tile(to)
    assaulting_city = tile.city is not None and tile.city.owner is not cargo.owner
    if not assaulting_city and not can_enter_terrain(cargo, tile.terrain, real_map, to):
        return _unload_fail(StepOutcome.ILLEGAL)

    if rules.transport_escort_required_for_unload and not _has_adjacent_escort(
        carrier, real_map
    ):
        return _unload_fail(StepOutcome.ILLEGAL)

    occupants = real_map.units_at(to)
    if (
        occupants
        and not rules.allow_unit_stacking
        and any(o.owner is cargo.owner and not _is_intangible(o) for o in occupants)
    ):
        return _unload_fail(StepOutcome.BLOCKED_BY_FRIENDLY)

    # Commit: detach to "in transit" (off the board), resolve the landing,
    # then place ashore on success.
    if cargo.id in carrier.cargo:
        carrier.cargo.remove(cargo.id)
    cargo.carried_by = None
    entry, destroyed, captured = _resolve_entry(
        cargo, to, real_map, rules, combat_resolver, rng
    )
    if entry is not StepOutcome.OK:
        return MoveOutcome(
            last_outcome=entry,
            steps_taken=0,
            units_destroyed=tuple(destroyed),
            cities_captured=tuple(captured),
        )
    real_map.unload_cargo(carrier, cargo, to)
    return MoveOutcome(
        last_outcome=StepOutcome.OK,
        steps_taken=1,
        units_destroyed=tuple(destroyed),
        cities_captured=tuple(captured),
    )


def _unload_fail(outcome: StepOutcome) -> MoveOutcome:
    return MoveOutcome(
        last_outcome=outcome,
        steps_taken=0,
        units_destroyed=(),
        cities_captured=(),
    )


def _is_intangible(unit: Unit) -> bool:
    """Satellites can be neither attacked nor blocked (spec §2.4)."""
    return unit.kind is UnitKind.SATELLITE


# -----------------------------------------------------------------------------
# Ground bombardment: surface warships strike adjacent shore/air targets (§4.6)
# -----------------------------------------------------------------------------

# Surface gun platforms. Submarines hunt ships; carriers fight through their
# air wing; transports are unarmed — none of them bombard.
_BOMBARD_KINDS = frozenset(
    {UnitKind.BATTLESHIP, UnitKind.DESTROYER, UnitKind.PATROL}
)
# A ship always reserves its last hit, so it can never bombard itself to death.
MIN_BOMBARD_HP = 2


class BombardmentOutcome(Enum):
    TARGET_DESTROYED = "target_destroyed"  # army/fighter wiped out
    TARGET_SUNK = "target_sunk"  # docked ship lost the ensuing naval duel
    ATTACKER_SUNK = "attacker_sunk"  # the bombarding ship lost that duel
    NO_TARGET = "no_target"  # nothing bombardable in the cell
    OUT_OF_RANGE = "out_of_range"  # not an adjacent on-board cell
    INELIGIBLE = "ineligible"  # wrong ship kind, or under MIN_BOMBARD_HP


@dataclass(frozen=True, slots=True)
class BombardResult:
    outcome: BombardmentOutcome
    target_id: UnitId | None = None
    attacker_destroyed: bool = False


def can_bombard(ship: Unit) -> bool:
    """True if `ship` is a surface warship with the HP to fire (spec §4.6)."""
    return ship.kind in _BOMBARD_KINDS and ship.hits >= MIN_BOMBARD_HP


def execute_bombardment(
    ship: Unit,
    target: Coord,
    real_map: Map,
    rules: RuleSet,
    combat_resolver: CombatResolverProtocol,
    rng: random.Random,
) -> BombardResult:
    """Fire one salvo from `ship` at the adjacent cell `target` (spec §4.6).

    The salvo hits a single enemy occupant, by priority **ship → fighter →
    army**. An army or fighter is destroyed outright (any HP) and the ship
    pays exactly 1 HP. A ship target (only ever a hull docked in the shelled
    city) is instead resolved as ordinary combat — the attacker stays put
    since the strike is ranged. Open-water naval duels are unaffected; a lone
    ship on open water is not a bombardment target.
    """
    del rules  # reserved for future bombardment rule toggles
    if not can_bombard(ship):
        return BombardResult(BombardmentOutcome.INELIGIBLE)
    if not real_map.in_bounds(target) or ship.coord.chebyshev_to(target) != 1:
        return BombardResult(BombardmentOutcome.OUT_OF_RANGE)

    victim = _bombard_target(ship, target, real_map)
    if victim is None:
        return BombardResult(BombardmentOutcome.NO_TARGET)

    if victim.kind in _SEA_KINDS:
        # Naval target: ordinary attrition combat, attacker stays in place.
        combat_resolver.resolve(ship, victim, rng)
        if ship.hits <= 0:
            real_map.remove_unit(ship)
            return BombardResult(
                BombardmentOutcome.ATTACKER_SUNK,
                target_id=victim.id,
                attacker_destroyed=True,
            )
        real_map.remove_unit(victim)
        return BombardResult(BombardmentOutcome.TARGET_SUNK, target_id=victim.id)

    # Army or fighter: wiped out regardless of HP; the salvo costs 1 HP.
    real_map.remove_unit(victim)
    ship.hits -= 1
    return BombardResult(BombardmentOutcome.TARGET_DESTROYED, target_id=victim.id)


def _bombard_target(ship: Unit, target: Coord, real_map: Map) -> Unit | None:
    """The single enemy unit a salvo on `target` strikes, or None.

    Priority: a docked **ship** (only counted when the cell is a city — open
    water naval duels stay move-in combat), then a **fighter**, then an
    **army** (transient, since armies can't garrison). Intangible units
    (satellites) are never targets.
    """
    enemies = [
        u
        for u in real_map.units_at(target)
        if u.owner is not ship.owner and not _is_intangible(u)
    ]
    if not enemies:
        return None
    predicates: list[Callable[[Unit], bool]] = []
    if real_map.tile(target).city is not None:
        predicates.append(lambda u: u.kind in _SEA_KINDS)
    predicates.append(lambda u: u.kind is UnitKind.FIGHTER)
    predicates.append(lambda u: u.kind is UnitKind.ARMY)
    for predicate in predicates:
        for unit in enemies:
            if predicate(unit):
                return unit
    return None


def _spend_fighter_fuel(unit: Unit, at: Coord, real_map: Map, rules: RuleSet) -> None:
    """Burn one fuel point for a Fighter's step, refuelling on a friendly city.

    Out-of-fuel fighters are reaped at end-of-round (`crash_out_of_fuel_
    fighters`), not here, so a fighter that lands on its final fuel point
    survives (spec §3.5).
    """
    if unit.kind is not UnitKind.FIGHTER:
        return
    unit.range = max(0, unit.range - 1)
    tile = real_map.tile(at)
    if tile.city is not None and tile.city.owner is unit.owner:
        unit.range = rules.fighter_base_range


# -----------------------------------------------------------------------------
# End-of-round lifecycle: fighter fuel, satellite orbit/decay, repair (§2-3)
# -----------------------------------------------------------------------------


def crash_out_of_fuel_fighters(real_map: Map, rules: RuleSet) -> tuple[UnitId, ...]:
    """Remove fighters that are out of fuel and not safely landed (spec §3.5).

    A fighter survives at 0 fuel only if it ends the round on a friendly
    city (a friendly carrier would have refuelled it on landing / loading).
    """
    del rules
    crashed: list[UnitId] = []
    for unit in list(real_map.board_units()):
        if unit.kind is not UnitKind.FIGHTER or unit.range > 0:
            continue
        tile = real_map.tile(unit.coord)
        on_friendly_city = tile.city is not None and tile.city.owner is unit.owner
        if not on_friendly_city:
            real_map.remove_unit(unit)
            crashed.append(unit.id)
    return tuple(crashed)


def advance_satellites(real_map: Map) -> tuple[tuple[UnitId, ...], tuple[UnitId, ...]]:
    """Orbit every satellite one cell and decay its lifetime (spec §2.4).

    Each satellite steps one cell along `orbit_direction`, reflecting off
    the map edge (the offending axis flips), then loses one turn of
    lifetime; at zero it deorbits and is removed. Returns
    `(moved_ids, deorbited_ids)`.
    """
    moved: list[UnitId] = []
    deorbited: list[UnitId] = []
    for sat in list(real_map.board_units()):
        if sat.kind is not UnitKind.SATELLITE or sat.orbit_direction is None:
            continue
        new_dir, dest = _orbit_step(sat.coord, sat.orbit_direction, real_map)
        sat.orbit_direction = new_dir
        if dest != sat.coord:
            real_map.move_unit(sat, dest)
            moved.append(sat.id)
        sat.range -= 1
        if sat.range <= 0:
            real_map.remove_unit(sat)
            deorbited.append(sat.id)
    return (tuple(moved), tuple(deorbited))


def _orbit_step(
    coord: Coord, direction: Direction, real_map: Map
) -> tuple[Direction, Coord]:
    """Next (direction, coord) for an orbiting body, bouncing off edges.

    Reflects each axis that would carry the body off the board, so a
    satellite ricochets along the interior rather than leaving the map.
    """
    dx, dy = direction.dx, direction.dy
    if not (0 <= coord.x + dx < real_map.width):
        dx = -dx
    if not (0 <= coord.y + dy < real_map.height):
        dy = -dy
    new_dir = next(
        (d for d in Direction if d.dx == dx and d.dy == dy), direction
    )
    return (new_dir, Coord(coord.x + dx, coord.y + dy))


def repair_in_cities(real_map: Map) -> tuple[UnitId, ...]:
    """Heal +1 HP for each unit that held station in a friendly city (spec §2.3).

    A unit repairs only if it did not move this round (it must begin *and*
    end the turn in the city). Also clears the per-round movement flag.
    """
    repaired: list[UnitId] = []
    for unit in real_map.board_units():
        moved = unit._moved_this_round  # pyright: ignore[reportPrivateUsage]
        unit._moved_this_round = False  # pyright: ignore[reportPrivateUsage]
        if moved or unit.hits >= type(unit).max_hits:
            continue
        tile = real_map.tile(unit.coord)
        if tile.city is not None and tile.city.owner is unit.owner:
            unit.hits += 1
            repaired.append(unit.id)
    return tuple(repaired)


# -----------------------------------------------------------------------------
# City support limits: garrison / airbase / dry-dock (spec §5.4)
# -----------------------------------------------------------------------------

# Sea kinds share the city's single dry-dock slot.
_SEA_KINDS = frozenset(
    {
        UnitKind.PATROL,
        UnitKind.DESTROYER,
        UnitKind.SUBMARINE,
        UnitKind.TRANSPORT,
        UnitKind.CARRIER,
        UnitKind.BATTLESHIP,
    }
)

# How many friendly units of each support category may rest on a city cell.
# Army 0: armies may never garrison a friendly city (it would make the city
# uncapturable). Fighter 8: airbase, same as a Carrier. Sea 1: dry-dock holds
# one ship (which also repairs via `repair_in_cities`). Satellites are exempt
# (they orbit; see `_city_support_category`).
_CITY_SUPPORT_LIMITS: dict[str, int] = {"land": 0, "air": 8, "sea": 1}


def _city_support_category(kind: UnitKind) -> str | None:
    """The city-support category for `kind`, or None if exempt.

    Satellites are exempt: they orbit rather than dock, and leave a city on
    their own via `advance_satellites`.
    """
    if kind is UnitKind.ARMY:
        return "land"
    if kind is UnitKind.FIGHTER:
        return "air"
    if kind in _SEA_KINDS:
        return "sea"
    return None  # SATELLITE


def disband_overcrowded_city_units(player: Player, real_map: Map) -> tuple[UnitId, ...]:
    """Disband `player`'s units that exceed a city's per-category support limit.

    Run at each player's turn-end. For every category over its limit on a
    friendly city cell the excess is removed; loaded ships keep their berth
    and, among equals, the oldest (lowest id) units keep their slots — so the
    unit scrapped is the empty, freshly-produced one. Armies have a limit of
    0, so any army resting on a friendly city is disbanded — a produced army
    that never marched out, or one that just conquered the city and is now its
    abstract defence.

    A disband must never drown cargo: `Map.remove_unit` sinks a carrier's hold
    (that is the combat-loss path, spec §4.5), so we deliberately scrap empty
    ships before loaded ones. Under standard (no-stacking) rules the only
    over-limit ship is the empty produced hull, so a loaded ship is never the
    one removed here.

    Returns the disbanded unit ids, ascending.
    """
    disbanded: list[UnitId] = []
    for city in real_map.cities():
        if city.owner is not player:
            continue
        by_category: dict[str, list[Unit]] = {}
        for unit in real_map.units_at(city.coord):
            if unit.owner is not player:
                continue
            category = _city_support_category(unit.kind)
            if category is None:
                continue
            by_category.setdefault(category, []).append(unit)
        for category, units in by_category.items():
            limit = _CITY_SUPPORT_LIMITS[category]
            if len(units) <= limit:
                continue
            # Keep loaded ships, then oldest; scrap empty/newest first so an
            # administrative disband never sinks a hold full of cargo.
            units.sort(key=lambda u: (not u.cargo, int(u.id)))
            for unit in units[limit:]:
                real_map.remove_unit(unit)
                disbanded.append(unit.id)
    disbanded.sort(key=int)
    return tuple(disbanded)


# -----------------------------------------------------------------------------
# Standing orders: per-turn application of persistent unit orders
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StandingOrderResult:
    """Per-unit outcome of one turn's standing-orders step."""

    moved_unit_ids: tuple[UnitId, ...]
    interrupted_unit_ids: tuple[UnitId, ...]
    woken_sentry_ids: tuple[UnitId, ...]


def enemy_in_scan_range(unit: Unit, real_map: Map) -> bool:
    """True if any enemy unit sits within `unit`'s Chebyshev scan disc.

    Used as the wake trigger for sentried units and as an interruption
    trigger for autonomous Heading/PatrolPath moves.
    """
    radius = type(unit).scan_range
    for other in real_map.board_units():
        if other.owner is unit.owner:
            continue
        if unit.coord.chebyshev_to(other.coord) <= radius:
            return True
    return False


def apply_standing_orders(
    player: Player,
    real_map: Map,
    rules: RuleSet,
    combat_resolver: CombatResolverProtocol,
    rng: random.Random,
) -> StandingOrderResult:
    """Apply one step of each owned unit's standing order.

    Runs at the top of `player`'s turn, before the controller is consulted.

    For each owned unit:
    - `None`: nothing to do.
    - `Sentry()`: nothing this step. Wake check happens separately (see
      `wake_sentried_units`).
    - `Heading(d)`: step one cell in `d`. Cleared on interruption
      (out-of-bounds, illegal terrain, friendly-occupied target, or enemy
      visible in scan range *after* the step). Otherwise the heading
      persists.
    - `PatrolPath`: step into the next cell of the path; replace the
      order with `after_step()`. Same interruption rules.

    Returns the IDs of units that moved successfully, units whose orders
    were interrupted, and units that were sentried this turn.
    """
    moved: list[UnitId] = []
    interrupted: list[UnitId] = []
    sentried: list[UnitId] = []

    # Snapshot the list so removals during iteration don't trip us up.
    own_units = [u for u in real_map.board_units() if u.owner is player]

    for unit in own_units:
        order = unit.standing_order
        if order is None:
            continue
        if isinstance(order, Sentry):
            sentried.append(unit.id)
            continue

        if isinstance(order, Heading):
            target = unit.coord.step(order.direction)
            next_order_on_success = order  # heading persists
        else:
            # PatrolPath — the only remaining variant after Sentry/Heading.
            nxt = order.next_cell()
            if nxt is None:
                unit.standing_order = None
                interrupted.append(unit.id)
                continue
            target = nxt
            next_order_on_success = order.after_step()

        if not is_legal_step(unit, target, real_map, rules):
            unit.standing_order = None
            interrupted.append(unit.id)
            continue

        # Friendly-blocking interrupt: don't fight your way through your own.
        occupants = real_map.units_at(target)
        if (
            occupants
            and not rules.allow_unit_stacking
            and any(o.owner is player for o in occupants)
        ):
            unit.standing_order = None
            interrupted.append(unit.id)
            continue

        outcome = execute_unit_path(
            unit=unit,
            path=((target.x, target.y),),
            real_map=real_map,
            rules=rules,
            combat_resolver=combat_resolver,
            rng=rng,
        )

        unit_alive = real_map.unit_by_id(unit.id) is not None
        if not unit_alive or outcome.last_outcome is not StepOutcome.OK:
            # Combat killed us, capture failed, or step otherwise failed.
            if unit_alive:
                unit.standing_order = None
            interrupted.append(unit.id)
            continue

        # Step succeeded. Check post-step interruption: enemy now in scan.
        if enemy_in_scan_range(unit, real_map):
            unit.standing_order = None
            interrupted.append(unit.id)
            moved.append(unit.id)
            continue

        unit.standing_order = next_order_on_success
        if unit.standing_order is None:
            # PatrolPath naturally exhausted (one-shot finished).
            interrupted.append(unit.id)
        moved.append(unit.id)

    return StandingOrderResult(
        moved_unit_ids=tuple(moved),
        interrupted_unit_ids=tuple(interrupted),
        woken_sentry_ids=(),
    )


def wake_sentried_units(player: Player, real_map: Map) -> tuple[UnitId, ...]:
    """Wake any sentried own unit with an enemy currently in scan range.

    Called at the top of `player`'s turn, before `apply_standing_orders`.
    Returns the IDs of units that were woken (had their Sentry cleared).

    Per spec, a surprise NEVER auto-sentries — it auto-WAKES. The
    "adjacent combat" wake trigger is subsumed here because an enemy
    fighting next to a unit is, by definition, within scan range.
    """
    woken: list[UnitId] = []
    for unit in real_map.board_units():
        if unit.owner is not player:
            continue
        if not isinstance(unit.standing_order, Sentry):
            continue
        if enemy_in_scan_range(unit, real_map):
            unit.standing_order = None
            woken.append(unit.id)
    return tuple(woken)


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

"""Naval behaviors for `PlanFollower` (Phase 15.9).

Two jobs, both stateless (re-derived from the board each turn, like the land
follower):

- **Sea scouting** — route idle ships toward the nearest sea frontier so the
  AI discovers other landmasses (the value-of-information work the search
  can't do; see the phase plan).
- **Amphibious operations** — for each `Role.INVADE` objective, orchestrate a
  transport + cargo armies: loiter at the embark port while armies board,
  then sail to the target's coast and storm ashore (an `UnloadOrder`).

The phase of an operation is read off the board each turn (transport cargo
count + position), so no cross-turn state is needed. `plan_naval` claims the
armies it uses as cargo so the land follower leaves them alone.
"""

from __future__ import annotations

from empire.ai.search.plan import Plan, Role
from empire.ai.vision import sea_frontier_cells, terrain_for_view
from empire.contracts.turn_plan import UnitMove, UnloadOrder
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.tile import TerrainKind
from empire.core.unit import Unit, UnitKind
from empire.pathfinding.cost import ARMY as ARMY_COST
from empire.pathfinding.cost import SEA as SEA_COST
from empire.pathfinding.distance_field import DistanceField, PassabilityGrid

# Sea unit kinds (everything that lives on water).
SEA_KINDS = frozenset(
    {
        UnitKind.PATROL,
        UnitKind.DESTROYER,
        UnitKind.SUBMARINE,
        UnitKind.TRANSPORT,
        UnitKind.CARRIER,
        UnitKind.BATTLESHIP,
    }
)
_SEA_KINDS = SEA_KINDS  # internal alias


class NavalResult:
    """What `plan_naval` decided this turn."""

    def __init__(self) -> None:
        self.moves: dict[UnitId, UnitMove] = {}
        self.unloads: list[UnloadOrder] = []
        self.claimed_armies: set[UnitId] = set()


def plan_naval(view: WorldView, plan: Plan) -> NavalResult:
    """Decide moves/unloads for own ships and invasion-cargo armies.

    The follower runs its normal land logic on every army NOT in
    `result.claimed_armies`."""
    result = NavalResult()
    own = list(view.own_units)
    ships = [u for u in own if u.kind in _SEA_KINDS and u.carried_by is None]
    if not ships:
        return result  # nothing naval to do (and no invasion possible)

    land_grid = PassabilityGrid(view.real_map(), ARMY_COST, view.own_player.view)
    sea_grid = PassabilityGrid(view.real_map(), SEA_COST, view.own_player.view)
    armies = [
        u for u in own if u.kind is UnitKind.ARMY and u.carried_by is None
    ]

    used_ships: set[UnitId] = set()
    invade_targets = [
        o.target for o in plan.objectives if o.role is Role.INVADE
    ]
    for target in invade_targets:
        transport = _pick_transport(ships, used_ships, target)
        if transport is None:
            continue  # no free transport; production builds one
        used_ships.add(transport.id)
        _run_operation(
            view, target, transport, armies, land_grid, sea_grid, result
        )

    # Every other ship scouts the sea frontier.
    frontier = sea_frontier_cells(view)
    for ship in ships:
        if ship.id in used_ships:
            continue
        move = _sea_scout(ship, sea_grid, frontier)
        if move is not None:
            result.moves[ship.id] = move
    return result


# ---- amphibious operation ----------------------------------------------------


def _run_operation(
    view: WorldView,
    target: Coord,
    transport: Unit,
    armies: list[Unit],
    land_grid: PassabilityGrid,
    sea_grid: PassabilityGrid,
    result: NavalResult,
) -> None:
    """Drive one transport's invasion of `target` this turn."""
    want = min(transport.effective_capacity(), _OP_FORCE)
    aboard = len(transport.cargo)

    # Armies that could still reach the transport to board (free,
    # land-reachable). Exclude any army already on the target's landmass — a
    # transport loitering off the destination coast floods one step inland and
    # would otherwise re-claim the very troops it just landed, pulling them
    # back aboard instead of letting them assault.
    target_land = DistanceField(target, land_grid)
    field_to_transport = DistanceField(transport.coord, land_grid)
    boarders = sorted(
        (
            (steps, int(a.id), a)
            for a in armies
            if a.id not in result.claimed_armies
            and target_land.steps_to(a.coord) is None
            and (steps := field_to_transport.steps_to(a.coord)) is not None
        ),
    )

    # Sail once we have the force we want, OR once we have *someone* aboard
    # and no further reinforcement can reach the boat (don't wait forever for
    # a third army that doesn't exist).
    loaded_enough = aboard >= want or (aboard >= 1 and not boarders)
    if loaded_enough:
        # Storm ashore onto a beachhead — empty land cells *beside* the city,
        # one per cargo (stacking-off blocks two on one cell). Unloading onto
        # the city itself would feed armies to its capture roll one at a time,
        # each consumed (spec §5.4): a poor assault. From the beach the land
        # follower's massed-assault doctrine takes the city next turn.
        landings = _landing_cells(view, transport, target, land_grid)
        if landings:
            for cargo_id, cell in zip(list(transport.cargo), landings):
                result.unloads.append(
                    UnloadOrder(cargo_id=cargo_id, to=(cell.x, cell.y))
                )
            return
        # Sail to the target's coast. The target lies across fogged ocean the
        # fog-masked grid can't route through, so the beachhead geometry and a
        # fallback heading come from the true-geography grid — the same
        # "gauntlet beats abandonment" pattern the land follower uses. The
        # engine moves the transport through real water cells, peeling back the
        # fog as it goes.
        raw_sea = PassabilityGrid(view.real_map(), SEA_COST)
        # Route around our own ships: stacking-off means a recon patrol idling
        # in a strait would block the transport indefinitely (its grid is
        # terrain-only and can't see the occupant).
        others = frozenset(
            u.coord
            for u in view.own_units
            if u.kind in _SEA_KINDS
            and u.carried_by is None
            and u.id != transport.id
        )
        nav = sea_grid.with_blocked(others) if others else sea_grid
        raw_nav = raw_sea.with_blocked(others) if others else raw_sea
        beach = _beachhead(view, target, transport, nav)
        if beach is None:
            beach = _beachhead(view, target, transport, raw_nav)
        if beach is not None:
            move = _step_toward(
                transport, beach, DistanceField(transport.coord, nav)
            )
            if move is None:
                move = _step_toward(
                    transport, beach, DistanceField(transport.coord, raw_nav)
                )
            if move is not None:
                result.moves[transport.id] = move
        return

    # LOADING. A transport in dry-dock (on its city cell) can neither be
    # boarded nor unload (spec §5.4), so first float it to an adjacent sea
    # cell beside land — only then can armies step aboard. Once afloat it
    # holds station while the nearest free armies march in.
    if _in_drydock(view, transport):
        spot = _loiter_spot(view, transport, sea_grid, land_grid)
        if spot is not None:
            result.moves[transport.id] = UnitMove(
                unit_id=transport.id, path=((spot.x, spot.y),)
            )
    for _, _, army in boarders[: want - aboard]:
        result.claimed_armies.add(army.id)
        move = _board_move(army, transport, land_grid)
        if move is not None:
            result.moves[army.id] = move


def _in_drydock(view: WorldView, transport: Unit) -> bool:
    """A transport sitting on a friendly city cell is in dry-dock."""
    return any(c.coord == transport.coord for c in view.own_cities)


def _loiter_spot(
    view: WorldView,
    transport: Unit,
    sea_grid: PassabilityGrid,
    land_grid: PassabilityGrid,
) -> Coord | None:
    """An adjacent sea cell to float a dry-docked transport to, preferring the
    one bordering the most boardable land (so armies have a cell to embark
    from). Ties break on (y, x)."""
    best: tuple[tuple[int, int, int], Coord] | None = None
    for n in transport.coord.neighbors():
        if not view.in_bounds(n) or not sea_grid.is_passable(n):
            continue
        land_access = sum(
            1
            for m in n.neighbors()
            if view.in_bounds(m)
            and m != transport.coord
            and land_grid.is_passable(m)
        )
        key = (-land_access, n.y, n.x)
        if best is None or key < best[0]:
            best = (key, n)
    return best[1] if best is not None else None


def _board_move(
    army: Unit, transport: Unit, land_grid: PassabilityGrid
) -> UnitMove | None:
    """Move `army` to board `transport`. Adjacent → step directly onto it
    (the engine loads instead of treating water as illegal). Otherwise march
    toward the carrier (if it's in port, on land/city) or to the nearest land
    cell beside it (if it's afloat — the army can't path onto water)."""
    if army.coord.chebyshev_to(transport.coord) == 1:
        return UnitMove(
            unit_id=army.id, path=((transport.coord.x, transport.coord.y),)
        )
    field = DistanceField(army.coord, land_grid)
    if land_grid.is_passable(transport.coord):  # transport in port
        return _step_toward(army, transport.coord, field)
    # Afloat: head for the nearest reachable land cell adjacent to it.
    best: tuple[int, int, int, Coord] | None = None
    for n in transport.coord.neighbors():
        if not land_grid.is_passable(n):
            continue
        steps = field.steps_to(n)
        if steps is None:
            continue
        key = (steps, n.y, n.x, n)
        if best is None or key < best:
            best = key
    if best is None:
        return None
    return _step_toward(army, best[3], field)


# How many armies a single amphibious operation tries to carry. Filling the
# transport (capacity 6) matters for holding: capture consumes the storming
# army (§4.5), so a 3-army wave that loses one or two crossing the beach takes
# the city with its last soldier and leaves it empty — recaptured next turn
# (the t146->t147 loss the projection probe found). A full wave lands enough
# survivors to both capture and garrison. `want` is still min()'d with the
# transport's damage-scaled capacity, so this just stops under-filling.
_OP_FORCE = 6


def _pick_transport(
    ships: list[Unit], used: set[UnitId], target: Coord
) -> Unit | None:
    """A free transport for an operation: prefer one already carrying cargo
    (finish what it started), else the nearest empty one to the target."""
    transports = [
        s for s in ships if s.kind is UnitKind.TRANSPORT and s.id not in used
    ]
    if not transports:
        return None
    loaded = [t for t in transports if t.cargo]
    pool = loaded if loaded else transports
    return min(pool, key=lambda t: (t.coord.chebyshev_to(target), int(t.id)))


def _landing_cells(
    view: WorldView,
    transport: Unit,
    target: Coord,
    land_grid: PassabilityGrid,
) -> list[Coord]:
    """Army-passable land cells adjacent to the transport — the beachhead an
    amphibious assault disembarks onto, nearest the target city first. One
    cell per cargo army (stacking-off lands one per cell). Empty while the
    transport is still at sea; non-empty once it reaches the target's coast
    (`_beachhead` sails it there), so the troops land on the target's own
    landmass and the assault doctrine marches them to the city."""
    out: list[Coord] = []
    for n in transport.coord.neighbors():
        if not view.in_bounds(n) or not land_grid.is_passable(n):
            continue
        if n == target:
            continue  # storm the city from the beach, don't unload onto it
        # Only land at the *target's* coast — a loaded transport loitering off
        # its home shore also has land neighbours; without this it would
        # disembark right back home and never sail.
        if n.chebyshev_to(target) > 2:
            continue
        out.append(n)
    out.sort(key=lambda c: (c.chebyshev_to(target), c.y, c.x))
    return out


def _beachhead(
    view: WorldView, target: Coord, transport: Unit, sea_grid: PassabilityGrid
) -> Coord | None:
    """The sea cell adjacent to `target` that the transport can reach
    soonest — the spot to sail to before disembarking."""
    field = DistanceField(transport.coord, sea_grid)
    best: tuple[int, int, int, Coord] | None = None
    for n in target.neighbors():
        if not view.in_bounds(n):
            continue
        if not sea_grid.is_passable(n):
            continue
        steps = field.steps_to(n)
        if steps is None:
            continue
        key = (steps, n.y, n.x, n)
        if best is None or key < best:
            best = key
    return best[3] if best is not None else None


def _sea_scout(
    ship: Unit, sea_grid: PassabilityGrid, frontier: frozenset[Coord]
) -> UnitMove | None:
    """Step the ship toward the nearest reachable sea-frontier cell."""
    field = DistanceField(ship.coord, sea_grid)
    best: tuple[int, int, int] | None = None
    best_cell: Coord | None = None
    for cell in frontier:
        steps = field.steps_to(cell)
        if steps is None or steps == 0:
            continue
        key = (steps, cell.y, cell.x)
        if best is None or key < best:
            best, best_cell = key, cell
    if best_cell is None:
        return None
    return _step_toward(ship, best_cell, field)


def _step_toward(
    unit: Unit, target: Coord, field: DistanceField
) -> UnitMove | None:
    """Up to `moves_this_turn` cells along the field toward `target`."""
    cells = field.path_to(target)
    if cells is None or len(cells) < 2:
        return None
    budget = unit.moves_this_turn()
    steps = cells[1 : 1 + budget]
    if not steps:
        return None
    return UnitMove(unit_id=unit.id, path=tuple((c.x, c.y) for c in steps))


def is_ocean_coastal(view: WorldView, city_coord: Coord) -> bool:
    """True if a city cell neighbours seen water — i.e. an amphibious assault
    can land beside it. Used by the generator to pick invasion targets."""
    for n in city_coord.neighbors():
        if view.in_bounds(n) and terrain_for_view(view, n) is TerrainKind.WATER:
            return True
    return False

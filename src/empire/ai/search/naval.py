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
    ships = [u for u in own if u.kind in SEA_KINDS and u.carried_by is None]
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
    if invade_targets:
        # All transports run the same operation against the primary target, but
        # each sails AUTONOMOUSLY once IT carries a near-full wave (`_run_operation`
        # commits at >= _OP_FORCE - _SEAT_SLACK, a 5-6 army wave — no thin
        # trickle). We deliberately do NOT hold the whole fleet until every hull
        # assembles: fleet-wide staging deadlocked with multiple hulls + ongoing
        # army production (there was always one underfilled hull with an army
        # "imminent" that could never actually board, so the fleet never
        # committed and full hulls idled near home — measured 38% -> 75% landed
        # when dropped). Same-coast hulls still fill at the same time (implicit
        # concentration), and the return-and-reload recycling turns the cadence
        # into sustained reinforcement waves.
        target = invade_targets[0]
        fleet = [s for s in ships if s.kind is UnitKind.TRANSPORT]
        for transport in fleet:
            used_ships.add(transport.id)
            _run_operation(
                view, target, transport, armies, land_grid, sea_grid, result,
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


def _wave_ready(
    transport: Unit,
    target: Coord,
    armies: list[Unit],
    land_grid: PassabilityGrid,
    claimed: set[UnitId],
) -> bool:
    """Should THIS transport sail its wave now? Two commit rules, each curing a
    staging deadlock that stranded loaded hulls near home:
      - NEAR-FULL: it carries within `_SEAT_SLACK` of a full wave. Insisting on
        the LAST seat deadlocked a 5/6 hull forever when the boarding cells were
        too crowded to fill it (armies stay "imminent" yet can never board) — a
        5-6 army wave is force enough; stragglers ride the next recycled wave.
      - NO IMMINENT: it has >=1 aboard and no free army is *imminent* — within
        `_BOARD_PATIENCE` land-steps of the hull (excluding troops already on the
        target landmass). An unbounded "any reachable army" test never committed
        while the home continent kept producing armies. We wait only for armies
        about to arrive; distant ones ride the next recycled wave."""
    want = min(transport.effective_capacity(), _OP_FORCE)
    aboard = len(transport.cargo)
    if aboard >= want - _SEAT_SLACK:
        return True
    if aboard < 1:
        return False
    target_land = DistanceField(target, land_grid)
    reach = DistanceField(transport.coord, land_grid)
    imminent = any(
        a.id not in claimed
        and target_land.steps_to(a.coord) is None
        and (s := reach.steps_to(a.coord)) is not None
        and s <= _BOARD_PATIENCE
        for a in armies
    )
    return not imminent


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
    """Drive one transport's invasion of `target` this turn — load, sail and
    storm ashore, or return-and-reload if emptied. The hull sails autonomously
    once it carries a near-full wave (`_wave_ready`); there is no fleet-wide
    staging brake (it deadlocked — see `plan_naval`)."""
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

    if _wave_ready(transport, target, armies, land_grid, result.claimed_armies):
        # Storm ashore onto the chosen landing zone — army-passable cells on the
        # target's landmass beside the transport, one per cargo (stacking-off
        # lands one per cell). Landing *away* from the city (not against its
        # wall) lets the troops pool into a mass before the land follower's
        # massed-assault doctrine marches them on the city; landing right beside
        # it fed them to the defenders piecemeal and never converted (§10.1.1).
        landings = _landing_cells(view, transport, target, land_grid, target_land)
        if landings:
            for cargo_id, cell in zip(list(transport.cargo), landings):
                result.unloads.append(
                    UnloadOrder(cargo_id=cargo_id, to=(cell.x, cell.y))
                )
            return
        # Sail to the chosen landing zone's sea cell. The target lies across
        # fogged ocean the fog-masked grid can't route through, so geometry and
        # a fallback heading come from the true-geography grid — the same
        # "gauntlet beats abandonment" pattern the land follower uses. The
        # engine moves the transport through real water cells, peeling back the
        # fog as it goes.
        nav, raw_nav = _nav_grids(view, transport, sea_grid)
        beach = _landing_zone(view, target, transport, nav, land_grid, target_land)
        if beach is None:
            beach = _landing_zone(
                view, target, transport, raw_nav, land_grid, target_land
            )
        if beach is not None:
            move = _sail(view, transport, beach, sea_grid)
            if move is not None:
                result.moves[transport.id] = move
        return

    # RETURN-AND-RELOAD (the recycling that turns one doomed wave into sustained
    # reinforcement). An empty hull with nothing to board *here* has delivered
    # its wave and drifted off the target coast — its reachable land is the enemy
    # continent, so the home army surplus is unreachable and it would otherwise
    # sit forever (the seed-16 deadlock). Sail it back to a home embark coast
    # where free armies wait, then it re-enters LOADING next turn. Cheaper than
    # building a fresh transport: reuse the hull we already paid for.
    if aboard == 0 and not boarders and not _in_drydock(view, transport):
        dest = _reload_destination(view, transport, armies, land_grid, target_land)
        if dest is not None:
            move = _sail(view, transport, dest, sea_grid)
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


def _nav_grids(
    view: WorldView, transport: Unit, sea_grid: PassabilityGrid
) -> tuple[PassabilityGrid, PassabilityGrid]:
    """The (fog-masked, true-geography) sea grids for routing this transport,
    both with our other ships blocked out. Stacking-off means an idle patrol in
    a strait would otherwise block the transport indefinitely (the grid is
    terrain-only and can't see the occupant); the raw grid braves fogged ocean
    the masked one can't route through ("gauntlet beats abandonment")."""
    others = frozenset(
        u.coord
        for u in view.own_units
        if u.kind in SEA_KINDS and u.carried_by is None and u.id != transport.id
    )
    raw_sea = PassabilityGrid(view.real_map(), SEA_COST)
    nav = sea_grid.with_blocked(others) if others else sea_grid
    raw_nav = raw_sea.with_blocked(others) if others else raw_sea
    return nav, raw_nav


def _sail(
    view: WorldView, transport: Unit, dest_sea: Coord, sea_grid: PassabilityGrid
) -> UnitMove | None:
    """Step the transport toward `dest_sea`, fog-masked field first then the
    true-geography fallback (so a fogged crossing is braved, not abandoned)."""
    nav, raw_nav = _nav_grids(view, transport, sea_grid)
    move = _step_toward(transport, dest_sea, DistanceField(transport.coord, nav))
    if move is None:
        move = _step_toward(
            transport, dest_sea, DistanceField(transport.coord, raw_nav)
        )
    return move


def _reload_destination(
    view: WorldView,
    transport: Unit,
    armies: list[Unit],
    land_grid: PassabilityGrid,
    target_land: DistanceField,
) -> Coord | None:
    """A sea cell to sail an emptied hull back to so it can reload: open water
    beside an own city whose landmass holds free armies (not ones already landed
    overseas — those are the assault, not the next wave). Nearest to the
    transport by straight-line distance; ties break on (y, x). None when no such
    embark coast exists (nothing left to ferry)."""
    best: tuple[int, int, int] | None = None
    best_sea: Coord | None = None
    for city in view.own_cities:
        # Only coastal cities can be an embark point; skip inland ones before
        # paying for a land flood (an inland city has no water neighbour, so it
        # could never contribute a sea cell anyway).
        sea_cells = [
            n
            for n in city.coord.neighbors()
            if view.in_bounds(n)
            and view.real_map().terrain_at(n) is TerrainKind.WATER
        ]
        if not sea_cells:
            continue
        city_land = DistanceField(city.coord, land_grid)
        has_waiting = any(
            target_land.steps_to(a.coord) is None
            and city_land.steps_to(a.coord) is not None
            for a in armies
        )
        if not has_waiting:
            continue
        for n in sea_cells:
            key = (transport.coord.chebyshev_to(n), n.y, n.x)
            if best is None or key < best:
                best, best_sea = key, n
    return best_sea


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

# How close (Chebyshev, land steps) a free army must be to a not-yet-full hull
# to count as "still boarding" and justify the fleet waiting another turn.
# Armies march ~1 cell/turn, so this is roughly "arrives within N turns." Small
# enough that the fleet commits and sails its imminent force instead of idling
# for the whole continent's production to funnel in (the staging-commit deadlock
# that stranded loaded fleets at home — the loaded->landed wall); large enough
# that a coast-massed wave still fills before sailing. Stragglers ride the next
# recycled wave. Tunable via the amphib probe.
_BOARD_PATIENCE = 3

# How many seats short of full still counts as a committable wave. Armies massed
# at the embark coast can be near a hull yet unable to actually board (boarding
# cells crowded, or the hull floated a cell too far), so insisting on the LAST
# seat deadlocks a 5/6 hull a few cells from the target forever. One seat of
# slack lets a near-full wave commit; the recycled return-and-reload brings the
# rest. Tunable via the amphib probe.
_SEAT_SLACK = 1

# Landing-zone selection (§10.1.2). `_LAND_MARCH`: how far from the target a
# landing cell may be (the fist marches the rest on foot) — keeps the beachhead
# on the target's doorstep without forcing it against the wall. `_SCREEN_R`:
# Chebyshev radius for counting the enemy defender screen near a landing spot.
_LAND_MARCH = 6
_SCREEN_R = 3


def _landing_cells(
    view: WorldView,
    transport: Unit,
    target: Coord,
    land_grid: PassabilityGrid,
    target_land: DistanceField,
) -> list[Coord]:
    """Army-passable land cells adjacent to the transport that lie on the
    *target's* landmass — the beachhead the assault disembarks onto, one cell
    per cargo army (stacking-off lands one per cell). Empty while the transport
    is mid-ocean or sitting off its home shore (those neighbours aren't on the
    target landmass), non-empty once it reaches the chosen landing zone, so it
    never disembarks on the wrong coast. `target_land` is the land flood from
    the target; membership = a finite distance in it."""
    out: list[Coord] = []
    for n in transport.coord.neighbors():
        if not view.in_bounds(n) or not land_grid.is_passable(n):
            continue
        if n == target:
            continue  # storm the city from the beach, don't unload onto it
        if target_land.steps_to(n) is None:
            continue  # not the target's landmass (home shore, an islet, etc.)
        out.append(n)
    # Nearest the target first, so cargo lands on the cells closest to the city.
    out.sort(key=lambda c: ((target_land.steps_to(c) or 0), c.y, c.x))
    return out


def _landing_zone(
    view: WorldView,
    target: Coord,
    transport: Unit,
    sea_grid: PassabilityGrid,
    land_grid: PassabilityGrid,
    target_land: DistanceField,
) -> Coord | None:
    """The sea cell to sail to: adjacent to the best landing spot on the
    target's landmass, not merely the cell nearest the city (§10.1.2).

    Each candidate sea cell is the launch point for disembarking onto its
    army-passable landmass neighbours. Scored, best first, by:
      1. out of enemy-city artillery range  (don't land under the guns)
      2. fewest enemy armies near the landing cells  (avoid the defender screen)
      3. widest  (most landmass neighbours → several land per turn AND room to
         pool into a mass instead of trickling in)
      4. closest to the target  (shorter fist march), then soonest reachable.
    Returns None if no reachable landmass coast is in range."""
    sea_field = DistanceField(transport.coord, sea_grid)
    art = view.rules.city_artillery_range
    gun_cities: list[Coord] = []
    if art > 0:
        gun_cities = [c.coord for c in view.known_enemy_cities]
        gun_cities += [c.coord for c in view.neutral_cities]
    enemy = [k.snapshot.coord for k in view.known_enemy_units]

    best_key: tuple | None = None
    best_sea: Coord | None = None
    d = _LAND_MARCH + 1
    for dy in range(-d, d + 1):
        for dx in range(-d, d + 1):
            s = Coord(target.x + dx, target.y + dy)
            if not view.in_bounds(s) or not sea_grid.is_passable(s):
                continue
            # The launch cell must be open water. The SEA cost profile counts
            # city tiles as passable (ships dock in port), so without this the
            # enemy city itself scores as the best "sea cell" — and the
            # transport sails onto it, fails the capture (only armies capture)
            # and is destroyed with all its cargo.
            if view.real_map().terrain_at(s) is not TerrainKind.WATER:
                continue
            sea_steps = sea_field.steps_to(s)
            if sea_steps is None:
                continue
            land_neighbors = [
                n for n in s.neighbors()
                if n != target
                and view.in_bounds(n)
                and land_grid.is_passable(n)
                and (md := target_land.steps_to(n)) is not None
                and md <= _LAND_MARCH
            ]
            if not land_neighbors:
                continue
            in_gun = any(
                ln.chebyshev_to(g) <= art
                for ln in land_neighbors
                for g in gun_cities
            )
            screen = sum(
                1
                for e in enemy
                if min(ln.chebyshev_to(e) for ln in land_neighbors) <= _SCREEN_R
            )
            width = len(land_neighbors)
            march = min(target_land.steps_to(n) or 0 for n in land_neighbors)
            # Reachability is second only to gun-avoidance: each transport picks
            # the nearest acceptable beach to ITSELF, so a fleet spreads across
            # distinct landing cells instead of all converging on one globally-
            # "best" cell — which made them block each other (stacking-off) and
            # never complete the approach, so a loaded fleet hovered offshore
            # forever without landing. Screen/width/march break ties after that.
            key = (in_gun, sea_steps, screen, -width, march, s.y, s.x)
            if best_key is None or key < best_key:
                best_key, best_sea = key, s
    return best_sea


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

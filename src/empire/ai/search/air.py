"""Air behaviors for `PlanFollower`.

Fighters are fast (speed 8), far-seeing (scan 5) and fly over anything (land,
water, city), which makes them the AI's best scouts — and, opportunistically,
strikers that can leap an artillery gauntlet (out-of-range → adjacent in one
move). The catch is fuel: a fighter burns one range point per step and *crashes*
if it empties away from a friendly city/carrier (engine
`crash_out_of_fuel_fighters`), refuelling only on a friendly city or carrier. So
every order keeps a return-to-base margin — a fighter never flies somewhere it
can't get home from.

Stateless, like the land/naval followers: re-derived from the board each turn.
Carrier-borne projection (fighters refuelling at sea to reach another landmass)
is deferred; this is home/base-range recon + opportunistic strike.
"""

from __future__ import annotations

from empire.ai.search.naval import _step_toward
from empire.ai.search.plan import Plan
from empire.ai.vision import frontier_cells
from empire.contracts.turn_plan import UnitMove
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.unit import Unit, UnitKind
from empire.pathfinding.cost import AIR as AIR_COST
from empire.pathfinding.distance_field import DistanceField, PassabilityGrid

# Spare fuel kept beyond the bare return trip — covers path detours around the
# off-board border ring and a turn's slack so a fighter is never stranded.
_FUEL_SAFETY = 2


def plan_air(view: WorldView, plan: Plan) -> dict[UnitId, UnitMove]:
    """Moves for every airborne own fighter: refuel if low, else strike a
    reachable sighted enemy, else scout the nearest reachable fog frontier —
    always keeping enough fuel to return to a friendly city."""
    del plan  # air behavior is plan-independent for now (recon/strike always on)
    fighters = [
        u
        for u in view.own_units
        if u.kind is UnitKind.FIGHTER and u.carried_by is None
    ]
    if not fighters:
        return {}
    cities = [c.coord for c in view.own_cities]
    if not cities:
        return {}  # no base to refuel at — leave fighters to the engine
    grid = PassabilityGrid(view.real_map(), AIR_COST, view.own_player.view)
    frontier = frontier_cells(view)
    enemy = [k.snapshot.coord for k in view.known_enemy_units]
    out: dict[UnitId, UnitMove] = {}
    for fighter in fighters:
        move = _fighter_move(fighter, cities, frontier, enemy, grid)
        if move is not None:
            out[fighter.id] = move
    return out


def _fighter_move(
    fighter: Unit,
    cities: list[Coord],
    frontier: frozenset[Coord],
    enemy: list[Coord],
    grid: PassabilityGrid,
) -> UnitMove | None:
    rng = fighter.range
    nearest_city = min(cities, key=lambda c: fighter.coord.chebyshev_to(c))
    home = fighter.coord.chebyshev_to(nearest_city)
    field = DistanceField(fighter.coord, grid)

    def returnable(dest: Coord) -> bool:
        back = min(dest.chebyshev_to(c) for c in cities)
        return fighter.coord.chebyshev_to(dest) + back + _FUEL_SAFETY <= rng

    # Low on fuel → head to the nearest base to refuel.
    if rng <= home + _FUEL_SAFETY:
        return _step_toward(fighter, nearest_city, field)

    # Opportunistic strike: nearest sighted enemy we can hit and still get home.
    strikes = sorted(
        (fighter.coord.chebyshev_to(e), e.y, e.x, e)
        for e in enemy
        if returnable(e)
    )
    if strikes:
        return _step_toward(fighter, strikes[0][3], field)

    # Recon: nearest fog frontier we can reach and return from.
    recon = sorted(
        (fighter.coord.chebyshev_to(c), c.y, c.x, c)
        for c in frontier
        if returnable(c)
    )
    if recon:
        return _step_toward(fighter, recon[0][3], field)

    # Nothing useful in range → return toward base (top up / loiter).
    if home > 0:
        return _step_toward(fighter, nearest_city, field)
    return None

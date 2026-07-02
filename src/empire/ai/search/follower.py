"""`PlanFollower`: the controller that executes one `Plan`.

It runs in two places — inside playouts (driving the searcher's side of a
simulated future) and as the real-turn executor for the plan the search
commits — so the engine sees exactly the behavior the search scored.

Assignment is recomputed from scratch every turn, deterministically:
objectives take their `strength` nearest armies in plan-priority order
(ties break on unit registry order), the rest follow the surplus policy.
Recomputing — rather than remembering — is what keeps an objective-level
plan executable over a playout horizon where units die and new ones appear.

Movement discipline under city artillery (both measured —
71% of armies lost to artillery died with no friendly within 3 cells, and
the survivors were bleeding on *transit* through unrelated cities' rings):

- **Danger-aware routing**: every hostile city's artillery ring (enemy AND
  neutral — neutral cities shoot everyone) is masked out of the movement
  grid. An assault group's own target keeps its ring open, since that ring
  must eventually be entered. If masking disconnects a goal, the raw grid
  is the fallback — better to run a gauntlet than to abandon the plan.
- **Mass before storming**: an assault group stages on the ring just
  outside its target's range and enters only when every member is staged;
  the city's one-shot-per-round cannot defeat a simultaneous entry in
  detail, but kills a trickle one by one.

The follower is immutable: one instance per plan. `SearchAI` builds a fresh
follower for each candidate playout and for each committed turn.
"""

from __future__ import annotations

from empire.ai.search.air import plan_air
from empire.ai.search.naval import SEA_KINDS, is_ocean_coastal, plan_naval
from empire.ai.search.plan import Objective, Plan, Role, SurplusPolicy
from empire.ai.vision import frontier_cells
from empire.contracts.surprise import Surprise
from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove, UnloadOrder
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.tile import TerrainKind
from empire.core.unit import Unit, UnitKind
from empire.pathfinding.cost import ARMY as ARMY_COST_PROFILE
from empire.pathfinding.distance_field import DistanceField, PassabilityGrid


def idle_step(unit: Unit, view: WorldView) -> UnitMove:
    """The 'nothing better to do' fallback. A land unit must NEVER end its turn
    on a friendly city — §5.4 disbands it at turn-end — so if it is sitting on
    one, step off to any legal, unoccupied, non-friendly-city neighbour rather
    than sentry. Off a city, or for a unit §5.4 doesn't disband, plain sentry."""
    tile = view.terrain_at(unit.coord)
    on_friendly_city = (
        tile is not None
        and tile.terrain is TerrainKind.CITY
        and tile.city is not None
        and tile.city.owner is view.own_player
    )
    if not on_friendly_city:
        return UnitMove(unit_id=unit.id)

    legal = type(unit).legal_terrain
    for nb in unit.coord.neighbors():
        if not view.in_bounds(nb):
            continue
        nb_tile = view.terrain_at(nb)
        if nb_tile is None or nb_tile.terrain not in legal:
            continue
        # Don't step onto another friendly city (same trap) or a friendly unit.
        if nb_tile.terrain is TerrainKind.CITY:
            continue
        if any(u.owner is view.own_player for u in view.real_map().units_at(nb)):
            continue
        return UnitMove(unit_id=unit.id, path=((nb.x, nb.y),))
    # Boxed in (every escape blocked) — sentry and accept the disband.
    return UnitMove(unit_id=unit.id)


# Roles whose armies storm a city: a land assault and the land phase of an
# amphibious invasion both drive troops onto the target the same way (the
# follower handles INVADE cargo via `plan_naval`; any *landed* invasion army
# falls through to here to take the city).
_STORM_ROLES = frozenset({Role.ASSAULT, Role.INVADE})


class PlanFollower:
    """Executes one frozen `Plan`; satisfies `AIController` structurally."""

    def __init__(self, plan: Plan) -> None:
        self._plan: Plan = plan

    @property
    def plan(self) -> Plan:
        return self._plan

    def name(self) -> str:
        return "PlanFollower"

    # ---- AIController ------------------------------------------------------

    def plan_turn(self, view: WorldView) -> TurnPlan:
        moves, unloads = self._decide(view)
        # Ships can only launch from a coastal city; building one inland just
        # strands it. When the plan calls for a sea unit, build it only at
        # coastal cities and make cargo armies at the inland ones instead.
        sea_prod = self._plan.production in SEA_KINDS
        # Fighters build at a single base, not fleet-wide — a couple of scouts
        # is plenty and they refuel at home; the other cities keep making armies
        # so an air plan doesn't gut ground production. Cheapest sane base: the
        # first own city (deterministic).
        air_prod = self._plan.production is UnitKind.FIGHTER
        air_base = view.own_cities[0].id if air_prod and view.own_cities else None
        production: list[ProductionOrder] = []
        for c in view.own_cities:
            target = self._plan.production
            if (sea_prod and not is_ocean_coastal(view, c.coord)) or (air_prod and c.id != air_base):
                target = UnitKind.ARMY
            # Re-target only when the city isn't already building the desired
            # kind. `building` is never cleared to None once set (production
            # keeps churning out the same kind), so guarding on "is None"
            # would lock a city to its first-ever order forever — fatal the
            # moment a plan needs to switch army->transport. `set_target`
            # applies the §5.2 change penalty on a real switch and is a no-op
            # when the kind already matches.
            if c.production.building is target:
                continue
            production.append(ProductionOrder(city_id=c.id, target=target))
        return TurnPlan(
            production_orders=tuple(production),
            moves=tuple(m for m in moves.values() if m.path),
            unloads=tuple(unloads),
        )

    def revise_move(
        self, unit_id: UnitId, surprise: Surprise, view: WorldView
    ) -> UnitMove:
        del surprise  # plan-driven: re-decide against the live view
        moves, _ = self._decide(view)
        return moves.get(unit_id, UnitMove(unit_id=unit_id))

    # ---- decision core -----------------------------------------------------

    def _decide(
        self, view: WorldView
    ) -> tuple[dict[UnitId, UnitMove], list[UnloadOrder]]:
        """All unit moves + amphibious unloads this turn. Naval runs first
        and claims invasion-cargo armies; the land logic handles the rest."""
        naval = plan_naval(view, self._plan)
        air = plan_air(view, self._plan)
        land = self._decide_land(view, naval.claimed_armies)
        # Fighters are neither cargo nor land armies, so no claim conflict.
        return {**land, **naval.moves, **air}, naval.unloads

    def _decide_land(
        self, view: WorldView, claimed: set[UnitId]
    ) -> dict[UnitId, UnitMove]:
        """Every (non-cargo) army's move this turn, by deterministic
        re-assignment. `claimed` armies are committed as naval cargo."""
        armies = [
            u
            for u in view.own_units
            if u.kind is UnitKind.ARMY
            and u.carried_by is None
            and u.id not in claimed
        ]
        if not armies:
            return {}
        grid = PassabilityGrid(
            view.real_map(), ARMY_COST_PROFILE, view.own_player.view
        )
        fields_raw = {u.id: DistanceField(u.coord, grid) for u in armies}

        assignment = self._assign(armies, fields_raw)
        groups: dict[Objective, list[Unit]] = {}
        for unit in armies:
            objective = assignment.get(unit.id)
            if objective is not None:
                groups.setdefault(objective, []).append(unit)

        rings = self._hostile_rings(view)
        all_ring_cells = frozenset(
            cell for cells in rings.values() for cell in cells
        )
        safe_grid = grid.with_blocked(all_ring_cells) if rings else grid
        target_grids: dict[Coord, PassabilityGrid] = {}
        for objective in groups:
            if objective.role in _STORM_ROLES and objective.target not in target_grids:
                others = frozenset(
                    cell
                    for center, cells in rings.items()
                    if center != objective.target
                    for cell in cells
                )
                target_grids[objective.target] = (
                    grid.with_blocked(others) if others else grid
                )

        surplus_scouts = self._plan.surplus is SurplusPolicy.SCOUT and any(
            u.id not in assignment for u in armies
        )
        frontier = frontier_cells(view) if surplus_scouts else frozenset[Coord]()

        moves: dict[UnitId, UnitMove] = {}
        for unit in armies:
            objective = assignment.get(unit.id)
            if objective is None:
                field = DistanceField(unit.coord, safe_grid)
                moves[unit.id] = self._surplus_move(
                    unit, field, fields_raw[unit.id], frontier, view
                )
            elif objective.role is Role.DEFEND:
                field = DistanceField(unit.coord, safe_grid)
                moves[unit.id] = self._defend_move(
                    unit, objective, field, fields_raw[unit.id], view
                )
            else:
                approach = DistanceField(
                    unit.coord, target_grids[objective.target]
                )
                if approach.steps_to(objective.target) is None:
                    approach = fields_raw[unit.id]  # gauntlet beats abandonment
                moves[unit.id] = self._assault_move(
                    unit, objective, groups[objective], approach, view
                )
        return moves

    def _hostile_rings(self, view: WorldView) -> dict[Coord, frozenset[Coord]]:
        """Artillery ring cells of every known hostile city (enemy + neutral —
        neutral cities fire at everyone), keyed by city coord. Empty without
        artillery rules."""
        artillery_range = view.rules.city_artillery_range
        if artillery_range <= 0:
            return {}
        rings: dict[Coord, frozenset[Coord]] = {}
        for city in view.known_enemy_cities + view.neutral_cities:
            center = city.coord
            rings[center] = frozenset(
                Coord(center.x + dx, center.y + dy)
                for dx in range(-artillery_range, artillery_range + 1)
                for dy in range(-artillery_range, artillery_range + 1)
            )
        return rings

    def _assign(
        self,
        armies: list[Unit],
        fields: dict[UnitId, DistanceField],
    ) -> dict[UnitId, Objective]:
        """Objectives pick their `strength` nearest reachable armies, in plan
        order. Ties break on the armies' registry order (stable, serialized)."""
        assignment: dict[UnitId, Objective] = {}
        unassigned: list[Unit] = list(armies)
        for objective in self._plan.objectives:
            if objective.strength <= 0:
                continue
            ranked = sorted(
                (
                    (steps, index, unit)
                    for index, unit in enumerate(unassigned)
                    if (steps := fields[unit.id].steps_to(objective.target))
                    is not None
                ),
                key=lambda entry: (entry[0], entry[1]),
            )
            chosen = [unit for _, _, unit in ranked[: objective.strength]]
            for unit in chosen:
                assignment[unit.id] = objective
            unassigned = [u for u in unassigned if u.id not in assignment]
        return assignment

    # ---- per-role movement ---------------------------------------------------

    def _defend_move(
        self,
        unit: Unit,
        objective: Objective,
        field: DistanceField,
        raw_field: DistanceField,
        view: WorldView,
    ) -> UnitMove:
        if unit.coord.chebyshev_to(objective.target) <= 1:
            # Garrison holds at the city's edge — never on it (§5.4).
            return idle_step(unit, view)
        return self._advance(unit, objective.target, field, view, raw_field)

    def _assault_move(
        self,
        unit: Unit,
        objective: Objective,
        group: list[Unit],
        field: DistanceField,
        view: WorldView,
    ) -> UnitMove:
        """Mass at the ring, then storm together (see module docstring).

        The storm gate is judged over *near* members only (within twice the
        ring) — assignment is recomputed every turn, so a freshly produced
        army joining the group from across the map must reinforce, not
        freeze a staged fist at the ring. The near set must reach the fist
        size (or the whole group, if smaller) so a lone first-arriver waits
        rather than trickling in. Once any member is inside the fire zone
        the storm is latched: late joiners can't stall an entry under fire.
        """
        target = objective.target
        artillery_range = view.rules.city_artillery_range
        if artillery_range <= 0:
            return self._advance(unit, target, field, view)
        ring = artillery_range + 1
        storming = any(
            u.coord.chebyshev_to(target) <= artillery_range for u in group
        )
        if not storming:
            near = [
                u for u in group if u.coord.chebyshev_to(target) <= 2 * ring
            ]
            quorum = min(objective.strength, len(group))
            storming = len(near) >= quorum and all(
                u.coord.chebyshev_to(target) <= ring + 1 for u in near
            )
        if storming:
            return self._advance(unit, target, field, view)  # storm together
        if unit.coord.chebyshev_to(target) <= ring:
            # Already at the ring: hold for the rest of the fist (§5.4-safe).
            return idle_step(unit, view)
        # Approach, but stop short of the city's fire — truncate any step
        # that would enter artillery range before the group is ready.
        cells = field.path_to(target)
        if cells is None or len(cells) < 2:
            return idle_step(unit, view)
        budget = unit.moves_this_turn()
        steps: list[Coord] = []
        for cell in cells[1 : 1 + budget]:
            if cell.chebyshev_to(target) <= artillery_range:
                break
            steps.append(cell)
        if not steps:
            return idle_step(unit, view)
        return UnitMove(unit_id=unit.id, path=tuple((c.x, c.y) for c in steps))

    def _surplus_move(
        self,
        unit: Unit,
        field: DistanceField,
        raw_field: DistanceField,
        frontier: frozenset[Coord],
        view: WorldView,
    ) -> UnitMove:
        if self._plan.surplus is SurplusPolicy.RESERVE:
            return idle_step(unit, view)
        # SCOUT: push toward the nearest safely-reachable frontier cell;
        # if no frontier is reachable without crossing a ring, brave it.
        target = self._nearest_frontier(field, frontier)
        if target is not None:
            return self._advance(unit, target, field, view)
        target = self._nearest_frontier(raw_field, frontier)
        if target is not None:
            return self._advance(unit, target, raw_field, view)
        # No frontier at all (late game, board explored): under artillery
        # rules, rally behind the nearest active assault instead of freezing
        # in place — otherwise unassigned armies stand
        # standing as statues at distance 6 while 3-army fists ground
        # themselves on the gauntlet; reserves massed at the ring make the
        # next re-assignment instant. WITHOUT artillery there is nothing to
        # mass against — a reserve loitering beside an enemy city is pure
        # wasted tempo (measured: STANDARD 51%->45.5% with the rally on) —
        # so hold as before and let assignment pull units when needed.
        if view.rules.city_artillery_range > 0:
            return self._rally_move(unit, field, raw_field, view)
        return idle_step(unit, view)

    def _rally_move(
        self,
        unit: Unit,
        field: DistanceField,
        raw_field: DistanceField,
        view: WorldView,
    ) -> UnitMove:
        """Close to just outside the nearest assault objective's ring."""
        assault_targets = [
            o.target for o in self._plan.objectives if o.role is Role.ASSAULT
        ]
        if not assault_targets:
            return idle_step(unit, view)
        # Rank on the RAW field: a hostile target is always unreachable on
        # the safe field (it sits inside its own masked ring).
        ranked = sorted(
            (
                (dist, t.y, t.x, t)
                for t in assault_targets
                if (dist := raw_field.steps_to(t)) is not None
            ),
        )
        if not ranked:
            return idle_step(unit, view)
        target = ranked[0][3]
        artillery_range = view.rules.city_artillery_range
        hold_at = artillery_range + 2 if artillery_range > 0 else 1
        if unit.coord.chebyshev_to(target) <= hold_at:
            return idle_step(unit, view)
        # Raw path (the truncation below keeps us out of the target's own
        # fire); the safe field can never path to a hostile target.
        cells = raw_field.path_to(target)
        if cells is None or len(cells) < 2:
            return idle_step(unit, view)
        budget = unit.moves_this_turn()
        steps: list[Coord] = []
        for cell in cells[1 : 1 + budget]:
            if cell.chebyshev_to(target) < hold_at:
                break
            steps.append(cell)
        if not steps:
            return idle_step(unit, view)
        return UnitMove(unit_id=unit.id, path=tuple((c.x, c.y) for c in steps))

    def _advance(
        self,
        unit: Unit,
        target: Coord,
        field: DistanceField,
        view: WorldView,
        fallback: DistanceField | None = None,
    ) -> UnitMove:
        """Step along `field` toward `target`; if the (danger-masked) field
        can't reach it, `fallback` braves the gauntlet. §5.4-safe otherwise."""
        cells = field.path_to(target)
        if cells is None and fallback is not None:
            cells = fallback.path_to(target)
        if cells is None or len(cells) < 2:
            return idle_step(unit, view)
        budget = unit.moves_this_turn()
        steps = cells[1 : 1 + budget]
        return UnitMove(unit_id=unit.id, path=tuple((c.x, c.y) for c in steps))

    def _nearest_frontier(
        self, field: DistanceField, frontier: frozenset[Coord]
    ) -> Coord | None:
        """The reachable frontier cell fewest steps from the unit; ties break
        on (y, x) for determinism."""
        best: tuple[int, int, int] | None = None
        best_cell: Coord | None = None
        for cell in frontier:
            steps = field.steps_to(cell)
            if steps is None or steps == 0:
                continue
            key = (steps, cell.y, cell.x)
            if best is None or key < best:
                best, best_cell = key, cell
        return best_cell

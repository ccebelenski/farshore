"""`Strategist`: turns an `IntelReport` into a ranked list of `Goal`s.

`DeterministicStrategist` implements the rule-based algorithm from
`planning/03-ai-design.md` §3.2: defend what's threatened, consolidate
contested landmasses, expand onto reachable cities, then fill spare capacity
with power-projection / exploration / force-building. Every goal that needs a
force is gated through the `FeasibilityOracle`, so the strategist never
proposes something obviously impossible.

Pure given (intel, view): the same inputs yield the same goals. `AIMemory` is
threaded for the continuity/anti-thrash heuristics of later phases.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

from empire.ai.strategic.feasibility import FeasibilityOracle
from empire.ai.strategic.goals import (
    CaptureCityGoal,
    DefendCityGoal,
    DenyContinentGoal,
    ExploreAreaGoal,
    ForceComposition,
    Goal,
    ProjectPowerGoal,
    ResourceBudget,
)
from empire.ai.strategic.intel.report import (
    IntelReport,
    Opportunity,
    OpportunityKind,
    TheaterState,
)
from empire.ai.strategic.memory import AIMemory
from empire.ai.strategic.operational import ATTACK_FORCE_SIZE
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import CityId, GoalId
from empire.core.tile import TerrainKind
from empire.core.unit import UnitKind

# Tuning knobs (v1; subject to playtest tuning).
_DEFEND_PRIORITY = 0.9
_DENY_PRIORITY = 0.6
_PROJECT_PRIORITY = 0.5
_EXPLORE_PRIORITY = 0.2
_CAPTURE_SCORE_NORM = 10.0  # opportunity score that maps to priority 1.0
_ARMY = UnitKind.ARMY


class Strategist(ABC):
    """Produces the turn's strategic goals from intel."""

    @abstractmethod
    def plan(
        self, intel: IntelReport, memory: AIMemory, view: WorldView
    ) -> list[Goal]:
        ...


class DeterministicStrategist(Strategist):
    """The rule-based default strategist."""

    def __init__(self, feasibility: FeasibilityOracle) -> None:
        self._oracle = feasibility

    def plan(
        self, intel: IntelReport, memory: AIMemory, view: WorldView
    ) -> list[Goal]:
        del memory  # reserved for anti-thrash / continuity (later phases)
        goals: list[Goal] = []
        counter = _Counter()

        self._defensive_goals(intel, view, goals, counter)
        self._expand_goals(intel, view, goals, counter)
        # Consolidate (DenyContinent) and explore goals are intentionally not
        # emitted: they scattered armies to distant theater cells, defeating
        # concentration. Hunt mode (idle units → frontier, in the tactical
        # layer) covers exploration; capture goals cover expansion. The goal
        # types still exist for the LLM strategist and future tuning.

        # Best value-per-turn first; ties broken on id for reproducibility.
        # No blunt global cap any more: the per-class budgets in `_expand_goals`
        # (defended fronts = fists we can fill, soft grabs by army pool) plus the
        # threat-bounded defends already concentrate force. The old
        # `own_cities + 2` trim cut deliberately-budgeted captures whenever we
        # held few cities — exactly when we most needed to expand.
        goals.sort(key=lambda g: (-g.rank(), int(g.id)))
        return goals

    # -- step 1: defend ------------------------------------------------------

    def _defensive_goals(
        self,
        intel: IntelReport,
        view: WorldView,
        goals: list[Goal],
        counter: _Counter,
    ) -> None:
        own_cities = {c.id: c for c in view.own_cities}
        # Worst projected threat power bearing on each owned city.
        threat_to_city: dict[CityId, int] = {}
        for threat in intel.threats:
            for cid in threat.at_risk_city_ids:
                if cid in own_cities:
                    threat_to_city[cid] = max(
                        threat_to_city.get(cid, 0), threat.combat_power
                    )
        for cid in sorted(threat_to_city, key=int):
            city = own_cities[cid]
            power = threat_to_city[cid]
            # Only commit if we can actually hold it (else: evacuate, later).
            strongest = max(
                (t for t in intel.threats if cid in t.at_risk_city_ids),
                key=lambda t: t.combat_power,
            )
            if not self._oracle.defensible(city, strongest, view):
                continue
            garrison = max(1, math.ceil(power / 2))
            goals.append(
                DefendCityGoal(
                    id=counter.next(),
                    priority=_DEFEND_PRIORITY,
                    estimated_duration=2,
                    budget=ResourceBudget(city_ids=(cid,), production_slots=garrison),
                    target_city_id=cid,
                    target_coord=city.coord,
                    garrison_size_needed=garrison,
                )
            )

    # -- step 2: consolidate contested landmasses ----------------------------

    def _consolidate_goals(
        self,
        intel: IntelReport,
        view: WorldView,
        goals: list[Goal],
        counter: _Counter,
    ) -> None:
        for theater in intel.theaters:
            if theater.state is not TheaterState.CONTESTED:
                continue
            friendly = len(theater.friendly_city_ids)
            enemy = len(theater.enemy_city_ids)
            neutral = len(theater.neutral_city_ids)
            total = friendly + enemy + neutral
            # Only push when we already hold the majority and targets remain.
            if total == 0 or friendly * 2 < total or (enemy + neutral) == 0:
                continue
            goals.append(
                DenyContinentGoal(
                    id=counter.next(),
                    priority=_DENY_PRIORITY,
                    estimated_duration=max(3, (enemy + neutral) * 2),
                    target_region=_sorted_cells(theater.cells),
                    enemy_city_ids=theater.enemy_city_ids,
                    neutral_city_ids=theater.neutral_city_ids,
                )
            )

    # -- step 3: expand onto reachable cities --------------------------------

    def _expand_goals(
        self,
        intel: IntelReport,
        view: WorldView,
        goals: list[Goal],
        counter: _Counter,
    ) -> None:
        """Expand onto reachable cities, splitting targets by whether they are
        *defended* (Phase 15.7 concentration heuristic). Telemetry showed the old
        single budget dribbled 1.5-army 'fists' at defended cities because it
        opened one more front than it could crew (`+1`) and the deadline shoved
        the starved ones out. Now:

        * **Soft grabs** — an undefended neutral (no artillery): a lone army
          walks in. Emitted liberally; they don't need a fist and operational
          crews them from leftover idle after the real fists.
        * **Defended assaults** — enemy cities, and *every* city under
          FortifiedCities: need the full fist. Capped at the number of fists we
          can actually fill (`own_armies // fist`, NO `+1`) and chosen
          nearest-first (urgency), coastal as the tiebreak (future port value).
        """
        anchors = [c.coord for c in view.own_cities] + [u.coord for u in view.own_units]
        if not anchors:
            return
        artillery = view.rules.city_artillery_range > 0
        own_armies = sum(1 for u in view.own_units if u.kind is _ARMY)
        defended_cap = max(1, own_armies // ATTACK_FORCE_SIZE)
        soft_cap = max(3, own_armies)

        soft: list[Opportunity] = []
        defended: list[Opportunity] = []
        projected = False
        for opp in intel.opportunities:
            if opp.target_city_id is None or opp.kind not in (
                OpportunityKind.CAPTURE_NEUTRAL_CITY,
                OpportunityKind.CAPTURE_ENEMY_CITY,
            ):
                continue
            start = min(anchors, key=lambda a: a.chebyshev_to(opp.target))
            by_turn = view.turn + max(2, opp.distance + 1)  # army speed 1
            reachable = self._oracle.can_assemble(
                ForceComposition.of({_ARMY: 1}), by_turn, view
            ) and self._oracle.reachable(start, opp.target, _ARMY, by_turn, view)
            if not reachable:
                if not projected:  # across water → one capped amphibious push
                    projected = self._maybe_project(opp.target, view, goals, counter)
                continue
            is_soft = opp.kind is OpportunityKind.CAPTURE_NEUTRAL_CITY and not artillery
            (soft if is_soft else defended).append(opp)

        # Defended: nearest first (urgency), coastal as tiebreak, value last.
        defended.sort(
            key=lambda o: (o.distance, not _is_coastal(o.target, view), -o.score)
        )
        for opp in soft[:soft_cap] + defended[:defended_cap]:
            if opp.target_city_id is None:  # filtered above; keeps the type tight
                continue
            goals.append(
                CaptureCityGoal(
                    id=counter.next(),
                    priority=min(1.0, opp.score / _CAPTURE_SCORE_NORM),
                    estimated_duration=max(2, opp.distance + 1),
                    budget=ResourceBudget(production_slots=1),
                    target_city_id=opp.target_city_id,
                    target_coord=opp.target,
                )
            )

    def _maybe_project(
        self, target: Coord, view: WorldView, goals: list[Goal], counter: _Counter
    ) -> bool:
        """Emit a transport-borne ProjectPowerGoal toward `target` if we can
        assemble the flotilla. Returns whether one was emitted."""
        by_turn = view.turn + 12
        force = ForceComposition.of({_ARMY: 1, UnitKind.TRANSPORT: 1})
        if not self._oracle.can_assemble(force, by_turn, view):
            return False
        goals.append(
            ProjectPowerGoal(
                id=counter.next(),
                priority=_PROJECT_PRIORITY,
                estimated_duration=12,
                target_region=(target,),
                force_composition=force,
                transport_count=1,
            )
        )
        return True

    # -- step 4: explore ------------------------------------------------------

    def _explore_goals(
        self, intel: IntelReport, goals: list[Goal], counter: _Counter
    ) -> None:
        for theater in intel.theaters:
            if theater.state is not TheaterState.UNEXPLORED:
                continue
            goals.append(
                ExploreAreaGoal(
                    id=counter.next(),
                    priority=_EXPLORE_PRIORITY,
                    estimated_duration=5,
                    target_region=_sorted_cells(theater.cells),
                )
            )


class _Counter:
    """Allocates per-plan GoalIds in deterministic emission order."""

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> GoalId:
        self._n += 1
        return GoalId(self._n)


def _sorted_cells(cells: frozenset[Coord]) -> tuple[Coord, ...]:
    return tuple(sorted(cells, key=lambda c: (c.x, c.y)))


def _is_coastal(coord: Coord, view: WorldView) -> bool:
    """A city cell that touches water — a future port, worth more for naval
    projection. Used only as a tiebreak between equidistant defended fronts."""
    for n in coord.neighbors():
        tile = view.terrain_at(n)
        if tile is not None and tile.terrain is TerrainKind.WATER:
            return True
    return False

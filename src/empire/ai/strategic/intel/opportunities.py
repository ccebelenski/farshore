"""Opportunity assessment: capturable cities and attackable enemy forces,
scored so the strategist can rank them directly.

Pure over `WorldView`: same view → same opportunities.
"""

from __future__ import annotations

from empire.ai.strategic.intel.report import Opportunity, OpportunityKind
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import CityId, UnitId
from empire.core.unit import UNIT_REGISTRY

# Base values (arbitrary units; only their ratios matter to the ranking).
# A captured city out-produces any single unit kill, so cities dominate.
NEUTRAL_CITY_VALUE = 10
ENEMY_CITY_VALUE = 15
ENEMY_UNIT_VALUE_PER_STRENGTH = 2

# Success priors. A neutral city always falls to an army that reaches it; an
# enemy city is garrisoned, so less certain; a field engagement is a coin-flip
# absent combat odds (the FeasibilityOracle refines this in Phase 12).
NEUTRAL_CAPTURE_PROBABILITY = 1.0
ENEMY_CAPTURE_PROBABILITY = 0.6
ENEMY_UNIT_ATTACK_PROBABILITY = 0.5


def assess_opportunities(view: WorldView) -> tuple[Opportunity, ...]:
    """Rank capturable cities and attackable enemy units for the player."""
    anchors = [c.coord for c in view.own_cities] + [u.coord for u in view.own_units]
    if not anchors:
        # No forces and no cities: nothing can act on any opportunity.
        return ()

    def distance_to(coord: Coord) -> int:
        return min(a.chebyshev_to(coord) for a in anchors)

    opps: list[Opportunity] = []

    for city in view.neutral_cities:
        opps.append(
            _score(
                OpportunityKind.CAPTURE_NEUTRAL_CITY,
                city.coord,
                NEUTRAL_CITY_VALUE,
                NEUTRAL_CAPTURE_PROBABILITY,
                distance_to(city.coord),
                city_id=city.id,
            )
        )

    for city in view.known_enemy_cities:
        opps.append(
            _score(
                OpportunityKind.CAPTURE_ENEMY_CITY,
                city.coord,
                ENEMY_CITY_VALUE,
                ENEMY_CAPTURE_PROBABILITY,
                distance_to(city.coord),
                city_id=city.id,
            )
        )

    # Only currently-visible enemy units are attack targets: chasing a
    # remembered ghost wastes moves. `seen_at_turn == view.turn` marks "seen
    # this turn", which is exactly the currently-visible set.
    for known in view.known_enemy_units:
        if known.seen_at_turn != view.turn:
            continue
        snap = known.snapshot
        strength = UNIT_REGISTRY[snap.kind].strength
        opps.append(
            _score(
                OpportunityKind.ATTACK_ENEMY_UNIT,
                snap.coord,
                strength * ENEMY_UNIT_VALUE_PER_STRENGTH,
                ENEMY_UNIT_ATTACK_PROBABILITY,
                distance_to(snap.coord),
                unit_id=snap.unit_id,
            )
        )

    # Highest score first; ties broken on coordinate then kind for stability.
    opps.sort(key=lambda o: (-o.score, o.target.x, o.target.y, o.kind.value))
    return tuple(opps)


def _score(
    kind: OpportunityKind,
    target: Coord,
    value: int,
    probability: float,
    distance: int,
    *,
    city_id: CityId | None = None,
    unit_id: UnitId | None = None,
) -> Opportunity:
    # Discount by distance: a prize next door beats the same prize across the
    # map. `1 + distance` keeps an adjacent target (distance 0) at full value.
    score = value * probability / (1 + distance)
    return Opportunity(
        kind=kind,
        target=target,
        target_city_id=city_id,
        target_unit_id=unit_id,
        value=value,
        success_probability=probability,
        distance=distance,
        score=score,
    )

"""`CandidateGenerator`: the plausible plans `SearchAI` evaluates each turn.

The generator's job is breadth, not judgment — propose every course of
action a competent player might consider and let the playouts price them.
It anchors on known cities (nearest first), offers two commitment levels
per target (the rule-derived fist and an over-strength fist), multi-front
splits, a defensive posture, and the do-nothing/scout baselines. K stays
in the 8-32 band the design budgeted (`planning/03-ai-design.md` §9.1).

Deliberately heuristic and cheap: anything the generator misses the search
can never choose, but anything implausible it proposes merely wastes one
playout.
"""

from __future__ import annotations

from empire.ai.search.plan import Objective, Plan, Role, SurplusPolicy
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.ruleset import RuleSet

# How many nearest targets get dedicated assault candidates.
TARGET_FAN = 4
# Garrison size per threatened home city in the defensive candidate.
DEFEND_STRENGTH = 2
# An enemy unit within this range of a home city marks it threatened.
THREAT_RANGE = 6


class CandidateGenerator:
    """Proposes candidate `Plan`s from one fog view."""

    def generate(self, view: WorldView) -> tuple[Plan, ...]:
        """Candidates in a stable, deterministic order (ties in playout score
        resolve toward the earlier candidate, so order encodes a mild prior:
        aggressive single-front plans first, passivity last)."""
        fist = self._fist_size(view.rules)
        targets = self._ranked_targets(view)
        defenses = self._threatened_home_cities(view)

        plans: list[Plan] = []

        # Single-front assaults: normal fist and over-strength fist.
        for target in targets[:TARGET_FAN]:
            for strength in (fist, fist + 2):
                plans.append(
                    Plan(
                        objectives=(Objective(target, Role.ASSAULT, strength),),
                        surplus=SurplusPolicy.SCOUT,
                    )
                )

        # Two-front split: the two nearest targets, one fist each.
        if len(targets) >= 2:
            plans.append(
                Plan(
                    objectives=(
                        Objective(targets[0], Role.ASSAULT, fist),
                        Objective(targets[1], Role.ASSAULT, fist),
                    ),
                    surplus=SurplusPolicy.SCOUT,
                )
            )

        # Assault + home defense, when something threatens home.
        if targets and defenses:
            guard = tuple(
                Objective(c, Role.DEFEND, DEFEND_STRENGTH) for c in defenses[:2]
            )
            plans.append(
                Plan(
                    objectives=(
                        Objective(targets[0], Role.ASSAULT, fist),
                        *guard,
                    ),
                    surplus=SurplusPolicy.SCOUT,
                )
            )

        # Pure defense: garrison every threatened city, scout with the rest.
        if defenses:
            plans.append(
                Plan(
                    objectives=tuple(
                        Objective(c, Role.DEFEND, DEFEND_STRENGTH)
                        for c in defenses[:4]
                    ),
                    surplus=SurplusPolicy.SCOUT,
                )
            )

        # Baselines the search must always be able to fall back on.
        plans.append(Plan(objectives=(), surplus=SurplusPolicy.SCOUT))
        plans.append(Plan(objectives=(), surplus=SurplusPolicy.RESERVE))

        return tuple(plans)

    # ---- target/threat ranking ----------------------------------------------

    @staticmethod
    def _fist_size(rules: RuleSet) -> int:
        """Rule-derived assault strength: under city artillery the city fires
        at most `range` times on a direct approach, so `range + 1` armies
        guarantee one lands (Phase 15.7's `_fortified_fist` insight)."""
        if rules.city_artillery_range > 0:
            return rules.city_artillery_range + 1
        return 3

    def _ranked_targets(self, view: WorldView) -> list[Coord]:
        """Capturable cities, nearest-to-home first (enemy and neutral alike —
        the playout, not a weight table, decides which is worth more)."""
        home = self._home_centroid(view)
        targets = [c.coord for c in view.known_enemy_cities]
        targets += [c.coord for c in view.neutral_cities]
        targets.sort(key=lambda c: (c.chebyshev_to(home), c.y, c.x))
        return targets

    @staticmethod
    def _home_centroid(view: WorldView) -> Coord:
        own = view.own_cities
        if not own:
            units = view.own_units
            if not units:
                return Coord(0, 0)
            return units[0].coord
        x = sum(c.coord.x for c in own) // len(own)
        y = sum(c.coord.y for c in own) // len(own)
        return Coord(x, y)

    @staticmethod
    def _threatened_home_cities(view: WorldView) -> list[Coord]:
        """Own cities with a known enemy unit within `THREAT_RANGE`, most
        threatened (nearest enemy) first."""
        sightings = [k.snapshot.coord for k in view.known_enemy_units]
        if not sightings:
            return []
        ranked: list[tuple[int, int, int, Coord]] = []
        for city in view.own_cities:
            nearest = min(city.coord.chebyshev_to(s) for s in sightings)
            if nearest <= THREAT_RANGE:
                ranked.append((nearest, city.coord.y, city.coord.x, city.coord))
        ranked.sort()
        return [c for _, _, _, c in ranked]


# Production note: every candidate currently builds armies (`Plan.production`
# defaults to ARMY). Fighter-quota candidates join the generator when the
# combined-arms increment lands (Step 3).

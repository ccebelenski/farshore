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

from empire.ai.search.naval import is_ocean_coastal
from empire.ai.search.plan import Objective, Plan, Role, SurplusPolicy
from empire.ai.vision import sea_frontier_cells
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.ruleset import RuleSet
from empire.core.unit import UnitKind

# How many nearest targets get dedicated assault candidates.
TARGET_FAN = 4
# How many targets the all-out close-out candidate attacks simultaneously.
ALL_OUT_FAN = 6
# Garrison size per threatened home city in the defensive candidate.
DEFEND_STRENGTH = 2
# An enemy unit within this range of a home city marks it threatened.
THREAT_RANGE = 6
# Strength a single amphibious operation commits.
INVADE_STRENGTH = 3


class CandidateGenerator:
    """Proposes candidate `Plan`s from one fog view."""

    def generate(self, view: WorldView) -> tuple[Plan, ...]:
        """Candidates in a stable, deterministic order (ties in playout score
        resolve toward the earlier candidate, so order encodes a mild prior:
        aggressive single-front plans first, passivity last).

        Strategy is sequential: fight the home continent first (land plans),
        and only when no land-reachable target remains switch to naval —
        recon the sea, then invade discovered overseas cities. The switch is
        a generator decision (it drops the army-building baselines in naval
        mode so a naval plan isn't out-scored by faster army production in a
        12-turn playout that can't see the invasion pay off)."""
        fist = self._fist_size(view.rules)
        targets, overseas = self._partition_targets(view)
        defenses = self._threatened_home_cities(view)

        if not targets:
            # Home land exhausted → go overseas (or hold if nothing's reachable).
            naval = self._naval_plans(view, overseas)
            return (*naval, Plan(objectives=(), surplus=SurplusPolicy.RESERVE))

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

        # All-out: a fist at every known target at once. The close-out plan —
        # the Phase 15.8 stall autopsy found cap-outs were mostly *crushing*
        # endgames grinding targets one fist at a time while 20+ armies sat
        # in reserve. Playouts price it: chosen when force is overwhelming,
        # rejected when spreading thin would lose the fights.
        if len(targets) >= 3:
            plans.append(
                Plan(
                    objectives=tuple(
                        Objective(t, Role.ASSAULT, fist)
                        for t in targets[:ALL_OUT_FAN]
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

    def _partition_targets(
        self, view: WorldView
    ) -> tuple[list[Coord], list[Coord]]:
        """Split known capturable cities into (land-reachable, overseas),
        each nearest-to-home first. Land-reachable = an army can walk there
        from a home city (same landmass); overseas = everything else (needs
        a transport). Without naval the AI ignores overseas targets entirely,
        which is why it stalled once its continent was won."""
        from empire.pathfinding.cost import ARMY
        from empire.pathfinding.distance_field import DistanceField, PassabilityGrid

        home = self._home_centroid(view)
        known = [c.coord for c in view.known_enemy_cities]
        known += [c.coord for c in view.neutral_cities]
        known.sort(key=lambda c: (c.chebyshev_to(home), c.y, c.x))

        own = view.own_cities
        if not own:
            return [], known
        grid = PassabilityGrid(view.real_map(), ARMY, view.own_player.view)
        reach = DistanceField(own[0].coord, grid)
        land: list[Coord] = []
        overseas: list[Coord] = []
        for t in known:
            (land if reach.steps_to(t) is not None else overseas).append(t)
        return land, overseas

    def _naval_plans(self, view: WorldView, overseas: list[Coord]) -> list[Plan]:
        """Naval-mode candidates (home land won). Strategically committed by
        the generator: if a coastal overseas target is known, invade it;
        otherwise, if there's unexplored ocean, recon with patrols. (Not
        both — emitting recon alongside invade lets the faster-building patrol
        out-score the invasion in the playout, so the generator picks one.)"""
        coastal = [t for t in overseas if is_ocean_coastal(view, t)]
        if coastal:
            have_transport = any(
                u.kind is UnitKind.TRANSPORT for u in view.own_units
            )
            prod = UnitKind.ARMY if have_transport else UnitKind.TRANSPORT
            return [
                Plan(
                    objectives=(Objective(t, Role.INVADE, INVADE_STRENGTH),),
                    surplus=SurplusPolicy.RESERVE,
                    production=prod,
                )
                for t in coastal[:TARGET_FAN]
            ]
        if sea_frontier_cells(view):
            # Explore the ocean to find landmasses / coastal targets.
            return [
                Plan(
                    objectives=(),
                    surplus=SurplusPolicy.SCOUT,
                    production=UnitKind.PATROL,
                )
            ]
        return []

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

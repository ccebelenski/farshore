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
from empire.ai.vision import frontier_cells, sea_frontier_cells
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
# Transports to assemble for a concentrated landing. One thin wave lands
# outnumbered (~2 of ours vs ~6 defenders, per the projection probe) and is
# retaken at once; a small fleet, staged and landed together, puts enough
# ashore to win the landmass. Kept small and on ONE target — concentration,
# not a fragmenting trickle to several (which regressed projection to zero).
FLEET_TRANSPORTS = 2
# Fighters to keep on hand for recon/strike. A couple of fast, far-seeing
# scouts (scan 5, speed 8, fly anywhere) is plenty; more just starves ground
# production. The follower builds them one base at a time (see PlanFollower).
AIR_QUOTA = 2


class CandidateGenerator:
    """Proposes candidate `Plan`s from one fog view."""

    def generate(self, view: WorldView) -> tuple[Plan, ...]:
        """Candidates in a stable, deterministic order (ties in playout score
        resolve toward the earlier candidate, so order encodes a mild prior:
        aggressive single-front plans first, passivity last).

        Naval runs in *parallel* with the land game, not after it. As long as
        land-reachable targets remain we still emit the land assault plans, but
        we also offer naval candidates (recon the sea; invade a discovered
        overseas city) so the search can start projecting before the home
        continent is exhausted. The playout prices the trade — a naval plan
        spends coastal-city production on hulls instead of armies — and the
        evaluator's intel term lets a scouting plan show progress the search
        can see (without it, exploration never won a playout, so naval didn't
        begin until no land target was left and the enemy continent had gone
        undiscovered for a hundred turns). When no land target remains the
        naval plans stand alone (the early-return branch below)."""
        fist = self._fist_size(view.rules)
        targets, overseas, has_land_frontier = self._assess(view)
        defenses = self._threatened_home_cities(view)

        # No land-reachable target left → decide between going overseas and
        # finishing the home continent, by priority:
        #   1. a known coastal enemy/neutral city overseas → INVADE it now
        #      (beats exploring empty home interior — we already found a prize);
        #   2. home still has unexplored land → keep scouting it by land
        #      (there may be more cities to capture);
        #   3. nothing known overseas but open ocean remains → patrol-recon;
        #   4. truly nothing reachable → hold.
        # Naval-mode returns emit ONLY naval plans — an army-building hold would
        # out-score a slower-building patrol/transport in the 12-turn playout.
        if not targets:
            # No KNOWN land target. Empty `targets` EARLY means the home
            # continent is unexplored, NOT that it's won — so we must keep
            # scouting it on foot to find the enemy at all (the regression bug:
            # this branch used to switch straight to naval/RESERVE, so the AI
            # never explored home and turtled to defeat). Always offer land
            # exploration; naval (invade a discovered overseas city, recon the
            # sea) is ADDITIVE, never a replacement; RESERVE is the last resort.
            # `has_land_frontier` is no longer a gate — it glitches False on the
            # first turn (vision not yet populated), which locked RESERVE.
            del has_land_frontier
            scout = Plan(objectives=(), surplus=SurplusPolicy.SCOUT)
            reserve = Plan(objectives=(), surplus=SurplusPolicy.RESERVE)
            naval = [*self._invade_plans(view, overseas), *self._recon_plans(view)]
            air = self._air_plans(view)
            return (scout, *naval, *air, reserve)

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

        # Naval candidates, offered in parallel with the land plans above so
        # projection can begin before the home continent is conquered. Invade
        # a known overseas coastal city; otherwise recon the open sea to find
        # one. The search picks these only when a playout says the naval
        # trade-off (coastal production -> hulls; exploration value) beats
        # pressing the land fight.
        plans.extend(self._invade_plans(view, overseas))
        plans.extend(self._recon_plans(view))
        plans.extend(self._air_plans(view))

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

    def _assess(
        self, view: WorldView
    ) -> tuple[list[Coord], list[Coord], bool]:
        """Assess the land situation from one land flood out of a home city:
        (land-reachable targets, overseas targets, has-reachable-land-frontier).

        Land-reachable = an army can walk there (same landmass); overseas =
        everything else (needs a transport). The frontier flag is whether any
        unexplored-land edge is still reachable on foot — i.e. the home
        continent isn't fully scouted yet, so there may be more cities to
        find before going overseas."""
        from empire.pathfinding.cost import ARMY
        from empire.pathfinding.distance_field import DistanceField, PassabilityGrid

        home = self._home_centroid(view)
        known = [c.coord for c in view.known_enemy_cities]
        known += [c.coord for c in view.neutral_cities]
        known.sort(key=lambda c: (c.chebyshev_to(home), c.y, c.x))

        own = view.own_cities
        if not own:
            return [], known, False
        grid = PassabilityGrid(view.real_map(), ARMY, view.own_player.view)
        reach = DistanceField(own[0].coord, grid)

        # A city is a *land* target if any own army can walk to it — not just
        # from the home continent, but from a beachhead too: once a transport
        # lands troops overseas, the city beside them becomes a land target so
        # the massed-assault doctrine (not the invade pipeline) storms it. Add
        # one flood per landmass that holds an own army the home flood can't
        # reach (landed forces); skip armies already covered by another flood.
        fields = [reach]
        for u in view.own_units:
            if u.kind is not UnitKind.ARMY or u.carried_by is not None:
                continue
            if any(f.steps_to(u.coord) is not None for f in fields):
                continue
            fields.append(DistanceField(u.coord, grid))

        def land_reachable(c: Coord) -> bool:
            return any(f.steps_to(c) is not None for f in fields)

        # `land` = anything an army can walk to (home flood OR a beachhead) —
        # stormed on foot by the massed-assault doctrine. `overseas` = anything
        # our HOME continent can't reach, deliberately *not* excluding cities a
        # beachhead already reaches: a landmass we've landed on but not yet won
        # stays a ferry target so reinforcements keep flowing to it (holding
        # takes local superiority, which one thin wave can't deliver). Such a
        # city is then BOTH a land target (assault with what's ashore) and an
        # overseas one (ferry more) — the two plans the search weighs by the
        # local balance.
        land: list[Coord] = []
        overseas: list[Coord] = []
        for t in known:
            if land_reachable(t):
                land.append(t)
            if reach.steps_to(t) is None:
                overseas.append(t)
        has_frontier = any(
            reach.steps_to(c) is not None for c in frontier_cells(view)
        )
        return land, overseas, has_frontier

    def _invade_plans(self, view: WorldView, overseas: list[Coord]) -> list[Plan]:
        """A single INVADE plan against the nearest known coastal overseas city
        (empty if none is known).

        One target, deliberately: the follower assembles its transports into a
        fleet and lands them concentrated there. Spreading invade plans across
        several targets fragments the force and regressed projection to zero.
        Production builds hulls up to `FLEET_TRANSPORTS`, then armies to fill
        them — by the time an overseas city is known the home continent usually
        holds an army surplus, so hulls are the bottleneck, not troops."""
        coastal = [t for t in overseas if is_ocean_coastal(view, t)]
        if not coastal:
            return []
        target = coastal[0]
        n_transports = sum(
            1 for u in view.own_units if u.kind is UnitKind.TRANSPORT
        )
        prod = (
            UnitKind.TRANSPORT
            if n_transports < FLEET_TRANSPORTS
            else UnitKind.ARMY
        )
        return [
            Plan(
                objectives=(Objective(target, Role.INVADE, INVADE_STRENGTH),),
                surplus=SurplusPolicy.RESERVE,
                production=prod,
            )
        ]

    def _recon_plans(self, view: WorldView) -> list[Plan]:
        """Patrol-recon the open ocean to discover landmasses / coastal
        targets (empty if there's no unexplored sea)."""
        if sea_frontier_cells(view):
            return [
                Plan(
                    objectives=(),
                    surplus=SurplusPolicy.SCOUT,
                    production=UnitKind.PATROL,
                )
            ]
        return []

    def _air_plans(self, view: WorldView) -> list[Plan]:
        """Build a fighter for recon/strike while under the air quota and there
        is still fog worth scouting. Fighters are the AI's best scouts (scan 5,
        speed 8, fly over anything); the follower flies them (`plan_air`) and
        builds them one base at a time. Empty once the quota is met or the map
        is fully known. The search picks this only when a playout says a
        scout's intel/strike is worth a city-turn of production."""
        n_fighters = sum(
            1 for u in view.own_units if u.kind is UnitKind.FIGHTER
        )
        if n_fighters >= AIR_QUOTA:
            return []
        if not frontier_cells(view) and not sea_frontier_cells(view):
            return []
        return [
            Plan(
                objectives=(),
                surplus=SurplusPolicy.SCOUT,
                production=UnitKind.FIGHTER,
            )
        ]

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

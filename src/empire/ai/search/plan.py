"""`Plan`: one candidate course of action for `SearchAI` to evaluate.

A plan is *objective-level*, not unit-level: it names targets, roles, and
strengths, and leaves unit assignment to `PlanFollower` at execution time.
Unit-level plans would go stale inside a playout — units die and new ones
are produced over the horizon — while an objective-level plan stays
meaningful for as long as the searcher keeps choosing it. All types are
frozen values: a plan is data the search scores, never something that
mutates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from empire.core.coord import Coord
from empire.core.unit import UnitKind


class Role(Enum):
    """What the armies assigned to an objective do when they get there."""

    ASSAULT = "assault"  # march onto the target (combat/capture on entry)
    DEFEND = "defend"  # hold adjacent to the target city (§5.4: never on it)
    INVADE = "invade"  # ferry armies by transport and storm an overseas city


class PlanGoal(Enum):
    """The horizon-free strategic GOAL a plan serves, set by the generator (which
    knows the land/sea topology) and read by the scorer's split-score base
    value. Most goals are read off the objectives; SCOUT_SEA must be
    tagged because a 'press-home + scout-sea' plan looks structurally like an
    assault plan that happens to build a patrol — only the generator knows it was
    emitted to go find the enemy continent."""

    NONE = "none"
    INVADE = "invade"  # capture a city across the water (past-horizon payoff)
    SCOUT_SEA = "scout_sea"  # discover the enemy continent (concurrent with home)


class SurplusPolicy(Enum):
    """What armies left over after objective assignment do."""

    SCOUT = "scout"  # push toward the nearest fog frontier
    RESERVE = "reserve"  # hold position (stepping off friendly cities, §5.4)


@dataclass(frozen=True, slots=True)
class Objective:
    """One target with the strength committed to it.

    `strength` is the number of armies the follower assigns (nearest-first);
    fewer may be available, in which case the objective is under-crewed
    rather than dropped — the playout will price that in.
    """

    target: Coord
    role: Role
    strength: int


@dataclass(frozen=True, slots=True)
class Plan:
    """A full candidate: objectives in priority order + surplus + production.

    Objective order matters — earlier objectives take their pick of the
    nearest armies first.
    """

    objectives: tuple[Objective, ...]
    surplus: SurplusPolicy = SurplusPolicy.SCOUT
    production: UnitKind = UnitKind.ARMY
    goal: PlanGoal = PlanGoal.NONE

    @staticmethod
    def hold() -> Plan:
        """The do-nothing baseline candidate (defend in place, keep producing)."""
        return Plan(objectives=())

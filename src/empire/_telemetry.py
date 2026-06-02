"""Land-brawl telemetry: instrument StrategicAI games to answer the combined-arms
design questions (Phase 15.7 step 3) — are we sending the right-sized fists, are
they launching on time or forced early, do fighters have fuel to get home, and
is the artillery actually sparing fighters for the armies?

Reuses the arena's deterministic match builder; observes one side (the
StrategicAI) via its live `AIMemory` + the event bus. Read-only — it never
changes a decision, so the games it measures are the games we ship.

Run:  uv run python -m empire._telemetry --seeds 12 [--fortified]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from empire._arena import ARENA_PROFILE, build_land_brawl
from empire.ai.baseline.controller import BaselineAI
from empire.ai.strategic.ai import StrategicAI
from empire.ai.strategic.campaign import PREP_DEADLINE
from empire.ai.strategic.goals import CaptureCityGoal
from empire.ai.strategic.operational import TaskForceState
from empire.core.events import CityFiredEvent
from empire.core.ruleset import FORTIFIED_CITIES, STANDARD, RuleSet
from empire.core.unit import UnitKind

_SAMPLE_TURNS = (20, 50, 100)


def _int_dict() -> dict[int, int]:
    return {}


def _str_dict() -> dict[str, int]:
    return {}


@dataclass
class Stats:
    games: int = 0
    # Launches (FORMING -> EN_ROUTE): one record per assault that set out.
    launches: int = 0
    launch_armies: int = 0
    launch_fighters: int = 0
    launch_forming_turns: int = 0
    launch_deadline_forced: int = 0
    launch_solo: int = 0  # launched with a single unit (a trickle, not a fist)
    # Defended-capture launches only (the real fists — the trickle that matters,
    # isolated from soft-grab walk-ins that *should* be solo).
    def_launches: int = 0
    def_armies: int = 0
    def_solo: int = 0
    def_deadline: int = 0
    # Formation coherence: max pairwise spread of an EN_ROUTE/ENGAGED force.
    spread_samples: int = 0
    spread_total: int = 0
    # Fighter fuel health (per fighter-turn).
    fighter_turns: int = 0
    fighter_lowfuel_turns: int = 0  # range <= 3
    fighter_stranded_turns: int = 0  # low fuel AND no friendly city in range
    # Artillery target discipline.
    shots_at_army: int = 0
    shots_at_fighter: int = 0
    shots_at_other: int = 0
    # Loss timing: our city count sampled at fixed turns + at end.
    cities_at: dict[int, int] = field(default_factory=_int_dict)
    cities_at_n: dict[int, int] = field(default_factory=_int_dict)
    outcomes: dict[str, int] = field(default_factory=_str_dict)


def _play(seed: int, cap: int, rules: RuleSet, strategic_first: bool, st: Stats) -> None:
    built = build_land_brawl(ARENA_PROFILE, seed, rules)
    if built is None:
        return
    game, players = built
    si = 0 if strategic_first else 1
    strat = StrategicAI()
    game.attach_controller(players[si].id, strat)
    game.attach_controller(players[1 - si].id, BaselineAI())
    me = players[si]

    kind: dict[int, UnitKind] = {}

    def on_fired(e: CityFiredEvent) -> None:
        k = kind.get(int(e.target_id))
        if k is UnitKind.ARMY:
            st.shots_at_army += 1
        elif k is UnitKind.FIGHTER:
            st.shots_at_fighter += 1
        else:
            st.shots_at_other += 1

    game.event_bus.subscribe(CityFiredEvent, on_fired)

    artillery = rules.city_artillery_range > 0

    st.games += 1
    prev_state: dict[int, TaskForceState] = {}
    for _ in range(cap):
        # Snapshot kinds before the turn so artillery/launch lookups resolve.
        for u in game.map.all_units():
            kind[int(u.id)] = u.kind
        game.run_turn()

        coord = {int(u.id): u.coord for u in game.map.all_units()}
        ukind = {int(u.id): u.kind for u in game.map.all_units()}
        for tf in strat.memory.task_forces:
            tid = int(tf.id)
            was = prev_state.get(tid)
            if was is TaskForceState.FORMING and tf.state is TaskForceState.EN_ROUTE:
                a = sum(1 for x in tf.unit_ids if ukind.get(int(x)) is UnitKind.ARMY)
                f = sum(1 for x in tf.unit_ids if ukind.get(int(x)) is UnitKind.FIGHTER)
                forming = game.turn - tf.created_turn
                st.launches += 1
                st.launch_armies += a
                st.launch_fighters += f
                st.launch_forming_turns += forming
                if forming >= PREP_DEADLINE:
                    st.launch_deadline_forced += 1
                if len(tf.unit_ids) <= 1:
                    st.launch_solo += 1
                # Defended capture = a real fist (enemy city, or any city when
                # artillery is on); soft neutral walk-ins are excluded.
                if isinstance(tf.goal, CaptureCityGoal):
                    target = next(
                        (c for c in game.map.cities()
                         if int(c.id) == int(tf.goal.target_city_id)),
                        None,
                    )
                    defended = artillery or (target is not None and target.owner is not None)
                    if defended:
                        st.def_launches += 1
                        st.def_armies += a
                        if forming >= PREP_DEADLINE:
                            st.def_deadline += 1
                        if len(tf.unit_ids) <= 1:
                            st.def_solo += 1
            if tf.state in (TaskForceState.EN_ROUTE, TaskForceState.ENGAGED):
                pts = [coord[int(x)] for x in tf.unit_ids if int(x) in coord]
                if len(pts) >= 2:
                    spread = max(p.chebyshev_to(q) for p in pts for q in pts)
                    st.spread_samples += 1
                    st.spread_total += spread
            prev_state[tid] = tf.state

        my_cities = [c for c in game.map.cities() if c.owner is me]
        city_coords = [c.coord for c in my_cities]
        for fighter in (u for u in game.map.all_units()
                        if u.owner is me and u.kind is UnitKind.FIGHTER):
            st.fighter_turns += 1
            if fighter.range <= 3:
                st.fighter_lowfuel_turns += 1
                home = min((fighter.coord.chebyshev_to(c) for c in city_coords),
                           default=10**9)
                if home > fighter.range:
                    st.fighter_stranded_turns += 1
        if game.turn in _SAMPLE_TURNS:
            st.cities_at[game.turn] = st.cities_at.get(game.turn, 0) + len(my_cities)
            st.cities_at_n[game.turn] = st.cities_at_n.get(game.turn, 0) + 1
        if game.is_over():
            break

    final = sum(1 for c in game.map.cities() if c.owner is me)
    st.cities_at[-1] = st.cities_at.get(-1, 0) + final
    st.cities_at_n[-1] = st.cities_at_n.get(-1, 0) + 1
    if not game.is_over():
        outcome = "unfinished"
    else:
        w = game.winner()
        outcome = "strategic" if w is me else "baseline" if w is not None else "draw"
    st.outcomes[outcome] = st.outcomes.get(outcome, 0) + 1


def _report(st: Stats, rules: RuleSet) -> None:
    def avg(num: int, den: int) -> float:
        return num / den if den else 0.0

    print(f"\n=== telemetry: {rules.name}, {st.games} games ===")
    print(f"outcomes: {st.outcomes}")
    print(f"launches: {st.launches} "
          f"({avg(st.launches, st.games):.1f}/game)")
    print(f"  mean size: {avg(st.launch_armies, st.launches):.1f} armies + "
          f"{avg(st.launch_fighters, st.launches):.1f} fighters")
    print(f"  mean turns forming: {avg(st.launch_forming_turns, st.launches):.1f} "
          f"(PREP_DEADLINE={PREP_DEADLINE})")
    print(f"  deadline-forced: {avg(st.launch_deadline_forced, st.launches):.0%}; "
          f"solo (trickle): {avg(st.launch_solo, st.launches):.0%}")
    print(f"  DEFENDED fists only ({st.def_launches}): "
          f"{avg(st.def_armies, st.def_launches):.1f} armies, "
          f"{avg(st.def_solo, st.def_launches):.0%} solo, "
          f"{avg(st.def_deadline, st.def_launches):.0%} deadline-forced")
    print(f"formation spread (en-route/engaged): "
          f"{avg(st.spread_total, st.spread_samples):.1f} cells mean max-pairwise")
    print(f"fighter fuel: {avg(st.fighter_lowfuel_turns, st.fighter_turns):.0%} "
          f"of fighter-turns low (<=3); "
          f"{avg(st.fighter_stranded_turns, st.fighter_turns):.0%} stranded "
          f"(low + no city in range) over {st.fighter_turns} fighter-turns")
    arty = st.shots_at_army + st.shots_at_fighter + st.shots_at_other
    print(f"artillery target split (n={arty}): "
          f"army {avg(st.shots_at_army, arty):.0%}, "
          f"fighter {avg(st.shots_at_fighter, arty):.0%}, "
          f"other {avg(st.shots_at_other, arty):.0%}")
    traj = ", ".join(
        f"t{t}: {avg(st.cities_at.get(t, 0), st.cities_at_n.get(t, 0)):.1f}"
        for t in (*_SAMPLE_TURNS, -1)
    )
    print(f"our mean city count — {traj}  (t-1 = game end)")


def main() -> None:
    parser = argparse.ArgumentParser(description="StrategicAI land-brawl telemetry")
    parser.add_argument("--seeds", type=int, default=12)
    parser.add_argument("--cap", type=int, default=250)
    parser.add_argument("--fortified", action="store_true")
    args = parser.parse_args()
    rules = FORTIFIED_CITIES if args.fortified else STANDARD

    st = Stats()
    for seed in range(args.seeds):
        for strategic_first in (True, False):
            _play(seed, args.cap, rules, strategic_first, st)
    _report(st, rules)


if __name__ == "__main__":
    main()

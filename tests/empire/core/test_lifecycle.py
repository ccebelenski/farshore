"""Phase 10.8 — deferred unit lifecycle: fighter fuel (§3.5), satellite
orbit/lifetime (§2.4), and repair in friendly cities (§2.3)."""

from __future__ import annotations

import random

import pytest

from empire.combat.resolver import CombatResolver
from empire.core.city import City
from empire.core.coord import Coord, Direction
from empire.core.engine import (
    MoveOutcome,
    StepOutcome,
    advance_satellites,
    crash_out_of_fuel_fighters,
    execute_unit_path,
    repair_in_cities,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.unit import Army, Carrier, Destroyer, Fighter, Satellite
from tests.empire.support import build_map as _build_map

# --- helpers -----------------------------------------------------------------


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


@pytest.fixture()
def resolver() -> CombatResolver:
    return CombatResolver()


def _fly(
    fighter: Fighter, m: Map, resolver: CombatResolver, *cells: Coord
) -> MoveOutcome:
    return execute_unit_path(
        unit=fighter,
        path=tuple((c.x, c.y) for c in cells),
        real_map=m,
        rules=STANDARD,
        combat_resolver=resolver,
        rng=random.Random(0),
    )


# --- fighter fuel ------------------------------------------------------------


def test_fighter_burns_one_fuel_per_cell(p1: Player, resolver: CombatResolver) -> None:
    m = _build_map(["WWWWW"])
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 5
    m.place_unit(fighter, Coord(0, 0))

    _fly(fighter, m, resolver, Coord(1, 0), Coord(2, 0), Coord(3, 0))

    assert fighter.coord == Coord(3, 0)
    assert fighter.range == 2


def test_fighter_refuels_landing_on_friendly_city(
    p1: Player, resolver: CombatResolver
) -> None:
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p1)
    m = _build_map(["WC"], cities={Coord(1, 0): city})
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 3
    m.place_unit(fighter, Coord(0, 0))

    _fly(fighter, m, resolver, Coord(1, 0))

    assert fighter.coord == Coord(1, 0)
    assert fighter.range == STANDARD.fighter_base_range  # refuelled


def test_fighter_refuels_when_loading_onto_carrier(
    p1: Player, resolver: CombatResolver
) -> None:
    m = _build_map(["WW"])
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 2
    carrier = Carrier(UnitId(2), p1, Coord(1, 0))
    m.place_unit(fighter, Coord(0, 0))
    m.place_unit(carrier, Coord(1, 0))

    outcome = _fly(fighter, m, resolver, Coord(1, 0))

    assert outcome.last_outcome is StepOutcome.LOADED
    assert fighter.range == STANDARD.fighter_base_range


def test_out_of_fuel_fighter_over_water_crashes(p1: Player) -> None:
    m = _build_map(["WW"])
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 0
    m.place_unit(fighter, Coord(0, 0))

    crashed = crash_out_of_fuel_fighters(m, STANDARD)

    assert crashed == (UnitId(1),)
    assert m.unit_by_id(UnitId(1)) is None


def test_out_of_fuel_fighter_on_friendly_city_survives(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _build_map(["C"], cities={Coord(0, 0): city})
    fighter = Fighter(UnitId(1), p1, Coord(0, 0))
    fighter.range = 0
    m.place_unit(fighter, Coord(0, 0))

    crashed = crash_out_of_fuel_fighters(m, STANDARD)

    assert crashed == ()
    assert m.unit_by_id(UnitId(1)) is fighter


# --- satellite orbit / lifetime ----------------------------------------------


def test_satellite_orbits_one_cell_per_round(p1: Player) -> None:
    m = _build_map(["LLLLL"])
    sat = Satellite(UnitId(1), p1, Coord(1, 0))
    sat.orbit_direction = Direction.E  # launched
    sat.range = 10
    m.place_unit(sat, Coord(1, 0))

    moved, deorbited = advance_satellites(m)

    assert moved == (UnitId(1),)
    assert deorbited == ()
    assert sat.coord == Coord(2, 0)  # stepped east
    assert sat.orbit_direction is Direction.E
    assert sat.range == 9


def test_satellite_wraps_around_the_map_edge(p1: Player) -> None:
    """A simulated orbit: the heading NEVER changes; the world wraps."""
    m = _build_map(["LLL"])  # width 3; sat at the east edge heading east
    sat = Satellite(UnitId(1), p1, Coord(2, 0))
    sat.orbit_direction = Direction.E
    sat.range = 10
    m.place_unit(sat, Coord(2, 0))

    advance_satellites(m)

    # Wrapped to the west edge, still heading east.
    assert sat.orbit_direction is Direction.E
    assert sat.coord == Coord(0, 0)


def test_unlaunched_satellite_decays_in_place(p1: Player) -> None:
    """No launch direction chosen yet: it doesn't move, but the fuel clock
    runs from production — a parked satellite is not a free radar tower."""
    m = _build_map(["LLL"])
    sat = Satellite(UnitId(1), p1, Coord(1, 0))
    assert sat.orbit_direction is None  # produced unlaunched
    sat.range = 2
    m.place_unit(sat, Coord(1, 0))

    moved, deorbited = advance_satellites(m)
    assert moved == () and deorbited == ()
    assert sat.coord == Coord(1, 0)
    assert sat.range == 1

    _, deorbited = advance_satellites(m)
    assert deorbited == (UnitId(1),)  # crashed without ever launching
    assert m.unit_by_id(UnitId(1)) is None  # deorbit removes it from the map


def test_satellite_cannot_be_attacked(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    m = _build_map(["LL"])
    army = Army(UnitId(1), p1, Coord(0, 0))
    enemy_sat = Satellite(UnitId(2), p2, Coord(1, 0))
    m.place_unit(army, Coord(0, 0))
    m.place_unit(enemy_sat, Coord(1, 0))

    outcome = execute_unit_path(
        unit=army,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=resolver,
        rng=random.Random(0),
    )

    # No combat: the army moves onto the cell and the satellite is untouched.
    assert outcome.last_outcome is StepOutcome.OK
    assert army.coord == Coord(1, 0)
    assert m.unit_by_id(UnitId(2)) is enemy_sat


# --- repair ------------------------------------------------------------------


def test_stationary_unit_repairs_in_friendly_city(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _build_map(["C"], cities={Coord(0, 0): city})
    dest = Destroyer(UnitId(1), p1, Coord(0, 0))
    dest.hits = 1  # max_hits 3
    m.place_unit(dest, Coord(0, 0))

    repaired = repair_in_cities(m)

    assert repaired == (UnitId(1),)
    assert dest.hits == 2


def test_unit_does_not_repair_after_moving(p1: Player, resolver: CombatResolver) -> None:
    # A damaged destroyer sails from water into a friendly port city this
    # round: it began the turn outside the city, so it does not repair (spec
    # §2.3 requires beginning *and* ending stationary in the city).
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p1)
    m = _build_map(["WC"], cities={Coord(1, 0): city})
    dest = Destroyer(UnitId(1), p1, Coord(0, 0))
    dest.hits = 1
    m.place_unit(dest, Coord(0, 0))

    execute_unit_path(
        unit=dest,
        path=((1, 0),),
        real_map=m,
        rules=STANDARD,
        combat_resolver=resolver,
        rng=random.Random(0),
    )
    repaired = repair_in_cities(m)

    assert UnitId(1) not in repaired  # moved into the city this round
    assert dest.hits == 1


def test_unit_does_not_repair_in_unowned_city(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p2)  # enemy-owned
    m = _build_map(["C"], cities={Coord(0, 0): city})
    dest = Destroyer(UnitId(1), p1, Coord(0, 0))
    dest.hits = 1
    m.place_unit(dest, Coord(0, 0))

    repaired = repair_in_cities(m)

    assert repaired == ()
    assert dest.hits == 1


def test_repair_caps_at_max_hits(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _build_map(["C"], cities={Coord(0, 0): city})
    dest = Destroyer(UnitId(1), p1, Coord(0, 0))  # full HP (3)
    m.place_unit(dest, Coord(0, 0))

    repaired = repair_in_cities(m)

    assert repaired == ()
    assert dest.hits == type(dest).max_hits


def test_satellite_production_launch_defaults(p1: Player, p2: Player) -> None:
    """A human-owned city produces an UNLAUNCHED satellite (the TUI prompts
    for the orbit); an AI owner auto-launches east."""
    from empire.core.city import City
    from empire.core.engine import run_production_tick
    from empire.core.identity import CityId
    from empire.core.ruleset import STANDARD
    from empire.core.unit import UnitKind

    m = _build_map(["LLL"])
    ids = iter(range(1, 10))

    for player, expected in ((p1, None), (p2, Direction.E)):  # p1 human, p2 AI
        city = City(id=CityId(int(player.id)), coord=Coord(1, 0), owner=player)
        m._tiles[Coord(1, 0)] = type(m.tile(Coord(1, 0)))(
            coord=Coord(1, 0), terrain=m.tile(Coord(1, 0)).terrain, city=city
        )
        city.production.building = UnitKind.SATELLITE
        city.production.work = Satellite.build_time - 1
        produced = run_production_tick(
            player, m, STANDARD, lambda: UnitId(next(ids))
        )
        assert len(produced) == 1
        assert produced[0].orbit_direction == expected
        m.remove_unit(produced[0])


def test_produced_unit_is_stamped_with_its_home_city(p1: Player) -> None:
    """A unit rolling off the line carries its city's name as `home_city`
    (cosmetic birthplace stamp)."""
    from empire.core.city import City
    from empire.core.engine import run_production_tick
    from empire.core.identity import CityId
    from empire.core.ruleset import STANDARD
    from empire.core.unit import Army, UnitKind

    m = _build_map(["LLL"])
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p1, name="Cape Mercy")
    m._tiles[Coord(1, 0)] = type(m.tile(Coord(1, 0)))(  # pyright: ignore[reportPrivateUsage]
        coord=Coord(1, 0), terrain=m.tile(Coord(1, 0)).terrain, city=city
    )
    city.production.building = UnitKind.ARMY
    city.production.work = Army.build_time - 1
    produced = run_production_tick(p1, m, STANDARD, lambda: UnitId(1))
    assert len(produced) == 1
    assert produced[0].home_city == "Cape Mercy"


def test_satellite_is_never_path_movable(p1: Player) -> None:
    """Engine-level lockout: satellites cannot be single-moved by any path
    (manual step, goto, heading, AI plan) — orbit is the only motion."""
    from empire.core.engine import is_legal_step
    from empire.core.ruleset import STANDARD

    m = _build_map(["LLL"])
    sat = Satellite(UnitId(1), p1, Coord(0, 0))
    m.place_unit(sat, Coord(0, 0))
    assert not is_legal_step(sat, Coord(1, 0), m, STANDARD)

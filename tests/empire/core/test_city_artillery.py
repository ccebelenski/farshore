"""City artillery: the FortifiedCities defensive mechanic (spec §4.7).

Cities fire a single-target ranged salvo (range 2, chance to hit, chance to
pin) once per round. These tests pin down the rules in isolation — targeting
priority, range, hit/miss, pinning, the one-shot cadence, neutral-city fire,
and the round-level reset — using deterministic RNG seeds.
"""

from __future__ import annotations

import random

import pytest

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.engine import (
    ArtilleryOutcome,
    city_can_fire_at,
    clear_movement_pins,
    execute_city_artillery,
    reset_city_artillery,
    resolve_city_artillery,
    step_would_enter_artillery_zone,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD, RuleSet
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Destroyer, Fighter, Unit

_TERRAIN = {"L": TerrainKind.LAND, "W": TerrainKind.WATER, "C": TerrainKind.CITY}


def _map(rows: list[str], cities: dict[Coord, City] | None = None) -> Map:
    cities = cities or {}
    height, width = len(rows), len(rows[0])
    tiles: dict[Coord, Tile] = {}
    for y in range(height):
        for x in range(width):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=_TERRAIN[rows[y][x]])
    return Map(width=width, height=height, tiles=tiles)


# A fortified ruleset that always hits and always pins, so outcomes are
# deterministic regardless of RNG. Individual tests override the probabilities
# when they need to exercise the miss / no-pin branches.
FORT = RuleSet(
    name="TEST_FORT",
    map_profile=STANDARD.map_profile,
    army_capture_city_deterministic=True,
    city_artillery_range=2,
    city_artillery_hit_prob=1.0,
    city_artillery_pin_prob=1.0,
)


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


def _place(m: Map, u: Unit) -> Unit:
    m.place_unit(u, u.coord)
    return u


def _rng() -> random.Random:
    return random.Random(0)


# --- eligibility: range, ownership, the spent shot ---------------------------


def test_can_fire_on_enemy_within_range(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(2, 0)))  # distance 2 == range
    assert city_can_fire_at(city, army, FORT)


def test_cannot_fire_out_of_range(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(3, 0)))  # distance 3 > range 2
    assert not city_can_fire_at(city, army, FORT)


def test_cannot_fire_on_own_unit(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    own = _place(m, Army(UnitId(1), p1, Coord(1, 0)))
    assert not city_can_fire_at(city, own, FORT)


def test_disabled_ruleset_never_fires(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    assert not city_can_fire_at(city, army, STANDARD)  # range 0
    assert (
        execute_city_artillery(city, m, STANDARD, _rng()).outcome
        is ArtilleryOutcome.DISABLED
    )


def test_one_shot_per_round(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLL"], cities={Coord(0, 0): city})
    _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    _place(m, Army(UnitId(2), p2, Coord(2, 0)))

    first = execute_city_artillery(city, m, FORT, _rng())
    assert first.outcome is ArtilleryOutcome.TARGET_DESTROYED
    # Shot spent: a second call this round is refused.
    assert not city.artillery_ready
    second = execute_city_artillery(city, m, FORT, _rng())
    assert second.outcome is ArtilleryOutcome.NOT_READY


# --- targeting priority: army before fighter before everything else ----------


def test_targets_army_over_closer_fighter(p1: Player, p2: Player) -> None:
    """An army is the only unit that captures, so it is shelled first even when
    a fighter sits closer."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLL"], cities={Coord(0, 0): city})
    fighter = _place(m, Fighter(UnitId(1), p2, Coord(1, 0)))  # closer
    army = _place(m, Army(UnitId(2), p2, Coord(2, 0)))  # farther, higher priority

    result = execute_city_artillery(city, m, FORT, _rng())
    assert result.outcome is ArtilleryOutcome.TARGET_DESTROYED
    assert result.target_id == army.id
    assert m.unit_by_id(army.id) is None
    assert m.unit_by_id(fighter.id) is not None  # fighter untouched


def test_no_target_when_range_empty(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLL"], cities={Coord(0, 0): city})
    assert (
        execute_city_artillery(city, m, FORT, _rng()).outcome
        is ArtilleryOutcome.NO_TARGET
    )


# --- hit / miss / pin --------------------------------------------------------


def test_hit_kills_one_hp_unit_and_pins(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))

    result = execute_city_artillery(city, m, FORT, _rng())
    assert result.outcome is ArtilleryOutcome.TARGET_DESTROYED
    assert m.unit_by_id(army.id) is None


def test_miss_still_pins_and_spends_shot(p1: Player, p2: Player) -> None:
    """pin always, hit never: the army survives but is pinned and the shot is
    spent — the suppression effect that stops a lone attacker reaching a city."""
    rules = RuleSet(
        name="MISS_BUT_PIN",
        map_profile=STANDARD.map_profile,
        city_artillery_range=2,
        city_artillery_hit_prob=0.0,
        city_artillery_pin_prob=1.0,
    )
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))

    result = execute_city_artillery(city, m, rules, _rng())
    assert result.outcome is ArtilleryOutcome.MISSED
    assert m.unit_by_id(army.id) is not None  # survived
    assert army.pinned
    assert army.moves_this_turn() == 0  # land unit fully pinned
    assert not city.artillery_ready  # shot spent on a miss


def test_no_pin_when_pin_prob_zero(p1: Player, p2: Player) -> None:
    rules = RuleSet(
        name="HIT_NO_PIN",
        map_profile=STANDARD.map_profile,
        city_artillery_range=2,
        city_artillery_hit_prob=0.0,  # survive so we can read .pinned
        city_artillery_pin_prob=0.0,
    )
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))

    execute_city_artillery(city, m, rules, _rng())
    assert not army.pinned
    assert army.moves_this_turn() == Army.speed  # unhindered


def test_naval_pin_is_halved_not_zeroed(p1: Player, p2: Player) -> None:
    """Land/air pinned units lose all movement; naval keeps half (rounded
    down)."""
    rules = RuleSet(
        name="MISS_BUT_PIN",
        map_profile=STANDARD.map_profile,
        city_artillery_range=2,
        city_artillery_hit_prob=0.0,
        city_artillery_pin_prob=1.0,
    )
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CW"], cities={Coord(0, 0): city})
    dd = _place(m, Destroyer(UnitId(1), p2, Coord(1, 0)))
    full = Destroyer.speed

    execute_city_artillery(city, m, rules, _rng())
    assert dd.pinned
    assert dd.moves_this_turn() == full // 2


# --- neutral cities fire too -------------------------------------------------


def test_neutral_city_fires_on_any_player(p2: Player) -> None:
    """A neutral city (owner None) is hostile to everyone — 'neutral, not
    lame.'"""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=None)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    assert city_can_fire_at(city, army, FORT)
    result = execute_city_artillery(city, m, FORT, _rng())
    assert result.outcome is ArtilleryOutcome.TARGET_DESTROYED
    assert m.unit_by_id(army.id) is None


# --- the volley routine (opening barrage + per-segment) ----------------------


def test_volley_from_all_cities_in_range(p1: Player, p2: Player) -> None:
    """A unit inside two cities' range is shelled by the volley; the first
    salvo (hit_prob 1.0) destroys it, so it's gone afterwards."""
    c1 = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    c2 = City(id=CityId(2), coord=Coord(2, 0), owner=None)
    m = _map(["CLC"], cities={Coord(0, 0): c1, Coord(2, 0): c2})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))  # in range of both

    results = resolve_city_artillery(m, FORT, _rng())
    # resolve_city_artillery returns (city_id, result, target_coord) triples.
    assert any(
        result.outcome is ArtilleryOutcome.TARGET_DESTROYED
        for _city_id, result, _coord in results
    )
    assert m.unit_by_id(army.id) is None


def test_volley_disabled_under_standard(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    assert resolve_city_artillery(m, STANDARD, _rng()) == []


def test_volley_restricted_to_target_owner(p1: Player, p2: Player) -> None:
    """`target_owner=P` (the per-segment defensive volley) fires only at P's
    units — a closer enemy of another player is ignored, because a city shells
    the player whose segment just resolved, not whoever is nearest."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=None)  # neutral: hostile to both
    m = _map(["CLL"], cities={Coord(0, 0): city})
    p1_army = _place(m, Army(UnitId(1), p1, Coord(1, 0)))  # closer
    p2_army = _place(m, Army(UnitId(2), p2, Coord(2, 0)))  # farther

    resolve_city_artillery(m, FORT, _rng(), target_owner=p2)
    assert m.unit_by_id(p2_army.id) is None  # p2's unit shelled
    assert m.unit_by_id(p1_army.id) is not None  # closer p1 unit ignored


def test_volley_reveals_gun_to_victim(p1: Player, p2: Player) -> None:
    """Every fire path reveals the firing city to the victim's owner — the
    invariant now lives in `_fire_artillery`, so the volley reveals too."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=None)
    m = _map(["CL"], cities={Coord(0, 0): city})
    _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    assert not p2.view.seen(city.coord)

    resolve_city_artillery(m, FORT, _rng())
    assert p2.view.seen(city.coord)


# --- auto-move halts at the zone edge (never sleepwalks into range) ----------


def test_step_into_zone_is_blocked_from_outside(p1: Player, p2: Player) -> None:
    """Stepping from outside a DISCOVERED hostile city's range to inside it
    is blocked — the guard that stops an order at the red-zone edge (§4.7)."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p2)
    m = _map(["CLLLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p1, Coord(3, 0)))  # chebyshev 3: outside
    p1.view.visible = {Coord(0, 0)}  # the fort is discovered
    # (3,0)->(2,0): chebyshev 2 == range -> entering.
    assert step_would_enter_artillery_zone(army, Coord(2, 0), m, FORT)


def test_undiscovered_ring_gives_no_clairvoyant_halt(
    p1: Player, p2: Player
) -> None:
    """Fog-honest guard: a fort the player has NEVER SEEN projects no halt —
    auto-moves may walk into its (unknown) ring; the scan on arrival
    discovers the fort and the discovery wake takes over (playtest report:
    an explorer woke at a ring edge with no red showing)."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p2)
    m = _map(["CLLLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p1, Coord(3, 0)))
    assert not p1.view.seen(Coord(0, 0))  # fort undiscovered
    assert not step_would_enter_artillery_zone(army, Coord(2, 0), m, FORT)


def test_step_outside_to_outside_not_blocked(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p2)
    m = _map(["CLLLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p1, Coord(4, 0)))  # chebyshev 4
    p1.view.visible = {Coord(0, 0)}
    # (4,0)->(3,0): chebyshev 3, still outside range 2.
    assert not step_would_enter_artillery_zone(army, Coord(3, 0), m, FORT)


def test_move_within_zone_not_blocked(p1: Player, p2: Player) -> None:
    """A unit ALREADY inside range may keep moving — only the outside→inside
    transition halts an order, not movement within the zone."""
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p2)
    m = _map(["CLLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p1, Coord(2, 0)))  # already in range (2)
    p1.view.visible = {Coord(0, 0)}
    assert not step_would_enter_artillery_zone(army, Coord(1, 0), m, FORT)


def test_own_city_zone_does_not_block(p1: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CLLLL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p1, Coord(3, 0)))
    # Own city's guns are friendly — never a movement barrier.
    assert not step_would_enter_artillery_zone(army, Coord(2, 0), m, FORT)


# --- round-level housekeeping ------------------------------------------------


def test_reset_rearms_all_cities(p1: Player) -> None:
    c1 = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    c2 = City(id=CityId(2), coord=Coord(2, 0), owner=None)
    m = _map(["CLC"], cities={Coord(0, 0): c1, Coord(2, 0): c2})
    c1.artillery_ready = False
    c2.artillery_ready = False

    reset_city_artillery(m)
    assert c1.artillery_ready
    assert c2.artillery_ready


def test_clear_pins_restores_movement(p1: Player, p2: Player) -> None:
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m = _map(["CL"], cities={Coord(0, 0): city})
    army = _place(m, Army(UnitId(1), p2, Coord(1, 0)))
    army.pinned = True
    assert army.moves_this_turn() == 0

    clear_movement_pins(m)
    assert not army.pinned
    assert army.moves_this_turn() == Army.speed


def test_destroyed_unit_reveals_the_firing_city() -> None:
    """A unit shelled to death still reveals the firing city to its owner.

    Reactive fire and the opening barrage resolve BEFORE the scan phase, and a
    destroyed unit scans nothing — so without an explicit reveal you die to a
    city you never see on the map (playtest report). Being shelled tells you
    where the gun is."""
    from empire.contracts.controller import NullController
    from empire.core.game import Game

    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap(), color="red")
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap(), color="blue")
    ncoord, p1c, p2c = Coord(2, 0), Coord(4, 4), Coord(0, 4)
    cities = {
        ncoord: City(id=CityId(1), coord=ncoord, owner=None),  # neutral gun
        p1c: City(id=CityId(2), coord=p1c, owner=p1),          # keeps P1 alive
        p2c: City(id=CityId(3), coord=p2c, owner=p2),
    }
    real_map = _map(["LLLLL"] * 5, cities=cities)
    army = Army(UnitId(1), p1, Coord(0, 0))  # chebyshev 2 from the neutral = in range
    army.hits = 1
    real_map.place_unit(army, Coord(0, 0))
    game = Game(rules=FORT, real_map=real_map, players=[p1, p2], seed=1)
    game.attach_controller(p1.id, NullController())
    game.attach_controller(p2.id, NullController())

    assert not p1.view.seen(ncoord)  # gun unseen at the start
    game.run_turn()
    assert game.map.unit_by_id(UnitId(1)) is None, "1-HP army should be shelled to death"
    assert p1.view.seen(ncoord), "a shelled-to-death unit must still reveal the firing city"


def test_crossing_between_overlapping_rings_still_warns(
    p1: Player, p2: Player
) -> None:
    """The edge test is per CITY: standing inside one discovered ring does not
    license sleepwalking into a DIFFERENT discovered city's ring (overlapping
    rings used to merge into one zone — orders crossed unwarned and got
    shelled; playtest report)."""
    a = City(id=CityId(1), coord=Coord(0, 0), owner=p2)
    b = City(id=CityId(2), coord=Coord(5, 0), owner=p2)
    m = _map(["CLLLLC"], cities={Coord(0, 0): a, Coord(5, 0): b})
    army = _place(m, Army(UnitId(1), p1, Coord(2, 0)))  # in A's ring only
    p1.view.visible = {Coord(0, 0), Coord(5, 0)}  # both forts discovered

    # (2,0)->(3,0): leaves nothing (still within A's contiguous coverage
    # under the old union test) but ENTERS B's ring (dist 2, from dist 3):
    # per-city semantics must warn.
    assert step_would_enter_artillery_zone(army, Coord(3, 0), m, FORT)

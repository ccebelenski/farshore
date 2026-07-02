"""Tests for `StandingOrder` value types and the engine's per-turn step."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from empire.combat.resolver import CombatResolver
from empire.core.city import City
from empire.core.coord import Coord, Direction
from empire.core.engine import (
    apply_standing_orders,
    enemy_in_scan_range,
    load_adjacent_cargo,
    wake_sentried_units,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.standing_order import Heading, Loading, PatrolPath, Sentry
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Patrol, Transport
from tests.empire.support import build_map as _mixed_map
from tests.empire.support import land_map as _land_map


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


@pytest.fixture()
def resolver() -> CombatResolver:
    return CombatResolver()


# --- PatrolPath state machine -----------------------------------------------


def test_patrol_path_one_shot_exhausts() -> None:
    cells = (Coord(1, 0), Coord(2, 0), Coord(3, 0))
    p = PatrolPath.new(cells)
    p2 = p.after_step()
    assert p2 is not None and p2.remaining == cells[1:]
    p3 = p2.after_step()
    assert p3 is not None and p3.remaining == cells[2:]
    p4 = p3.after_step()
    assert p4 is None  # exhausted


def test_patrol_path_loop_rearms_the_cycle() -> None:
    """A looping patrol re-arms with the SAME cycle when the pass empties
    (the cycle's last cell is the start, adjacent to its first cell — so the
    next pass begins with a legal step, never a step into the unit's own
    cell, which the old reverse-on-end flip did)."""
    cycle = (Coord(1, 0), Coord(2, 0), Coord(1, 0), Coord(0, 0))  # A=(0,0)<->B=(2,0)
    p = PatrolPath.new(cycle, loop=True)
    for expected in cycle:
        assert p is not None and p.next_cell() == expected
        p = p.after_step()
    # Pass exhausted -> re-armed, same cycle from the top.
    assert p is not None
    assert p.remaining == cycle
    assert p.loop is True


# --- Heading: walks one cell per turn ---------------------------------------


def test_heading_walks_one_cell_per_call(
    p1: Player, resolver: CombatResolver
) -> None:
    m = _land_map(5, 1)
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    army.standing_order = Heading(Direction.E)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert result.moved_unit_ids == (UnitId(1),)
    assert army.coord == Coord(1, 0)
    assert isinstance(army.standing_order, Heading)  # persists


def test_heading_clears_at_map_edge(
    p1: Player, resolver: CombatResolver
) -> None:
    m = _land_map(3, 1)
    army = Army(UnitId(1), p1, Coord(2, 0))
    m.place_unit(army, Coord(2, 0))
    army.standing_order = Heading(Direction.E)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert UnitId(1) in result.interrupted_unit_ids
    assert army.standing_order is None
    assert army.coord == Coord(2, 0)


def test_heading_clears_on_illegal_terrain(
    p1: Player, resolver: CombatResolver
) -> None:
    # Army headed east into water.
    m = _mixed_map(["LLW"])
    army = Army(UnitId(1), p1, Coord(1, 0))
    m.place_unit(army, Coord(1, 0))
    army.standing_order = Heading(Direction.E)

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.standing_order is None
    assert army.coord == Coord(1, 0)


def test_heading_clears_on_friendly_block(
    p1: Player, resolver: CombatResolver
) -> None:
    m = _land_map(3, 1)
    a1 = Army(UnitId(1), p1, Coord(0, 0))
    a2 = Army(UnitId(2), p1, Coord(1, 0))
    m.place_unit(a1, Coord(0, 0))
    m.place_unit(a2, Coord(1, 0))
    a1.standing_order = Heading(Direction.E)

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert a1.standing_order is None
    assert a1.coord == Coord(0, 0)


def test_heading_interrupts_after_step_when_enemy_in_scan(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    # Army scan_range = 2. Enemy placed within 2 of the post-step position.
    m = _land_map(10, 3)
    own = Army(UnitId(1), p1, Coord(0, 1))
    enemy = Army(UnitId(2), p2, Coord(3, 1))  # 2 cells from (1,1) post-step
    m.place_unit(own, Coord(0, 1))
    m.place_unit(enemy, Coord(3, 1))
    own.standing_order = Heading(Direction.E)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    # Step succeeded, then post-step scan triggered interrupt.
    assert own.coord == Coord(1, 1)
    assert own.standing_order is None
    assert UnitId(1) in result.interrupted_unit_ids
    assert UnitId(1) in result.moved_unit_ids


# --- PatrolPath end-to-end --------------------------------------------------


def test_patrol_path_walks_and_exhausts(
    p1: Player, resolver: CombatResolver
) -> None:
    m = _land_map(5, 1)
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    army.standing_order = PatrolPath.new((Coord(1, 0), Coord(2, 0)))

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.coord == Coord(1, 0)
    assert isinstance(army.standing_order, PatrolPath)
    assert army.standing_order.remaining == (Coord(2, 0),)

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.coord == Coord(2, 0)
    # Path exhausted → standing order cleared.
    assert army.standing_order is None


# --- Sentry: no-op + wake trigger -------------------------------------------


def test_sentry_does_not_move(
    p1: Player, resolver: CombatResolver
) -> None:
    m = _land_map(5, 1)
    army = Army(UnitId(1), p1, Coord(2, 0))
    m.place_unit(army, Coord(2, 0))
    army.standing_order = Sentry()

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.coord == Coord(2, 0)
    assert isinstance(army.standing_order, Sentry)


def test_wake_clears_sentry_when_enemy_in_scan(p1: Player, p2: Player) -> None:
    m = _land_map(10, 1)
    own = Army(UnitId(1), p1, Coord(0, 0))
    enemy = Army(UnitId(2), p2, Coord(2, 0))  # within Army's scan_range=2
    m.place_unit(own, Coord(0, 0))
    m.place_unit(enemy, Coord(2, 0))
    own.standing_order = Sentry()

    woken = wake_sentried_units(p1, m)
    assert woken == (UnitId(1),)
    assert own.standing_order is None


def test_wake_leaves_sentry_when_no_enemy(p1: Player) -> None:
    m = _land_map(10, 1)
    own = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(own, Coord(0, 0))
    own.standing_order = Sentry()

    woken = wake_sentried_units(p1, m)
    assert woken == ()
    assert isinstance(own.standing_order, Sentry)


def test_enemy_in_scan_range_uses_unit_scan(p1: Player, p2: Player) -> None:
    # Patrol's scan_range is 3; an enemy 3 cells away counts.
    m = _mixed_map(["WWWWWW"])
    own = Patrol(UnitId(1), p1, Coord(0, 0))
    enemy = Patrol(UnitId(2), p2, Coord(3, 0))
    m.place_unit(own, Coord(0, 0))
    m.place_unit(enemy, Coord(3, 0))
    assert enemy_in_scan_range(own, m) is True


# --- wake triggers: artillery zones + city discovery (any unit kind) -----------


def _city_at(m: Map, coord: Coord, owner: Player | None, cid: int) -> City:
    city = City(id=CityId(cid), coord=coord, owner=owner)
    m._tiles[coord] = Tile(  # pyright: ignore[reportPrivateUsage]
        coord=coord, terrain=TerrainKind.CITY, city=city
    )
    return city


def test_heading_halts_at_edge_of_hostile_artillery_zone(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """A unit on auto-move HALTS AT THE EDGE of a hostile city's gun range —
    it refuses the step that would carry it in, and wakes, exactly as it stops
    before an attack. An order never sleepwalks into the red zone (spec §4.7);
    entering is a deliberate manual step."""
    m = _land_map(10, 3)
    _city_at(m, Coord(7, 1), p2, 1)
    # Owner has seen the city (it's on the map view); the guard is real-map
    # based either way, matching enemy_in_scan_range's convention.
    p1.view.visible = {Coord(x, y) for x in range(10) for y in range(3)}
    army = Army(UnitId(1), p1, Coord(3, 1))
    m.place_unit(army, Coord(3, 1))
    army.standing_order = Heading(direction=Direction.E)
    fortified = replace(
        STANDARD, city_artillery_range=2, city_artillery_hit_prob=0.0,
        city_artillery_pin_prob=0.0,
    )

    # Step 1: (3,1)->(4,1), chebyshev 3 to city: outside, order persists.
    result = apply_standing_orders(p1, m, fortified, resolver, random.Random(0))
    assert army.coord == Coord(4, 1)
    assert army.standing_order is not None
    assert army.id in result.moved_unit_ids

    # Step 2: (4,1)->(5,1) would ENTER the zone (chebyshev 2) -> refuse the
    # step and wake. The army stays put at the edge, never entering range.
    result = apply_standing_orders(p1, m, fortified, resolver, random.Random(0))
    assert army.coord == Coord(4, 1)  # did NOT step in
    assert army.standing_order is None
    assert army.id in result.interrupted_unit_ids
    assert army.id not in result.moved_unit_ids
    assert army.hits == Army.max_hits  # unharmed — never entered range


def test_heading_wakes_on_discovering_a_city(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """Finding a city is news worth stopping for — classic rules included."""
    del p2
    m = _land_map(10, 3)
    _city_at(m, Coord(7, 1), None, 1)  # a neutral city, never seen by p1
    army = Army(UnitId(1), p1, Coord(2, 1))
    m.place_unit(army, Coord(2, 1))
    army.standing_order = Heading(direction=Direction.E)

    # Step 1: to (3,1); city at chebyshev 4 > scan 2: keeps walking.
    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.standing_order is not None

    # Pretend the scan phase ran (nothing new seen yet).
    # Step 2: to (4,1); chebyshev 3 > 2: still walking.
    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.standing_order is not None

    # Step 3: to (5,1); chebyshev 2 == scan range: discovery -> wake.
    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.coord == Coord(5, 1)
    assert army.standing_order is None
    assert army.id in result.interrupted_unit_ids


def test_no_wake_when_the_city_was_already_seen(
    p1: Player, resolver: CombatResolver
) -> None:
    """A known city is not a discovery: classic-rules auto-moves walk on
    past cities the player has already scouted."""
    m = _land_map(10, 3)
    _city_at(m, Coord(7, 1), None, 1)
    p1.view.visible = {Coord(7, 1)}  # already on the player's map
    army = Army(UnitId(1), p1, Coord(4, 1))
    m.place_unit(army, Coord(4, 1))
    army.standing_order = Heading(direction=Direction.E)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.coord == Coord(5, 1)
    assert army.standing_order is not None  # no news, no wake
    assert army.id in result.moved_unit_ids


# --- never auto-attack on an order ---------------------------------------------


def test_heading_wakes_instead_of_attacking_an_enemy(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """A heading must not walk into combat — it wakes one cell short, with
    the enemy intact (engaging is a deliberate manual step)."""
    m = _land_map(6, 1)
    mover = Army(UnitId(1), p1, Coord(0, 0))
    enemy = Army(UnitId(2), p2, Coord(1, 0))  # directly in the path
    m.place_unit(mover, Coord(0, 0))
    m.place_unit(enemy, Coord(1, 0))
    mover.standing_order = Heading(direction=Direction.E)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))

    assert mover.coord == Coord(0, 0)  # did not step
    assert mover.standing_order is None  # woke
    assert m.unit_by_id(enemy.id) is not None  # enemy untouched — no auto-attack
    assert UnitId(1) in result.interrupted_unit_ids


def test_goto_wakes_instead_of_capturing_a_city(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """A go-to onto an enemy city must wake rather than auto-capture."""
    m = _land_map(6, 1)
    enemy_city = City(id=CityId(1), coord=Coord(1, 0), owner=p2)
    m._tiles[Coord(1, 0)] = Tile(  # pyright: ignore[reportPrivateUsage]
        coord=Coord(1, 0), terrain=TerrainKind.CITY, city=enemy_city
    )
    mover = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(mover, Coord(0, 0))
    mover.standing_order = PatrolPath.new((Coord(1, 0),))
    deterministic = replace(STANDARD, army_capture_city_deterministic=True)

    result = apply_standing_orders(p1, m, deterministic, resolver, random.Random(0))

    assert mover.coord == Coord(0, 0)  # did not step in
    assert enemy_city.owner is p2  # not captured by the order
    assert mover.standing_order is None  # woke
    assert UnitId(1) in result.interrupted_unit_ids


# --- loading mode (carrier waits, wakes when full) ----------------------------


def test_loading_carrier_wakes_when_full() -> None:
    """A Loading carrier with no room left is woken at the top of the turn."""
    from empire.core.standing_order import Loading
    from empire.core.unit import Army, Transport

    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    m = _land_map(3, 1)  # cells are land; carrier just needs to exist on the map
    transport = Transport(UnitId(1), p1, Coord(0, 0))
    m.place_unit(transport, Coord(0, 0))
    transport.standing_order = Loading()
    # Fill the hold to capacity with aboard armies.
    for i in range(transport.effective_capacity()):
        cargo = Army(UnitId(100 + i), p1, Coord(0, 0))
        m.add_aboard_unit(cargo)
        cargo.carried_by = transport.id
        transport.cargo.append(cargo.id)

    woken = wake_sentried_units(p1, m)

    assert UnitId(1) in woken
    assert transport.standing_order is None


def test_loading_carrier_with_room_keeps_waiting() -> None:
    """A Loading carrier that isn't full (and sees no enemy) stays in mode."""
    from empire.core.standing_order import Loading
    from empire.core.unit import Transport

    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    m = _land_map(3, 1)
    transport = Transport(UnitId(1), p1, Coord(0, 0))
    m.place_unit(transport, Coord(0, 0))
    transport.standing_order = Loading()  # empty hold

    assert wake_sentried_units(p1, m) == ()
    assert isinstance(transport.standing_order, Loading)


def test_loading_order_round_trips_through_save() -> None:
    from empire.core.game import Game
    from empire.core.standing_order import Loading
    from empire.core.unit import Transport
    from empire.persistence.schema_v1 import V1Serializer

    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    m = _mixed_map(["LWL"])
    transport = Transport(UnitId(1), p1, Coord(1, 0))
    m.place_unit(transport, Coord(1, 0))
    transport.standing_order = Loading()
    game = Game(rules=STANDARD, real_map=m, players=[p1, p2], seed=1)

    loaded = V1Serializer().from_dict(V1Serializer().to_dict(game))
    restored = loaded.map.unit_by_id(UnitId(1))
    assert restored is not None
    assert isinstance(restored.standing_order, Loading)


# --- Loading dock: continuous sweep of newly-adjacent cargo -------------------


def test_load_adjacent_cargo_snaps_own_armies_not_enemy(
    p1: Player, p2: Player
) -> None:
    """The shared loader boards eligible friendly neighbours and ignores an
    enemy unit (ownership is enforced by can_carry)."""
    m = _mixed_map(["WLL", "LLL"])
    transport = Transport(UnitId(1), p1, Coord(0, 0))
    m.place_unit(transport, Coord(0, 0))
    m.place_unit(Army(UnitId(2), p1, Coord(1, 0)), Coord(1, 0))  # own, adjacent
    m.place_unit(Army(UnitId(3), p1, Coord(0, 1)), Coord(0, 1))  # own, adjacent
    m.place_unit(Army(UnitId(4), p2, Coord(1, 1)), Coord(1, 1))  # ENEMY, adjacent

    boarded = load_adjacent_cargo(transport, m)
    assert set(boarded) == {UnitId(2), UnitId(3)}
    assert len(transport.cargo) == 2
    assert m.unit_by_id(UnitId(4)) is not None  # enemy left ashore


def test_load_adjacent_cargo_stops_at_capacity(p1: Player) -> None:
    # Transport centred so all 8 neighbours are in-bounds land; more armies
    # than capacity surround it.
    m = _mixed_map(["LLL", "LWL", "LLL"])
    transport = Transport(UnitId(1), p1, Coord(1, 1))
    m.place_unit(transport, Coord(1, 1))
    cap = transport.effective_capacity()
    neighbours = [c for c in Coord(1, 1).neighbors() if m.in_bounds(c)]
    assert len(neighbours) > cap  # genuinely over-subscribed
    for i, c in enumerate(neighbours, start=2):
        m.place_unit(Army(UnitId(i), p1, c), c)

    boarded = load_adjacent_cargo(transport, m)
    assert len(boarded) == cap  # filled exactly to capacity, no more
    assert len(transport.cargo) == cap


def test_loading_carrier_sweeps_new_cargo_each_turn(
    p1: Player, resolver: CombatResolver
) -> None:
    """A Loading carrier is a persistent dock: it snaps up armies that become
    adjacent on LATER turns (freshly produced / walked up), not just at
    set-time — the playtest bug ('Load doesn't sweep new armies')."""
    m = _mixed_map(["WL"])
    transport = Transport(UnitId(1), p1, Coord(0, 0))
    m.place_unit(transport, Coord(0, 0))
    transport.standing_order = Loading()

    # Turn 1: nothing adjacent -> nothing loaded, still holding.
    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert len(transport.cargo) == 0
    assert isinstance(transport.standing_order, Loading)

    # A freshly-produced army appears adjacent afterwards.
    m.place_unit(Army(UnitId(2), p1, Coord(1, 0)), Coord(1, 0))

    # Turn 2: the dock sweeps it aboard.
    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert len(transport.cargo) == 1
    assert transport.cargo[0] == UnitId(2)  # cargo holds ids
    assert all(u.id != UnitId(2) for u in m.board_units())  # off the board, aboard


# --- looping ship patrol: engine-level ping-pong ------------------------------


def test_ship_patrol_loops_between_endpoints(
    p1: Player, resolver: CombatResolver
) -> None:
    """A looping PatrolPath drives a ship A->B->A->B... indefinitely under
    apply_standing_orders — the continuous patrol route ('t'). Speed-1 hull
    on a 3-cell strait: 8 turns = two full round trips."""
    from empire.core.unit import Patrol

    m = _mixed_map(["WWW"])
    ship = Patrol(UnitId(1), p1, Coord(0, 0))
    m.place_unit(ship, Coord(0, 0))
    a, b = Coord(0, 0), Coord(2, 0)
    cycle = (Coord(1, 0), b, Coord(1, 0), a)  # out and back, ending at A
    ship.standing_order = PatrolPath.new(cycle, loop=True)

    seen: list[Coord] = []
    for _ in range(8):
        apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
        assert ship.standing_order is not None, "patrol must persist"
        seen.append(ship.coord)
    assert seen == [Coord(1, 0), b, Coord(1, 0), a] * 2  # two clean loops


def test_ship_patrol_wakes_on_enemy_in_scan(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """The patrol's wake contract: an enemy entering scan range interrupts
    the loop (the ship stops and the player takes over)."""
    from empire.core.unit import Patrol

    m = _mixed_map(["WWWWWW"])
    ship = Patrol(UnitId(1), p1, Coord(0, 0))
    m.place_unit(ship, Coord(0, 0))
    cycle = (Coord(1, 0), Coord(2, 0), Coord(1, 0), Coord(0, 0))
    ship.standing_order = PatrolPath.new(cycle, loop=True)
    # Enemy ship within Patrol scan (3) of the first step's destination.
    m.place_unit(Patrol(UnitId(2), p2, Coord(4, 0)), Coord(4, 0))

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert ship.coord == Coord(1, 0)  # stepped, then woke
    assert ship.standing_order is None
    assert UnitId(1) in result.interrupted_unit_ids

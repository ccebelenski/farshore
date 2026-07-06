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
    from empire.persistence.schema import Serializer

    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    m = _mixed_map(["LWL"])
    transport = Transport(UnitId(1), p1, Coord(1, 0))
    m.place_unit(transport, Coord(1, 0))
    transport.standing_order = Loading()
    game = Game(rules=STANDARD, real_map=m, players=[p1, p2], seed=1)

    loaded = Serializer().from_dict(Serializer().to_dict(game))
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


# --- Explore: autonomous fog-frontier scouting --------------------------------


def _see(p: Player, cells: list[Coord]) -> None:
    p.view.visible = set(cells)


def test_explore_army_heads_for_frontier_and_persists(
    p1: Player, resolver: CombatResolver
) -> None:
    from empire.core.standing_order import Explore

    m = _land_map(5, 1)
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    army.standing_order = Explore()
    _see(p1, [Coord(x, 0) for x in range(3)])  # (3,0),(4,0) unexplored

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.coord == Coord(1, 0)  # heading for the frontier at (2,0)
    assert isinstance(army.standing_order, Explore)  # still exploring
    assert UnitId(1) in result.moved_unit_ids


def test_explore_prefers_shore_frontier(
    p1: Player, resolver: CombatResolver
) -> None:
    """Shore frontier wins over NEARER inland frontier: the coastline is the
    priority (it reveals the shape of the world)."""
    from empire.core.standing_order import Explore

    rows = ["LLLL", "LLLL", "LLLL", "LLLL", "LWLL"]
    m = _mixed_map(rows)
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    army.standing_order = Explore()
    # Column 3 unexplored -> frontier is column 2. (2,3)/(2,4) touch the
    # known water at (1,4): shore. (2,0)/(2,1) are nearer but inland.
    _see(p1, [Coord(x, y) for x in range(3) for y in range(5)])

    for _ in range(3):
        apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.coord == Coord(2, 3)  # dist-3 shore beat the dist-2 inland


def test_explore_wakes_when_no_frontier_left(
    p1: Player, resolver: CombatResolver
) -> None:
    from empire.core.standing_order import Explore

    m = _land_map(3, 1)
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    army.standing_order = Explore()
    _see(p1, [Coord(x, 0) for x in range(3)])  # everything seen

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert army.standing_order is None  # woke: nothing left to explore
    assert UnitId(1) in result.interrupted_unit_ids
    assert army.coord == Coord(0, 0)


def test_two_explorers_claim_different_frontier_cells(
    p1: Player, resolver: CombatResolver
) -> None:
    from empire.core.standing_order import Explore

    m = _land_map(4, 2)
    a = Army(UnitId(1), p1, Coord(0, 0))
    b = Army(UnitId(2), p1, Coord(0, 1))
    m.place_unit(a, Coord(0, 0))
    m.place_unit(b, Coord(0, 1))
    a.standing_order = Explore()
    b.standing_order = Explore()
    _see(p1, [Coord(x, y) for x in range(3) for y in range(2)])  # col 3 unseen

    for _ in range(2):
        apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    # Frontier is (2,0) and (2,1); the claims set kept them off each other.
    assert {a.coord, b.coord} == {Coord(2, 0), Coord(2, 1)}


def test_explore_fighter_wakes_at_bingo_fuel(
    p1: Player, resolver: CombatResolver
) -> None:
    """A fighter never explores itself into a crash: it wakes when remaining
    fuel just covers the flight home to the nearest own city."""
    from empire.core.city import City
    from empire.core.identity import CityId
    from empire.core.standing_order import Explore
    from empire.core.unit import Fighter

    m = _land_map(8, 1)
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    m._tiles[Coord(0, 0)] = type(m.tile(Coord(0, 0)))(  # own base at (0,0)
        coord=Coord(0, 0), terrain=m.tile(Coord(0, 0)).terrain, city=city
    )
    jet = Fighter(UnitId(1), p1, Coord(0, 0))
    jet.range = 3  # tiny tank
    m.place_unit(jet, Coord(0, 0))
    jet.standing_order = Explore()
    _see(p1, [Coord(x, 0) for x in range(6)])  # frontier at (5,0), far away

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert jet.standing_order is None  # woke at bingo, not crashed
    assert m.unit_by_id(UnitId(1)) is not None  # alive
    # It can still get home: fuel >= distance back to (0,0).
    assert jet.range >= jet.coord.chebyshev_to(Coord(0, 0))


# --- Return To Base: fighter flies itself home ---------------------------------


def _city_tile(m: Map, city_obj) -> None:
    m._tiles[city_obj.coord] = Tile(
        coord=city_obj.coord, terrain=TerrainKind.CITY, city=city_obj
    )


def test_rtb_flies_home_lands_and_refuels(
    p1: Player, resolver: CombatResolver
) -> None:
    from empire.core.city import City
    from empire.core.standing_order import ReturnToBase
    from empire.core.unit import Fighter

    m = _land_map(12, 1)
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    _city_tile(m, city)
    jet = Fighter(UnitId(1), p1, Coord(10, 0))
    jet.range = 12
    m.place_unit(jet, Coord(10, 0))
    jet.standing_order = ReturnToBase()

    # Fighter speed 8: turn 1 covers 8 cells, turn 2 lands the last 2.
    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert jet.coord == Coord(2, 0)
    assert isinstance(jet.standing_order, ReturnToBase)
    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert jet.coord == Coord(0, 0)  # landed
    assert jet.standing_order is None  # order complete
    assert jet.range == STANDARD.fighter_base_range  # refuelled on landing
    assert UnitId(1) in result.moved_unit_ids


def test_rtb_boards_a_nearer_carrier(
    p1: Player, resolver: CombatResolver
) -> None:
    from empire.core.city import City
    from empire.core.standing_order import ReturnToBase
    from empire.core.unit import Carrier, Fighter

    m = _mixed_map(["LLLLWWWWWWWL"])  # land strip, water, far land
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    _city_tile(m, city)
    flattop = Carrier(UnitId(1), p1, Coord(6, 0))
    m.place_unit(flattop, Coord(6, 0))
    jet = Fighter(UnitId(2), p1, Coord(8, 0))
    jet.range = 20
    m.place_unit(jet, Coord(8, 0))
    jet.standing_order = ReturnToBase()

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    # Carrier (dist 2) beat the city (dist 8): the jet boarded it.
    assert jet.carried_by == flattop.id
    assert jet.id in flattop.cargo
    assert jet.standing_order is None


def test_rtb_skips_full_airbase(p1: Player, resolver: CombatResolver) -> None:
    """A city with all 8 airbase slots parked is no landing spot: RTB picks
    the farther open base instead."""
    from empire.core.city import City
    from empire.core.standing_order import ReturnToBase
    from empire.core.unit import Fighter

    m = _land_map(14, 2)  # second row: room to route around the packed base
    near = City(id=CityId(1), coord=Coord(4, 0), owner=p1)
    far = City(id=CityId(2), coord=Coord(0, 0), owner=p1)
    _city_tile(m, near)
    _city_tile(m, far)
    for i in range(8):  # pack the near base to its §5.4 air limit
        parked = Fighter(UnitId(10 + i), p1, Coord(4, 0))
        m.place_unit(parked, Coord(4, 0))
    jet = Fighter(UnitId(1), p1, Coord(6, 0))
    jet.range = 20
    m.place_unit(jet, Coord(6, 0))
    jet.standing_order = ReturnToBase()

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert jet.coord == Coord(0, 0)  # flew past the full base to the open one
    assert jet.standing_order is None


def test_rtb_wakes_when_no_base_reachable_on_fuel(
    p1: Player, resolver: CombatResolver
) -> None:
    """The target vanished en route (carrier sunk): nothing reachable on
    current fuel -> wake, player chooses where to fly or crash."""
    from empire.core.city import City
    from empire.core.standing_order import ReturnToBase
    from empire.core.unit import Fighter

    m = _land_map(30, 1)
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    _city_tile(m, city)
    jet = Fighter(UnitId(1), p1, Coord(20, 0))
    jet.range = 5  # can never reach (0,0): dist 20
    m.place_unit(jet, Coord(20, 0))
    jet.standing_order = ReturnToBase()

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert jet.standing_order is None
    assert UnitId(1) in result.interrupted_unit_ids
    assert m.unit_by_id(UnitId(1)) is not None  # alive, player's call now


def test_fighter_lands_at_own_city_with_parked_fighter(
    p1: Player, resolver: CombatResolver
) -> None:
    """The airbase landing rule: an own city with §5.4 air capacity left
    accepts an incoming fighter even though a friendly is parked there
    (one parked fighter used to make the whole airbase unlandable)."""
    from empire.core.city import City
    from empire.core.engine import StepOutcome, execute_unit_path
    from empire.core.unit import Fighter

    m = _land_map(3, 1)
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    _city_tile(m, city)
    m.place_unit(Fighter(UnitId(1), p1, Coord(0, 0)), Coord(0, 0))  # parked
    jet = Fighter(UnitId(2), p1, Coord(1, 0))
    m.place_unit(jet, Coord(1, 0))

    outcome = execute_unit_path(
        unit=jet, path=((0, 0),), real_map=m, rules=STANDARD,
        combat_resolver=resolver, rng=random.Random(0),
    )
    assert outcome.last_outcome is StepOutcome.OK
    assert jet.coord == Coord(0, 0)


# --- Explore: the three second-explorer deadlocks (playtest, seed-4 shape) ----


def test_explore_army_routes_around_own_city(
    p1: Player, resolver: CombatResolver
) -> None:
    """Armies can never enter a city, so explore must PLAN around them: an
    army whose shortest known route runs through its own capital used to wake
    with 'nothing to explore' on the first step (the seed-4 playtest bug)."""
    from empire.core.city import City
    from empire.core.standing_order import Explore

    m = _land_map(5, 2)
    city = City(id=CityId(1), coord=Coord(1, 0), owner=p1)
    _city_tile(m, city)
    army = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(army, Coord(0, 0))
    army.standing_order = Explore()
    _see(p1, [Coord(x, y) for x in range(4) for y in range(2)])  # col 4 unseen

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert isinstance(army.standing_order, Explore)  # did NOT falsely wake
    assert army.coord == Coord(1, 1)  # detoured around the city


def test_explore_holds_when_all_frontier_is_claimed(
    p1: Player, resolver: CombatResolver
) -> None:
    """One reachable frontier cell, two explorers: the second HOLDS (a fellow
    explorer claimed it — the world opens next turn) instead of waking with
    'exploration done'."""
    from empire.core.standing_order import Explore

    m = _land_map(4, 1)
    a = Army(UnitId(1), p1, Coord(1, 0))
    b = Army(UnitId(2), p1, Coord(0, 0))
    m.place_unit(a, Coord(1, 0))
    m.place_unit(b, Coord(0, 0))
    a.standing_order = Explore()
    b.standing_order = Explore()
    _see(p1, [Coord(x, 0) for x in range(3)])  # (3,0) unseen -> frontier (2,0)

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert a.coord == Coord(2, 0)  # A took the only frontier cell
    assert b.coord == Coord(0, 0)  # B waited...
    assert isinstance(b.standing_order, Explore)  # ...and stayed on mission


def test_explore_waits_out_transient_traffic(
    p1: Player, resolver: CombatResolver
) -> None:
    """A friendly parked on the only route is traffic, not terrain: the
    explorer holds (order intact) and proceeds once the cell frees up."""
    from empire.core.standing_order import Explore, Sentry

    m = _land_map(4, 1)
    blocker = Army(UnitId(1), p1, Coord(1, 0))
    m.place_unit(blocker, Coord(1, 0))
    blocker.standing_order = Sentry()
    scout = Army(UnitId(2), p1, Coord(0, 0))
    m.place_unit(scout, Coord(0, 0))
    scout.standing_order = Explore()
    _see(p1, [Coord(x, 0) for x in range(3)])

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert scout.coord == Coord(0, 0)  # held, didn't wake
    assert isinstance(scout.standing_order, Explore)

    m.remove_unit(blocker)  # traffic clears
    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert scout.coord == Coord(1, 0)  # moving again
    assert isinstance(scout.standing_order, Explore)


# --- move-order defer/retry: transient jams never cost an order ---------------


def test_convergent_headings_defer_and_both_advance(
    p1: Player, resolver: CombatResolver
) -> None:
    """A blocked by B who moves this same phase: A defers, retries after the
    traffic clears, and BOTH keep their orders (transient jams used to wake
    the blocked unit instantly — playtest: 'premature wakes')."""
    m = _land_map(4, 1)
    a = Army(UnitId(1), p1, Coord(0, 0))
    b = Army(UnitId(2), p1, Coord(1, 0))
    m.place_unit(a, Coord(0, 0))
    m.place_unit(b, Coord(1, 0))
    a.standing_order = Heading(Direction.E)
    b.standing_order = Heading(Direction.E)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert b.coord == Coord(2, 0)
    assert a.coord == Coord(1, 0)  # deferred, then took the freed cell
    assert isinstance(a.standing_order, Heading)  # order survived the jam
    assert isinstance(b.standing_order, Heading)
    assert set(result.moved_unit_ids) == {UnitId(1), UnitId(2)}


def test_goto_repaths_around_a_parked_friendly(
    p1: Player, resolver: CombatResolver
) -> None:
    """A go-to whose route is squatted by a sentried friendly re-paths around
    the jam on the retry instead of waking (flexible pathing)."""
    m = _land_map(4, 2)
    blocker = Army(UnitId(1), p1, Coord(1, 0))
    m.place_unit(blocker, Coord(1, 0))
    blocker.standing_order = Sentry()
    mover = Army(UnitId(2), p1, Coord(0, 0))
    m.place_unit(mover, Coord(0, 0))
    mover.standing_order = PatrolPath.new((Coord(1, 0), Coord(2, 0), Coord(3, 0)))
    _see(p1, [Coord(x, y) for x in range(4) for y in range(2)])

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(1, 1)  # detoured via the open row
    assert isinstance(mover.standing_order, PatrolPath)  # still en route

    for _ in range(3):
        apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(3, 0)  # arrived despite the squatter


def test_heading_wakes_on_second_attempt_when_still_blocked(
    p1: Player, resolver: CombatResolver
) -> None:
    """No alternative geometry for a heading: still blocked after everyone
    moved -> wake (the deliberate second-attempt wake)."""
    m = _land_map(3, 1)
    blocker = Army(UnitId(1), p1, Coord(1, 0))
    m.place_unit(blocker, Coord(1, 0))
    blocker.standing_order = Sentry()
    mover = Army(UnitId(2), p1, Coord(0, 0))
    m.place_unit(mover, Coord(0, 0))
    mover.standing_order = Heading(Direction.E)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(0, 0)
    assert mover.standing_order is None
    assert UnitId(2) in result.interrupted_unit_ids


# --- wake on NEWS, not state: contact deltas (playtest 'orders wake too easily')


def test_goto_with_seeded_contact_keeps_walking(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """An enemy the player could already see when issuing the go-to is OLD
    news: seeded into the order's contacts, it never wakes the unit — the
    order used to die instantly anywhere near a known enemy."""
    m = _land_map(8, 3)
    mover = Army(UnitId(1), p1, Coord(0, 0))
    enemy = Army(UnitId(2), p2, Coord(2, 2))  # within scan (2) the whole way
    m.place_unit(mover, Coord(0, 0))
    m.place_unit(enemy, Coord(2, 2))
    mover.standing_order = PatrolPath.new(
        (Coord(1, 0), Coord(2, 0), Coord(3, 0)),
        contacts=frozenset({UnitId(2)}),
    )

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(1, 0)
    assert isinstance(mover.standing_order, PatrolPath)  # old news: no wake

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(2, 0)
    assert isinstance(mover.standing_order, PatrolPath)  # still walking


def test_goto_wakes_on_new_contact_mid_route(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """A genuinely NEW contact — an id not carried on the order — still
    wakes the unit the step it appears."""
    m = _land_map(10, 3)
    mover = Army(UnitId(1), p1, Coord(0, 0))
    known = Army(UnitId(2), p2, Coord(2, 2))
    m.place_unit(mover, Coord(0, 0))
    m.place_unit(known, Coord(2, 2))
    mover.standing_order = PatrolPath.new(
        (Coord(1, 0), Coord(2, 0), Coord(3, 0), Coord(4, 0)),
        contacts=frozenset({UnitId(2)}),
    )

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(1, 0)
    assert isinstance(mover.standing_order, PatrolPath)

    # A second enemy appears; in scan of the NEXT step's cell (2,0).
    m.place_unit(Army(UnitId(3), p2, Coord(4, 1)), Coord(4, 1))
    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(2, 0)  # stepped, then the news woke it
    assert mover.standing_order is None
    assert UnitId(1) in result.interrupted_unit_ids
    assert UnitId(1) in result.moved_unit_ids


def test_heading_with_seeded_contact_keeps_walking(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """Same delta rule for a heading: the carried set rolls forward each
    step, so a known shadowing enemy never interrupts."""
    m = _land_map(8, 3)
    mover = Army(UnitId(1), p1, Coord(0, 0))
    enemy = Army(UnitId(2), p2, Coord(2, 2))
    m.place_unit(mover, Coord(0, 0))
    m.place_unit(enemy, Coord(2, 2))
    mover.standing_order = Heading(
        direction=Direction.E, contacts=frozenset({UnitId(2)})
    )

    for expected_x in (1, 2):
        apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
        assert mover.coord == Coord(expected_x, 0)
        assert isinstance(mover.standing_order, Heading)


def test_explore_ignores_seeded_contact_wakes_on_new_one(
    p1: Player, p2: Player, resolver: CombatResolver
) -> None:
    """Explore near a KNOWN enemy keeps exploring; a new contact wakes it."""
    from empire.core.standing_order import Explore

    m = _land_map(6, 2)
    scout = Army(UnitId(1), p1, Coord(0, 0))
    known = Army(UnitId(2), p2, Coord(1, 1))  # in scan from the start
    m.place_unit(scout, Coord(0, 0))
    m.place_unit(known, Coord(1, 1))
    scout.standing_order = Explore(contacts=frozenset({UnitId(2)}))
    _see(p1, [Coord(x, y) for x in range(3) for y in range(2)])  # col 3+ unseen

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert scout.coord == Coord(1, 0)  # kept exploring despite the known enemy
    assert isinstance(scout.standing_order, Explore)

    # A NEW enemy appears within scan of the next step's cell.
    m.place_unit(Army(UnitId(3), p2, Coord(3, 1)), Coord(3, 1))
    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert scout.standing_order is None  # news: woke
    assert UnitId(1) in result.interrupted_unit_ids


def test_sentry_still_wakes_on_any_enemy_in_scan(p1: Player, p2: Player) -> None:
    """The news rule is for MOVING orders only: a sentried unit's surprise is
    ANY enemy in scan (spec: surprises auto-WAKE), exactly as before."""
    m = _land_map(10, 1)
    own = Army(UnitId(1), p1, Coord(0, 0))
    enemy = Army(UnitId(2), p2, Coord(2, 0))
    m.place_unit(own, Coord(0, 0))
    m.place_unit(enemy, Coord(2, 0))
    own.standing_order = Sentry()

    assert wake_sentried_units(p1, m) == (UnitId(1),)
    assert own.standing_order is None


# --- terrain news re-paths a go-to instead of waking it ------------------------


def test_goto_repaths_around_revealed_terrain_and_arrives(
    p1: Player, resolver: CombatResolver
) -> None:
    """A one-shot go-to whose next cell turns out to be water (a route built
    over fog that lifted) re-paths to the same destination over known terrain
    and completes, instead of waking on the spot."""
    m = _mixed_map(["LLWLL", "LLLLL"])
    _see(p1, [Coord(x, y) for x in range(5) for y in range(2)])
    mover = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(mover, Coord(0, 0))
    # A blind straight-line route through the water at (2,0).
    mover.standing_order = PatrolPath.new(
        (Coord(1, 0), Coord(2, 0), Coord(3, 0), Coord(4, 0))
    )

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(1, 0)  # legal first leg

    # Next step (2,0) is water: re-path around via row 1, no wake.
    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(2, 1)  # detoured
    assert isinstance(mover.standing_order, PatrolPath)

    for _ in range(2):
        apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(4, 0)  # arrived at the ORIGINAL destination
    assert mover.standing_order is None  # exhausted normally


def test_goto_wakes_when_destination_unreachable(
    p1: Player, resolver: CombatResolver
) -> None:
    """No route at all to the go-to's destination (revealed water wall):
    the re-path finds nothing and the unit wakes."""
    m = _mixed_map(["LLWLL"])
    _see(p1, [Coord(x, 0) for x in range(5)])
    mover = Army(UnitId(1), p1, Coord(0, 0))
    m.place_unit(mover, Coord(0, 0))
    mover.standing_order = PatrolPath.new(
        (Coord(1, 0), Coord(2, 0), Coord(3, 0), Coord(4, 0))
    )

    apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(1, 0)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(1, 0)  # nowhere to go
    assert mover.standing_order is None  # woke: destination unreachable
    assert UnitId(1) in result.interrupted_unit_ids


def test_heading_still_wakes_on_revealed_terrain(
    p1: Player, resolver: CombatResolver
) -> None:
    """Fixed geometry keeps today's behavior: a heading into water wakes —
    only a go-to (a destination, not a direction) re-plans."""
    m = _mixed_map(["LLW"])
    _see(p1, [Coord(x, 0) for x in range(3)])
    mover = Army(UnitId(1), p1, Coord(1, 0))
    m.place_unit(mover, Coord(1, 0))
    mover.standing_order = Heading(direction=Direction.E)

    result = apply_standing_orders(p1, m, STANDARD, resolver, random.Random(0))
    assert mover.coord == Coord(1, 0)
    assert mover.standing_order is None
    assert UnitId(1) in result.interrupted_unit_ids

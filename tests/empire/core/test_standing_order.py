"""Tests for `StandingOrder` value types and the engine's per-turn step."""

from __future__ import annotations

import random

import pytest

from empire.combat.resolver import CombatResolver
from empire.core.coord import Coord, Direction
from empire.core.engine import (
    apply_standing_orders,
    enemy_in_scan_range,
    wake_sentried_units,
)
from empire.core.identity import PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.standing_order import Heading, PatrolPath, Sentry
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Patrol


def _land_map(width: int, height: int) -> Map:
    tiles: dict[Coord, Tile] = {}
    for y in range(height):
        for x in range(width):
            c = Coord(x, y)
            tiles[c] = Tile(coord=c, terrain=TerrainKind.LAND)
    return Map(width=width, height=height, tiles=tiles)


def _mixed_map(rows: list[str]) -> Map:
    terrain = {"L": TerrainKind.LAND, "W": TerrainKind.WATER}
    height = len(rows)
    width = len(rows[0])
    tiles: dict[Coord, Tile] = {}
    for y in range(height):
        for x in range(width):
            c = Coord(x, y)
            tiles[c] = Tile(coord=c, terrain=terrain[rows[y][x]])
    return Map(width=width, height=height, tiles=tiles)


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


def test_patrol_path_reverse_on_end_flips() -> None:
    cells = (Coord(1, 0), Coord(2, 0))
    p = PatrolPath.new(cells, reverse_on_end=True)
    after_first = p.after_step()
    assert after_first is not None and after_first.remaining == (Coord(2, 0),)
    after_second = after_first.after_step()
    assert after_second is not None
    assert after_second.remaining == (Coord(2, 0), Coord(1, 0))  # reversed
    assert after_second.original == (Coord(2, 0), Coord(1, 0))


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

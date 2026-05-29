"""Canary tests for threat assessment (Phase 11)."""

from __future__ import annotations

from empire.ai.strategic.intel.threats import assess_threats
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, UnitId
from empire.core.map import RememberedTile, UnitSnapshot
from empire.core.tile import TerrainKind
from empire.core.unit import Army, Battleship, Transport, UnitKind
from tests.empire.ai.strategic.intel._world import (
    grid_map,
    place,
    player,
    reveal,
    world,
)


def test_no_known_enemies_no_threats() -> None:
    p1 = player(1)
    rmap = grid_map(["....." for _ in range(5)])
    assert assess_threats(world(rmap, p1)) == ()


def test_enemy_army_near_city_puts_it_at_risk() -> None:
    """An enemy army three cells from my city flags that city as at-risk."""
    p1 = player(1)
    p2 = player(2, "Enemy")
    my_city = City(id=CityId(1), coord=Coord(1, 1), owner=p1)
    rmap = grid_map(["......." for _ in range(7)], cities={Coord(1, 1): my_city})
    enemy = place(rmap, Army(UnitId(50), p2, Coord(4, 1)))  # 3 cells east
    reveal(p1, Coord(1, 1), Coord(4, 1))

    threats = assess_threats(world(rmap, p1))

    assert len(threats) == 1
    t = threats[0]
    assert t.enemy_unit_id == enemy.id
    assert t.staleness == 0  # currently visible
    assert my_city.id in t.at_risk_city_ids
    assert Coord(1, 1) in t.projected_reach


def test_distant_city_not_at_risk() -> None:
    """A city beyond the projection horizon is not flagged."""
    p1 = player(1)
    p2 = player(2, "Enemy")
    far_city = City(id=CityId(2), coord=Coord(0, 0), owner=p1)
    rmap = grid_map(["." * 12 for _ in range(3)], cities={Coord(0, 0): far_city})
    place(rmap, Army(UnitId(51), p2, Coord(10, 0)))  # 10 cells away, army speed 1
    reveal(p1, Coord(0, 0), Coord(10, 0))

    threats = assess_threats(world(rmap, p1))

    assert len(threats) == 1
    assert far_city.id not in threats[0].at_risk_city_ids


def test_combat_power_scales_with_hits() -> None:
    """A damaged enemy projects less power than a fresh one of the same kind."""
    p1 = player(1)
    p2 = player(2, "Enemy")
    rmap = grid_map(["~~~~~" for _ in range(5)])
    ship = Battleship(UnitId(60), p2, Coord(2, 2))
    ship.hits = 9  # half of max 18
    place(rmap, ship)
    reveal(p1, Coord(2, 2))

    (threat,) = assess_threats(world(rmap, p1))
    # Battleship strength 4 times 9 hits.
    assert threat.combat_power == 4 * 9


def test_same_enemy_seen_and_remembered_yields_one_fresh_threat() -> None:
    """An enemy that moved from a remembered tile into view must not produce a
    phantom second threat at its stale position; the fresh sighting wins."""
    p1 = player(1)
    p2 = player(2, "Enemy")
    rmap = grid_map(["." * 8 for _ in range(3)])
    enemy = place(rmap, Army(UnitId(70), p2, Coord(5, 1)))  # current position
    reveal(p1, Coord(5, 1))

    # p1 still remembers the enemy at its old cell (1, 1), not currently visible.
    p1.view.remembered[Coord(1, 1)] = RememberedTile(
        coord=Coord(1, 1),
        terrain=TerrainKind.LAND,
        remembered_at=0,
        last_units=[
            UnitSnapshot(
                unit_id=enemy.id,
                kind=UnitKind.ARMY,
                owner_id=p2.id,
                coord=Coord(1, 1),
                hits=1,
            )
        ],
    )

    threats = assess_threats(world(rmap, p1, turn=5))

    assert len(threats) == 1
    assert threats[0].enemy_unit_id == enemy.id
    assert threats[0].origin == Coord(5, 1)  # the fresh position, not (1, 1)
    assert threats[0].staleness == 0


def test_carrier_and_aboard_cargo_both_listed_at_risk() -> None:
    """Two friendly units sharing a cell (carrier + cargo) must both appear in
    at_risk_unit_ids — the earlier coord->id map dropped all but one."""
    p1 = player(1)
    p2 = player(2, "Enemy")
    rmap = grid_map(["~~~~~" for _ in range(5)])
    transport = place(rmap, Transport(UnitId(1), p1, Coord(1, 2)))
    army = place(rmap, Army(UnitId(2), p1, Coord(1, 2)))
    rmap.load_cargo(transport, army)  # army now aboard, coord tracks transport
    place(rmap, Battleship(UnitId(80), p2, Coord(2, 2)))  # adjacent enemy
    reveal(p1, Coord(1, 2), Coord(2, 2))

    (threat,) = assess_threats(world(rmap, p1))

    assert Coord(1, 2) in threat.projected_reach
    assert threat.at_risk_unit_ids == (transport.id, army.id)

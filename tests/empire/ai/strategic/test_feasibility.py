"""Phase 12 — FeasibilityOracle forward checks (purity + correctness)."""

from __future__ import annotations

from empire.ai.strategic.feasibility import FeasibilityOracle
from empire.ai.strategic.goals import ForceComposition
from empire.ai.strategic.intel.report import Threat
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.unit import Army, UnitKind
from tests.empire.ai.strategic.intel._world import (
    grid_map,
    place,
    player,
    reveal_all,
    world,
)

_ORACLE = FeasibilityOracle()


def _threat(power: int, origin: Coord) -> Threat:
    return Threat(
        enemy_unit_id=UnitId(99),
        enemy_owner_id=PlayerId(2),
        kind=UnitKind.ARMY,
        origin=origin,
        combat_power=power,
        staleness=0,
        projected_reach=frozenset({origin}),
        at_risk_city_ids=(),
        at_risk_unit_ids=(),
    )


# --- can_assemble ------------------------------------------------------------


def test_can_assemble_uses_production_window() -> None:
    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(0, 0), owner=p1)
    rmap = grid_map(["....", "...."], cities={Coord(0, 0): city})
    view = world(rmap, p1, turn=0)
    need = ForceComposition.of({UnitKind.ARMY: 1})  # build_time 5

    # 1 city x 5 turns = 5 production points = exactly one Army.
    assert _ORACLE.can_assemble(need, by_turn=5, view=view)
    # 1 city x 4 turns = 4 < 5 -> not in time.
    assert not _ORACLE.can_assemble(need, by_turn=4, view=view)


def test_can_assemble_counts_existing_units() -> None:
    p1 = player(1)
    rmap = grid_map(["...."])
    place(rmap, Army(UnitId(1), p1, Coord(0, 0)))
    view = world(rmap, p1, turn=0)
    need = ForceComposition.of({UnitKind.ARMY: 1})
    # Already have the army — feasible even with no cities and no window.
    assert _ORACLE.can_assemble(need, by_turn=0, view=view)


# --- defensible --------------------------------------------------------------


def test_defensible_compares_local_strength_to_threat() -> None:
    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(3, 3), owner=p1)
    rmap = grid_map(["." * 7 for _ in range(7)], cities={Coord(3, 3): city})
    place(rmap, Army(UnitId(1), p1, Coord(3, 4)))  # strength 1, within radius
    view = world(rmap, p1)

    assert _ORACLE.defensible(city, _threat(1, Coord(0, 3)), view)
    assert not _ORACLE.defensible(city, _threat(2, Coord(0, 3)), view)


# --- reachable ---------------------------------------------------------------


def test_reachable_over_legal_terrain() -> None:
    p1 = player(1)
    rmap = grid_map(["....."])
    view = world(rmap, p1, turn=0)
    # Army speed 1: 3 turns reaches 3 cells away, not 4.
    assert _ORACLE.reachable(Coord(0, 0), Coord(3, 0), UnitKind.ARMY, 3, view)
    assert not _ORACLE.reachable(Coord(0, 0), Coord(4, 0), UnitKind.ARMY, 3, view)


def test_army_cannot_reach_across_water() -> None:
    p1 = player(1)
    rmap = grid_map([".~."])  # land, water, land
    reveal_all(p1, rmap)  # the gap must be observed; unseen cells are optimistic
    view = world(rmap, p1, turn=0)
    # Even with a generous window, an Army can't cross the water gap.
    assert not _ORACLE.reachable(Coord(0, 0), Coord(2, 0), UnitKind.ARMY, 10, view)


# --- purity ------------------------------------------------------------------


def test_oracle_is_pure() -> None:
    p1 = player(1)
    city = City(id=CityId(1), coord=Coord(2, 2), owner=p1)
    rmap = grid_map(["." * 5 for _ in range(5)], cities={Coord(2, 2): city})
    place(rmap, Army(UnitId(1), p1, Coord(2, 3)))
    view = world(rmap, p1, turn=0)
    need = ForceComposition.of({UnitKind.ARMY: 2})
    threat = _threat(1, Coord(0, 2))

    assert _ORACLE.can_assemble(need, 8, view) == _ORACLE.can_assemble(need, 8, view)
    assert _ORACLE.defensible(city, threat, view) == _ORACLE.defensible(city, threat, view)
    r1 = _ORACLE.reachable(Coord(2, 2), Coord(4, 4), UnitKind.ARMY, 5, view)
    r2 = _ORACLE.reachable(Coord(2, 2), Coord(4, 4), UnitKind.ARMY, 5, view)
    assert r1 == r2

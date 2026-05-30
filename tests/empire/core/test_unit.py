"""Phase-2 canary tests for the `Unit` hierarchy and registry.

The big introspection test guarantees every UnitKind resolves to a concrete
class and that every subclass declares every required ClassVar. The golden
table catches typos in the v1 attribute values.
"""

from __future__ import annotations

from typing import Final

import pytest

from empire.core.coord import Coord
from empire.core.identity import PlayerId, UnitId
from empire.core.map import ViewMap
from empire.core.player import Player
from empire.core.tile import TerrainKind
from empire.core.unit import (
    UNIT_REGISTRY,
    Army,
    Battleship,
    Carrier,
    Destroyer,
    Fighter,
    Patrol,
    Submarine,
    Transport,
    Unit,
    UnitKind,
)

# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def player() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


# --- registry / introspection -------------------------------------------------


_REQUIRED_CLASSVARS: Final[tuple[str, ...]] = (
    "kind",
    "max_hits",
    "speed",
    "strength",
    "capacity",
    "base_range",
    "build_time",
    "legal_terrain",
    "symbol",
)


def test_registry_covers_every_kind() -> None:
    assert set(UNIT_REGISTRY.keys()) == set(UnitKind)


def test_registry_classes_match_their_kind() -> None:
    for kind, cls in UNIT_REGISTRY.items():
        assert cls.kind is kind
        assert issubclass(cls, Unit)


def test_every_subclass_declares_every_required_classvar() -> None:
    for cls in UNIT_REGISTRY.values():
        for attr in _REQUIRED_CLASSVARS:
            assert hasattr(cls, attr), f"{cls.__name__} missing {attr}"
            # ClassVar is set, not None (None is not meaningful for any of these).
            value = getattr(cls, attr)
            assert value is not None, f"{cls.__name__}.{attr} is None"


# --- golden attribute table ---------------------------------------------------

# v1 design choices per planning/01-game-rules-spec.md §2. Changing any of
# these is a deliberate game-balance decision; this test catches accidents.
_GOLDEN: Final[dict[UnitKind, dict[str, object]]] = {
    UnitKind.ARMY: {"max_hits": 1, "speed": 1, "strength": 1, "build_time": 5, "symbol": "A"},
    UnitKind.FIGHTER: {"max_hits": 1, "speed": 8, "strength": 1, "build_time": 10, "symbol": "F"},
    UnitKind.PATROL: {"max_hits": 2, "speed": 4, "strength": 1, "build_time": 15, "symbol": "P"},
    UnitKind.DESTROYER: {"max_hits": 3, "speed": 3, "strength": 2, "build_time": 20, "symbol": "D"},
    UnitKind.SUBMARINE: {"max_hits": 2, "speed": 2, "strength": 3, "build_time": 25, "symbol": "S"},
    UnitKind.TRANSPORT: {"max_hits": 3, "speed": 2, "strength": 0, "build_time": 30, "symbol": "T"},
    UnitKind.CARRIER: {"max_hits": 8, "speed": 2, "strength": 1, "build_time": 40, "symbol": "C"},
    UnitKind.BATTLESHIP: {
        "max_hits": 18,
        "speed": 2,
        "strength": 4,
        "build_time": 50,
        "symbol": "B",
    },
    UnitKind.SATELLITE: {"max_hits": 1, "speed": 1, "strength": 0, "build_time": 50, "symbol": "*"},
}


@pytest.mark.parametrize("kind", list(UnitKind))
def test_unit_classvars_match_golden_table(kind: UnitKind) -> None:
    cls = UNIT_REGISTRY[kind]
    expected = _GOLDEN[kind]
    for attr, value in expected.items():
        actual = getattr(cls, attr)
        assert actual == value, f"{cls.__name__}.{attr}: expected {value!r}, got {actual!r}"


# --- behavioral spot checks ---------------------------------------------------


def test_construction_initializes_hits_and_range(player: Player) -> None:
    a = Army(UnitId(1), player, Coord(0, 0))
    assert a.hits == Army.max_hits
    assert a.range == Army.base_range
    f = Fighter(UnitId(2), player, Coord(0, 0))
    assert f.range == Fighter.base_range


def test_coord_read_only_via_property(player: Player) -> None:
    a = Army(UnitId(1), player, Coord(3, 4))
    assert a.coord == Coord(3, 4)
    # Direct assignment to .coord should raise (no setter on the property).
    with pytest.raises(AttributeError):
        a.coord = Coord(5, 5)  # type: ignore[misc]


def test_moves_this_turn_scales_with_damage_using_ceil(player: Player) -> None:
    """Damage scaling rounds UP, not down. Carrier (max_hits=8, speed=2)
    distinguishes ceil from floor at low HP: at hits=3, ceil(6/8)=1 but
    floor(6/8)=0. A unit that can't move is broken; rounding up keeps it
    at least minimally functional until destroyed.
    """
    c = Carrier(UnitId(1), player, Coord(0, 0))  # max_hits=8, speed=2
    assert c.moves_this_turn() == 2  # full HP
    c.hits = 5
    assert c.moves_this_turn() == 2  # ceil(10/8)=2, floor would give 1
    c.hits = 3
    assert c.moves_this_turn() == 1  # ceil(6/8)=1, floor would give 0
    c.hits = 1
    assert c.moves_this_turn() == 1  # ceil(2/8)=1, floor would give 0


def test_effective_capacity_scales_with_damage_using_ceil(player: Player) -> None:
    """Damage scaling on capacity also rounds UP. Carrier (max_hits=8,
    capacity=8) at hits=3 gives ceil(24/8)=3; tests a different ratio
    than the moves case above.
    """
    c = Carrier(UnitId(1), player, Coord(0, 0))  # max_hits=8, capacity=8
    assert c.effective_capacity() == 8
    c.hits = 5
    assert c.effective_capacity() == 5
    c.hits = 3
    assert c.effective_capacity() == 3
    c.hits = 1
    assert c.effective_capacity() == 1  # ceil(8/8)=1


def test_damage_scaling_for_three_hit_units(player: Player) -> None:
    """Sanity at small max_hits: Destroyer (max_hits=3, speed=3)."""
    d = Destroyer(UnitId(1), player, Coord(0, 0))
    assert d.moves_this_turn() == 3
    d.hits = 1
    assert d.moves_this_turn() == 1


def test_effective_capacity_zero_for_non_cargo_units(player: Player) -> None:
    b = Battleship(UnitId(1), player, Coord(0, 0))
    assert b.effective_capacity() == 0
    b.hits = 1
    assert b.effective_capacity() == 0


def test_army_only_legal_on_land_or_city(player: Player) -> None:
    del player
    assert Army.legal_terrain == frozenset({TerrainKind.LAND, TerrainKind.CITY})


def test_sea_units_legal_on_water_or_city() -> None:
    expected = frozenset({TerrainKind.WATER, TerrainKind.CITY})
    for cls in (Patrol, Destroyer, Submarine, Transport, Carrier, Battleship):
        assert cls.legal_terrain == expected


def test_fighter_legal_anywhere() -> None:
    assert Fighter.legal_terrain == frozenset(TerrainKind)

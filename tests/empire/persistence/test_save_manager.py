"""Phase-4 canary tests for `SaveManager` / `V1Serializer`.

Headline canary: a Game saved to JSON and loaded back deep-equals the
original on every observable field, including RNG state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from empire.core.city import City, DefaultOrder, OrderKind, ProductionState
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, RememberedTile, UnitSnapshot, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD, MapProfile, RuleSet
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Battleship, UnitKind
from empire.persistence.save_manager import SaveManager
from empire.persistence.schema_v1 import V1Serializer

# --- builders ----------------------------------------------------------------


def _build_tiny_game() -> Game:
    """A 4x4 game with two players, one city each, one unit each, and a small
    fog state. Exercises every part of the schema.
    """
    p1 = Player(id=PlayerId(1), name="Alice", is_ai=False, view=ViewMap(), color="red")
    p2 = Player(id=PlayerId(2), name="Bob", is_ai=True, view=ViewMap(), color="blue")

    city1 = City(
        id=CityId(1),
        coord=Coord(0, 0),
        owner=p1,
        production=ProductionState(building=UnitKind.ARMY, work=2),
    )
    city1.default_orders[UnitKind.ARMY] = DefaultOrder(OrderKind.ATTACK_NEAREST_ENEMY)
    city2 = City(id=CityId(2), coord=Coord(3, 3), owner=p2)
    neutral = City(id=CityId(3), coord=Coord(2, 2), owner=None)

    tiles: dict[Coord, Tile] = {}
    for x in range(4):
        for y in range(4):
            c = Coord(x, y)
            terrain = TerrainKind.LAND
            if (x, y) in {(0, 3), (1, 3)}:
                terrain = TerrainKind.WATER
            tiles[c] = Tile(coord=c, terrain=terrain)
    for city in (city1, city2, neutral):
        tile = tiles[city.coord]
        tiles[city.coord] = Tile(
            coord=tile.coord,
            terrain=TerrainKind.CITY,
            city=city,
            on_board=tile.on_board,
        )
    real_map = Map(width=4, height=4, tiles=tiles)

    army = Army(UnitId(1), p1, Coord(0, 0))
    army.hits = 1
    real_map.place_unit(army, Coord(0, 0))
    bship = Battleship(UnitId(2), p2, Coord(0, 0))
    bship.hits = 12  # damaged
    real_map.place_unit(bship, Coord(1, 3))  # water tile

    # P1 has seen a tile and remembers it stale.
    p1.view.visible.add(Coord(0, 0))
    p1.view.visible.add(Coord(1, 0))
    p1.view.remembered[Coord(3, 3)] = RememberedTile(
        coord=Coord(3, 3),
        terrain=TerrainKind.CITY,
        remembered_at=4,
        last_units=[
            UnitSnapshot(
                unit_id=UnitId(99),
                kind=UnitKind.DESTROYER,
                owner_id=PlayerId(2),
                coord=Coord(3, 3),
                hits=2,
            ),
        ],
        last_city_owner=PlayerId(2),
    )

    g = Game(rules=STANDARD, real_map=real_map, players=[p1, p2], seed=12345)
    g.turn = 7
    # Advance RNG so getstate() carries some history, not the just-seeded state.
    for _ in range(50):
        g.rng.random()
    return g


# --- round-trip --------------------------------------------------------------


def test_round_trip_preserves_top_level_fields() -> None:
    original = _build_tiny_game()
    payload = V1Serializer().to_dict(original)
    loaded = V1Serializer().from_dict(payload)
    assert loaded.turn == original.turn
    assert loaded.rules.name == original.rules.name
    assert loaded.rules.map_profile.width == original.rules.map_profile.width
    assert loaded.map.width == original.map.width
    assert loaded.map.height == original.map.height


def test_round_trip_preserves_rng_state() -> None:
    original = _build_tiny_game()
    payload = V1Serializer().to_dict(original)

    # Next number from original.
    next_from_original = original.rng.random()

    # Loaded game starts from the same state, so first random should match.
    loaded = V1Serializer().from_dict(payload)
    next_from_loaded = loaded.rng.random()
    assert next_from_loaded == next_from_original


def test_round_trip_preserves_terrain_grid() -> None:
    original = _build_tiny_game()
    loaded = V1Serializer().from_dict(V1Serializer().to_dict(original))
    for x in range(original.map.width):
        for y in range(original.map.height):
            c = Coord(x, y)
            assert loaded.map.terrain_at(c) is original.map.terrain_at(c)


def test_round_trip_preserves_cities_and_owners() -> None:
    original = _build_tiny_game()
    loaded = V1Serializer().from_dict(V1Serializer().to_dict(original))

    by_id_o = {c.id: c for c in original.map.cities()}
    by_id_l = {c.id: c for c in loaded.map.cities()}
    assert set(by_id_o.keys()) == set(by_id_l.keys())

    for city_id, original_city in by_id_o.items():
        loaded_city = by_id_l[city_id]
        assert loaded_city.coord == original_city.coord
        # Owner identity is by PlayerId since loaded Players are fresh objects.
        original_owner_id = original_city.owner.id if original_city.owner else None
        loaded_owner_id = loaded_city.owner.id if loaded_city.owner else None
        assert loaded_owner_id == original_owner_id


def test_round_trip_preserves_production_state() -> None:
    original = _build_tiny_game()
    loaded = V1Serializer().from_dict(V1Serializer().to_dict(original))
    city = next(c for c in loaded.map.cities() if c.id == CityId(1))
    assert city.production.building is UnitKind.ARMY
    assert city.production.work == 2
    assert city.default_orders[UnitKind.ARMY].kind is OrderKind.ATTACK_NEAREST_ENEMY


def test_round_trip_preserves_units_and_their_state() -> None:
    original = _build_tiny_game()
    loaded = V1Serializer().from_dict(V1Serializer().to_dict(original))
    by_id_o = {u.id: u for u in original.map.all_units()}
    by_id_l = {u.id: u for u in loaded.map.all_units()}
    assert set(by_id_l.keys()) == set(by_id_o.keys())
    for unit_id, original_unit in by_id_o.items():
        loaded_unit = by_id_l[unit_id]
        assert loaded_unit.kind is original_unit.kind
        assert loaded_unit.coord == original_unit.coord
        assert loaded_unit.hits == original_unit.hits
        assert loaded_unit.range == original_unit.range
        assert loaded_unit.owner.id == original_unit.owner.id


def test_round_trip_preserves_view_maps() -> None:
    original = _build_tiny_game()
    loaded = V1Serializer().from_dict(V1Serializer().to_dict(original))
    p1_loaded = next(p for p in loaded.players if p.id == PlayerId(1))
    assert p1_loaded.view.visible == {Coord(0, 0), Coord(1, 0)}
    assert Coord(3, 3) in p1_loaded.view.remembered
    rt = p1_loaded.view.remembered[Coord(3, 3)]
    assert rt.remembered_at == 4
    assert rt.last_city_owner == PlayerId(2)
    assert len(rt.last_units) == 1
    assert rt.last_units[0].unit_id == UnitId(99)


def test_topological_load_leaves_no_id_typed_reference_fields() -> None:
    """Confirms entities are fully linked at load time — no `Unit.owner=PlayerId(...)`
    leftovers from a half-linked phase. This catches the bug class that would
    arise from a phase-then-link load strategy.
    """
    original = _build_tiny_game()
    loaded = V1Serializer().from_dict(V1Serializer().to_dict(original))
    for u in loaded.map.all_units():
        assert isinstance(u.owner, Player), f"Unit {u.id}.owner is {type(u.owner).__name__}"
    for c in loaded.map.cities():
        if c.owner is not None:
            assert isinstance(c.owner, Player), f"City {c.id}.owner is {type(c.owner).__name__}"
    for t_coord in [Coord(0, 0), Coord(3, 3)]:
        tile = loaded.map.tile(t_coord)
        if tile.city is not None:
            assert isinstance(tile.city, City), f"Tile {t_coord}.city is {type(tile.city).__name__}"


# --- File I/O ----------------------------------------------------------------


def test_save_and_load_via_file(tmp_path: Path) -> None:
    original = _build_tiny_game()
    path = tmp_path / "save.json"
    SaveManager().save(original, path)
    assert path.exists()
    loaded = SaveManager().load(path)
    assert loaded.turn == original.turn
    assert loaded.rules.name == original.rules.name


def test_save_file_is_pretty_printed_json(tmp_path: Path) -> None:
    """Save files should be human-inspectable (D-005: 'pretty-print on save')."""
    original = _build_tiny_game()
    path = tmp_path / "save.json"
    SaveManager().save(original, path)
    raw = path.read_text()
    assert "\n" in raw  # multi-line
    json.loads(raw)  # valid JSON


# --- Schema-version handling -------------------------------------------------


def test_load_rejects_newer_schema(tmp_path: Path) -> None:
    bad_payload: dict[str, object] = {
        "schema_version": 999,
        "turn": 0,
        "rng_state": [3, [0] * 625, None],
        "rules": {},
        "players": [],
        "cities": [],
        "map": {"width": 0, "height": 0, "tiles": [], "off_board": []},
        "units": [],
    }
    path = tmp_path / "future.json"
    path.write_text(json.dumps(bad_payload))
    with pytest.raises(ValueError, match="newer"):
        SaveManager().load(path)


def test_load_rejects_missing_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "no_version.json"
    path.write_text(json.dumps({"foo": "bar"}))
    with pytest.raises(ValueError, match="schema_version"):
        SaveManager().load(path)


def test_load_rejects_root_that_is_not_an_object(tmp_path: Path) -> None:
    path = tmp_path / "list_root.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError, match="object"):
        SaveManager().load(path)


def test_load_rejects_unknown_intermediate_version_with_no_migration(
    tmp_path: Path,
) -> None:
    """If we're at v3 and the save is v1 but no v1→v2 migration is registered,
    the loader should fail clearly rather than silently mis-deserialize.
    Tested by lying about the current schema temporarily? No — simpler: assert
    that the current SCHEMA_VERSION matches the v1 we're using.

    For Phase 4 with only v1 existing, an old version would always fail
    because no migrations exist. This is the "no migration path" error.
    """
    # Phase 4 has only v1; an older "v0" save would have no migration.
    # We construct a v0 payload (one less than current).
    bad_payload = {"schema_version": V1Serializer.SCHEMA_VERSION - 1}
    path = tmp_path / "v0.json"
    path.write_text(json.dumps(bad_payload))
    with pytest.raises(ValueError, match="No migration"):
        SaveManager().load(path)


# --- Custom RuleSet ----------------------------------------------------------


def test_round_trip_with_custom_ruleset() -> None:
    """A non-STANDARD ruleset (toggles flipped) round-trips correctly."""
    profile = MapProfile(
        width=4, height=4, water_ratio=50, smooth_iterations=2, num_cities=2, min_city_distance=2
    )
    custom = RuleSet(
        name="STACKED",
        map_profile=profile,
        allow_unit_stacking=True,
        army_capture_city_deterministic=True,
        fog_cheat=True,
    )
    tiles = {
        Coord(x, y): Tile(coord=Coord(x, y), terrain=TerrainKind.LAND)
        for x in range(4)
        for y in range(4)
    }
    g = Game(
        rules=custom,
        real_map=Map(width=4, height=4, tiles=tiles),
        players=[Player(id=PlayerId(1), name="P", is_ai=False, view=ViewMap())],
        seed=1,
    )
    loaded = V1Serializer().from_dict(V1Serializer().to_dict(g))
    assert loaded.rules.name == "STACKED"
    assert loaded.rules.allow_unit_stacking is True
    assert loaded.rules.army_capture_city_deterministic is True
    assert loaded.rules.fog_cheat is True
    assert loaded.rules.map_profile.width == 4


def test_round_trip_preserves_standing_orders(tmp_path: Path) -> None:
    """Heading, PatrolPath, and Sentry all survive save/load intact."""
    from empire.core.coord import Direction
    from empire.core.standing_order import Heading, PatrolPath, Sentry

    game = _build_tiny_game()
    units = list(game.map.all_units())
    army = next(u for u in units if isinstance(u, Army))
    battleship = next(u for u in units if isinstance(u, Battleship))
    army.standing_order = Heading(direction=Direction.NE)
    battleship.standing_order = PatrolPath(
        remaining=(Coord(2, 0), Coord(2, 1)),
        original=(Coord(2, 0), Coord(2, 1)),
        reverse_on_end=True,
    )
    # Force a third unit onto Sentry to cover that variant. Build one on
    # the fly via SaveManager round-trip — easier to just decorate.
    army2 = Army(UnitId(99), army.owner, Coord(1, 0))
    game.map.place_unit(army2, Coord(1, 0))
    army2.standing_order = Sentry()

    path = tmp_path / "save.json"
    SaveManager().save(game, path)
    loaded = SaveManager().load(path)

    loaded_units = {int(u.id): u for u in loaded.map.all_units()}
    army_order = loaded_units[int(army.id)].standing_order
    assert isinstance(army_order, Heading)
    assert army_order.direction is Direction.NE
    bs_order = loaded_units[int(battleship.id)].standing_order
    assert isinstance(bs_order, PatrolPath)
    assert bs_order.remaining == (Coord(2, 0), Coord(2, 1))
    assert bs_order.reverse_on_end is True
    assert isinstance(loaded_units[99].standing_order, Sentry)


def test_round_trip_preserves_move_to_default_order(tmp_path: Path) -> None:
    """A MOVE_TO default order keeps its target coord across save/load."""
    game = _build_tiny_game()
    city = next(c for c in game.map.cities() if c.id == CityId(1))
    city.default_orders[UnitKind.FIGHTER] = DefaultOrder(OrderKind.MOVE_TO, Coord(2, 3))

    path = tmp_path / "save.json"
    SaveManager().save(game, path)
    loaded = SaveManager().load(path)

    loaded_city = next(c for c in loaded.map.cities() if c.id == CityId(1))
    order = loaded_city.default_orders[UnitKind.FIGHTER]
    assert order.kind is OrderKind.MOVE_TO
    assert order.target == Coord(2, 3)


def test_round_trip_preserves_cargo_mid_voyage(tmp_path: Path) -> None:
    """A transport with an army aboard at sea round-trips intact (spec §2.2)."""
    from empire.core.unit import Transport

    game = _build_tiny_game()
    # Put a transport on the water tile and load an army aboard it.
    transport = Transport(UnitId(50), game.players[0], Coord(0, 3))
    game.map.place_unit(transport, Coord(0, 3))
    cargo = Army(UnitId(51), game.players[0], Coord(0, 3))
    game.map.place_unit(cargo, Coord(0, 3))
    game.map.load_cargo(transport, cargo)
    assert cargo.is_aboard()

    path = tmp_path / "save.json"
    SaveManager().save(game, path)
    loaded = SaveManager().load(path)

    loaded_transport = loaded.map.unit_by_id(UnitId(50))
    loaded_cargo = loaded.map.unit_by_id(UnitId(51))
    assert loaded_transport is not None and loaded_cargo is not None
    # Carrier still holds the cargo; cargo still aboard and off the index.
    assert loaded_transport.cargo == [UnitId(51)]
    assert loaded_cargo.carried_by == UnitId(50)
    assert loaded_cargo not in list(loaded.map.board_units())
    assert list(loaded.map.units_at(Coord(0, 3))) == [loaded_transport]
    # Byte-identical re-serialization confirms a clean round-trip.
    s = V1Serializer()
    assert s.to_dict(loaded) == s.to_dict(game)

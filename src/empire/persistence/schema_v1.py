"""V1 (de)serializers for the game state.

Save format is JSON. Topological load order per
`planning/04-class-hierarchy.md` §8: RuleSet → Players (with empty
ViewMaps) → Cities (refs Players) → Tiles (with City refs) → Map → Units
(placed via Map; refs Players) → ViewMaps populated → Game.

Controllers are NOT serialized — they're runtime wiring, attached by the
caller after `Game.load()` via `Game.attach_controller`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, cast

from empire.core.city import City, DefaultOrder, OrderKind, ProductionState
from empire.core.coord import Coord, Direction
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, RememberedTile, UnitSnapshot, ViewMap
from empire.core.player import Player
from empire.core.ruleset import MapProfile, RuleSet
from empire.core.standing_order import (
    Heading,
    Loading,
    PatrolPath,
    Sentry,
    StandingOrder,
)
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import UNIT_REGISTRY, Unit, UnitKind


class V1Serializer:
    """Serializes/deserializes `Game` to/from the schema-v1 dict format."""

    SCHEMA_VERSION: ClassVar[int] = 1

    # ---- top-level --------------------------------------------------------

    def to_dict(self, game: Game) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "turn": game.turn,
            "next_unit_id": game.next_unit_id,
            "rng_state": _pack_rng_state(game.rng.getstate()),
            "rules": self._rules_to_dict(game.rules),
            "players": [self._player_to_dict(p) for p in game.players],
            "cities": [self._city_to_dict(c) for c in game.map.cities()],
            "map": self._map_to_dict(game.map),
            "units": [self._unit_to_dict(u) for u in game.map.all_units()],
        }

    def from_dict(self, payload: Mapping[str, Any]) -> Game:
        if payload.get("schema_version") != self.SCHEMA_VERSION:
            raise ValueError(
                f"V1Serializer.from_dict requires schema_version={self.SCHEMA_VERSION}, "
                f"got {payload.get('schema_version')!r}"
            )

        # 1. RuleSet (no refs).
        rules = self._rules_from_dict(payload["rules"])

        # 2. Players (with empty ViewMaps; populated in step 6).
        players: list[Player] = [self._player_from_dict(p) for p in payload["players"]]
        players_by_id: dict[PlayerId, Player] = {p.id: p for p in players}

        # 3. Cities (refs Players).
        cities: list[City] = [
            self._city_from_dict(c, players_by_id) for c in payload["cities"]
        ]
        cities_by_coord: dict[Coord, City] = {c.coord: c for c in cities}

        # 4. Tiles (with City refs).
        tiles = self._tiles_from_dict(payload["map"], cities_by_coord)

        # 5. Map.
        real_map = Map(
            width=int(payload["map"]["width"]),
            height=int(payload["map"]["height"]),
            tiles=tiles,
        )

        # 6. Units (placed via Map). Aboard cargo joins the registry but not
        # the spatial index — it occupies no cell independently (spec §2.2).
        for unit_payload in payload["units"]:
            unit = self._unit_from_dict(unit_payload, players_by_id)
            if unit.carried_by is None:
                real_map.place_unit(unit, unit.coord)
            else:
                real_map.add_aboard_unit(unit)

        # 7. ViewMaps populated.
        for player_payload in payload["players"]:
            player = players_by_id[PlayerId(int(player_payload["id"]))]
            self._populate_view_map(player.view, player_payload["view"])

        # 8. Game. `next_unit_id` is optional for pre-counter saves; Game
        # falls back to max-existing-id derivation when absent.
        raw_next = payload.get("next_unit_id")
        game = Game(
            rules=rules,
            real_map=real_map,
            players=players,
            seed=None,
            next_unit_id=None if raw_next is None else int(raw_next),
        )
        game.rng.setstate(_unpack_rng_state(payload["rng_state"]))
        game.turn = int(payload["turn"])
        return game

    # ---- RuleSet / MapProfile ---------------------------------------------

    def _rules_to_dict(self, r: RuleSet) -> dict[str, Any]:
        return {
            "name": r.name,
            "map_profile": self._profile_to_dict(r.map_profile),
            "fighter_base_range": r.fighter_base_range,
            "satellite_range": r.satellite_range,
            "production_change_penalty_divisor": r.production_change_penalty_divisor,
            "allow_unit_stacking": r.allow_unit_stacking,
            "army_capture_city_deterministic": r.army_capture_city_deterministic,
            "asymmetric_combat_bonus": r.asymmetric_combat_bonus,
            "seven_terrain_types": r.seven_terrain_types,
            "transport_escort_required_for_unload": r.transport_escort_required_for_unload,
            "city_artillery_range": r.city_artillery_range,
            "city_artillery_hit_prob": r.city_artillery_hit_prob,
            "city_artillery_pin_prob": r.city_artillery_pin_prob,
            "fog_cheat": r.fog_cheat,
        }

    def _rules_from_dict(self, d: Mapping[str, Any]) -> RuleSet:
        return RuleSet(
            name=str(d["name"]),
            map_profile=self._profile_from_dict(d["map_profile"]),
            fighter_base_range=int(d["fighter_base_range"]),
            satellite_range=int(d["satellite_range"]),
            production_change_penalty_divisor=int(d["production_change_penalty_divisor"]),
            allow_unit_stacking=bool(d["allow_unit_stacking"]),
            army_capture_city_deterministic=bool(d["army_capture_city_deterministic"]),
            asymmetric_combat_bonus=float(d["asymmetric_combat_bonus"]),
            seven_terrain_types=bool(d["seven_terrain_types"]),
            transport_escort_required_for_unload=bool(
                d["transport_escort_required_for_unload"]
            ),
            # Artillery fields are absent from older saves; default to
            # the inert/classic values.
            city_artillery_range=int(d.get("city_artillery_range", 0)),
            city_artillery_hit_prob=float(d.get("city_artillery_hit_prob", 0.5)),
            city_artillery_pin_prob=float(d.get("city_artillery_pin_prob", 0.5)),
            fog_cheat=bool(d["fog_cheat"]),
        )

    def _profile_to_dict(self, p: MapProfile) -> dict[str, Any]:
        return {
            "width": p.width,
            "height": p.height,
            "water_ratio": p.water_ratio,
            "smooth_iterations": p.smooth_iterations,
            "num_cities": p.num_cities,
            "min_city_distance": p.min_city_distance,
        }

    def _profile_from_dict(self, d: Mapping[str, Any]) -> MapProfile:
        return MapProfile(
            width=int(d["width"]),
            height=int(d["height"]),
            water_ratio=int(d["water_ratio"]),
            smooth_iterations=int(d["smooth_iterations"]),
            num_cities=int(d["num_cities"]),
            min_city_distance=int(d["min_city_distance"]),
        )

    # ---- Player / ViewMap -------------------------------------------------

    def _player_to_dict(self, p: Player) -> dict[str, Any]:
        return {
            "id": int(p.id),
            "name": p.name,
            "is_ai": p.is_ai,
            "color": p.color,
            "view": {
                "visible": [[c.x, c.y] for c in sorted(p.view.visible, key=_coord_key)],
                "remembered": [
                    self._remembered_to_dict(rt)
                    for rt in sorted(p.view.remembered.values(), key=lambda r: _coord_key(r.coord))
                ],
            },
        }

    def _player_from_dict(self, d: Mapping[str, Any]) -> Player:
        return Player(
            id=PlayerId(int(d["id"])),
            name=str(d["name"]),
            is_ai=bool(d["is_ai"]),
            view=ViewMap(),  # populated in step 7
            color=str(d["color"]),
        )

    def _populate_view_map(self, view: ViewMap, d: Mapping[str, Any]) -> None:
        for xy in d.get("visible", []):
            view.visible.add(Coord(int(xy[0]), int(xy[1])))
        for rt_payload in d.get("remembered", []):
            rt = self._remembered_from_dict(rt_payload)
            view.remembered[rt.coord] = rt

    def _remembered_to_dict(self, rt: RememberedTile) -> dict[str, Any]:
        return {
            "x": rt.coord.x,
            "y": rt.coord.y,
            "terrain": rt.terrain.value,
            "remembered_at": rt.remembered_at,
            "last_units": [self._snapshot_to_dict(s) for s in rt.last_units],
            "last_city_owner": (
                int(rt.last_city_owner) if rt.last_city_owner is not None else None
            ),
        }

    def _remembered_from_dict(self, d: Mapping[str, Any]) -> RememberedTile:
        owner = d.get("last_city_owner")
        return RememberedTile(
            coord=Coord(int(d["x"]), int(d["y"])),
            terrain=TerrainKind(d["terrain"]),
            remembered_at=int(d["remembered_at"]),
            last_units=[self._snapshot_from_dict(s) for s in d.get("last_units", [])],
            last_city_owner=PlayerId(int(owner)) if owner is not None else None,
        )

    def _snapshot_to_dict(self, s: UnitSnapshot) -> dict[str, Any]:
        return {
            "unit_id": int(s.unit_id),
            "kind": s.kind.value,
            "owner_id": int(s.owner_id),
            "x": s.coord.x,
            "y": s.coord.y,
            "hits": s.hits,
        }

    def _snapshot_from_dict(self, d: Mapping[str, Any]) -> UnitSnapshot:
        return UnitSnapshot(
            unit_id=UnitId(int(d["unit_id"])),
            kind=UnitKind(d["kind"]),
            owner_id=PlayerId(int(d["owner_id"])),
            coord=Coord(int(d["x"]), int(d["y"])),
            hits=int(d["hits"]),
        )

    # ---- City / ProductionState -------------------------------------------

    def _city_to_dict(self, c: City) -> dict[str, Any]:
        return {
            "id": int(c.id),
            "x": c.coord.x,
            "y": c.coord.y,
            "owner_id": int(c.owner.id) if c.owner is not None else None,
            "production": self._production_to_dict(c.production),
            "default_orders": {
                kind.value: self._default_order_to_dict(order)
                for kind, order in c.default_orders.items()
            },
        }

    def _default_order_to_dict(self, order: DefaultOrder) -> dict[str, Any]:
        return {
            "kind": order.kind.value,
            "target": (
                [order.target.x, order.target.y] if order.target is not None else None
            ),
        }

    def _default_order_from_dict(self, d: Any) -> DefaultOrder:
        # Back-compat: an older save stored the bare OrderKind string.
        if isinstance(d, str):
            return DefaultOrder(OrderKind(d))
        target = d.get("target")
        return DefaultOrder(
            kind=OrderKind(d["kind"]),
            target=Coord(int(target[0]), int(target[1])) if target is not None else None,
        )

    def _city_from_dict(
        self, d: Mapping[str, Any], players_by_id: Mapping[PlayerId, Player]
    ) -> City:
        owner_id = d.get("owner_id")
        owner = players_by_id[PlayerId(int(owner_id))] if owner_id is not None else None
        orders = {
            UnitKind(kind_str): self._default_order_from_dict(order_payload)
            for kind_str, order_payload in d.get("default_orders", {}).items()
        }
        return City(
            id=CityId(int(d["id"])),
            coord=Coord(int(d["x"]), int(d["y"])),
            owner=owner,
            production=self._production_from_dict(d["production"]),
            default_orders=orders,
        )

    def _production_to_dict(self, p: ProductionState) -> dict[str, Any]:
        return {
            "building": p.building.value if p.building is not None else None,
            "work": p.work,
        }

    def _production_from_dict(self, d: Mapping[str, Any]) -> ProductionState:
        building = d.get("building")
        return ProductionState(
            building=UnitKind(building) if building is not None else None,
            work=int(d["work"]),
        )

    # ---- Map / Tile -------------------------------------------------------

    def _map_to_dict(self, m: Map) -> dict[str, Any]:
        # Flat row-major array of terrain codes. City tiles are stored as
        # "C"; the city-to-coord link comes from the cities array (separate
        # top-level field) so we don't duplicate that data here.
        tiles: list[str] = []
        off_board: list[list[int]] = []
        for y in range(m.height):
            for x in range(m.width):
                tile = m.tile(Coord(x, y))
                tiles.append(tile.terrain.value)
                if not tile.on_board:
                    off_board.append([x, y])
        return {
            "width": m.width,
            "height": m.height,
            "tiles": tiles,
            "off_board": off_board,
        }

    def _tiles_from_dict(
        self,
        d: Mapping[str, Any],
        cities_by_coord: Mapping[Coord, City],
    ) -> dict[Coord, Tile]:
        width = int(d["width"])
        height = int(d["height"])
        flat = cast(list[str], d["tiles"])
        if len(flat) != width * height:
            raise ValueError(
                f"Map tiles array has {len(flat)} entries; expected {width * height}"
            )
        off_board_set = {(int(xy[0]), int(xy[1])) for xy in d.get("off_board", [])}
        tiles: dict[Coord, Tile] = {}
        idx = 0
        for y in range(height):
            for x in range(width):
                c = Coord(x, y)
                terrain = TerrainKind(flat[idx])
                city = cities_by_coord.get(c) if terrain is TerrainKind.CITY else None
                tiles[c] = Tile(
                    coord=c,
                    terrain=terrain,
                    city=city,
                    on_board=(x, y) not in off_board_set,
                )
                idx += 1
        return tiles

    # ---- Unit -------------------------------------------------------------

    def _unit_to_dict(self, u: Unit) -> dict[str, Any]:
        return {
            "id": int(u.id),
            "kind": u.kind.value,
            "owner_id": int(u.owner.id),
            "x": u.coord.x,
            "y": u.coord.y,
            "hits": u.hits,
            "range": u.range,
            "standing_order": self._standing_order_to_dict(u.standing_order),
            "cargo": [int(cid) for cid in u.cargo],
            "carried_by": int(u.carried_by) if u.carried_by is not None else None,
            "loaded_this_turn": u.loaded_this_turn,
            "orbit_direction": (
                u.orbit_direction.name if u.orbit_direction is not None else None
            ),
        }

    def _unit_from_dict(
        self, d: Mapping[str, Any], players_by_id: Mapping[PlayerId, Player]
    ) -> Unit:
        kind = UnitKind(d["kind"])
        cls = UNIT_REGISTRY[kind]
        owner = players_by_id[PlayerId(int(d["owner_id"]))]
        coord = Coord(int(d["x"]), int(d["y"]))
        unit = cls(UnitId(int(d["id"])), owner, coord)
        unit.hits = int(d["hits"])
        unit.range = int(d["range"])
        unit.standing_order = self._standing_order_from_dict(d.get("standing_order"))
        unit.cargo = [UnitId(int(cid)) for cid in d.get("cargo", [])]
        carried_by = d.get("carried_by")
        unit.carried_by = UnitId(int(carried_by)) if carried_by is not None else None
        unit.loaded_this_turn = bool(d.get("loaded_this_turn", False))
        orbit = d.get("orbit_direction")
        unit.orbit_direction = Direction[str(orbit)] if orbit is not None else None
        return unit

    def _standing_order_to_dict(
        self, order: StandingOrder | None
    ) -> dict[str, Any] | None:
        if order is None:
            return None
        if isinstance(order, Heading):
            return {"kind": "heading", "direction": order.direction.name}
        if isinstance(order, PatrolPath):
            return {
                "kind": "patrol",
                "remaining": [[c.x, c.y] for c in order.remaining],
                "original": [[c.x, c.y] for c in order.original],
                "loop": order.loop,
            }
        if isinstance(order, Loading):
            return {"kind": "loading"}
        # Sentry — the only remaining variant.
        return {"kind": "sentry"}

    def _standing_order_from_dict(
        self, d: Mapping[str, Any] | None
    ) -> StandingOrder | None:
        if d is None:
            return None
        kind = d["kind"]
        if kind == "heading":
            return Heading(direction=Direction[str(d["direction"])])
        if kind == "patrol":
            return PatrolPath(
                remaining=tuple(Coord(int(c[0]), int(c[1])) for c in d["remaining"]),
                original=tuple(Coord(int(c[0]), int(c[1])) for c in d["original"]),
                # "reverse_on_end" is the legacy name for the loop flag.
                loop=bool(d.get("loop", d.get("reverse_on_end", False))),
            )
        if kind == "loading":
            return Loading()
        if kind == "sentry":
            return Sentry()
        raise ValueError(f"Unknown StandingOrder kind in save: {kind!r}")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _coord_key(c: Coord) -> tuple[int, int]:
    """Sort key for deterministic JSON output."""
    return (c.x, c.y)


def _pack_rng_state(state: tuple[Any, ...]) -> list[Any]:
    """Convert `random.getstate()` tuple to a JSON-safe list-of-list-of-int form."""
    version, internal, gauss = state
    return [version, list(internal), gauss]


def _unpack_rng_state(packed: list[Any]) -> tuple[Any, ...]:
    """Inverse of `_pack_rng_state`. Returns the tuple `random.setstate()` requires."""
    version, internal, gauss = packed
    return (int(version), tuple(int(x) for x in internal), gauss)

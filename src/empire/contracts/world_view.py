"""`WorldView`: the AI-facing live-filtered view of the world.

A `WorldView` holds references to the real `Map`, the player's `ViewMap`,
the current turn, and the active `RuleSet`. It exposes only what fog of war
permits — visible tiles, remembered tiles, own assets, and known enemy/
neutral assets. No copies of the underlying state are made; readers see
current state every time they query.

See `planning/03-ai-design.md` §1 for the design rationale and
`planning/04-class-hierarchy.md` §7 for class skeletons.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import PlayerId
from empire.core.map import Map, RememberedTile, UnitSnapshot
from empire.core.player import Player
from empire.core.ruleset import RuleSet
from empire.core.tile import Tile
from empire.core.unit import Unit


@dataclass(frozen=True, slots=True)
class KnownEnemyUnit:
    """An enemy unit the player knows about: either currently visible or remembered.

    `seen_at_turn` equals the current turn iff this unit is currently
    visible; for remembered sightings it's the turn the unit was last in
    view. Behaviors should treat older sightings as more uncertain.
    """

    snapshot: UnitSnapshot
    seen_at_turn: int


class WorldView:
    """Live-filtered view of the world for one player.

    Per `planning/03-ai-design.md` §1, this is *not* a snapshot — it holds
    references and re-derives its output on each query. The frozenness lives
    in the artifacts produced by callers (e.g., `IntelReport`), not here.
    """

    def __init__(
        self,
        real_map: Map,
        player: Player,
        turn: int,
        rules: RuleSet,
    ) -> None:
        self._map = real_map
        self._player = player
        self._turn = turn
        self._rules = rules

    # ---- scalar context ----------------------------------------------------

    @property
    def own_player(self) -> Player:
        return self._player

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def rules(self) -> RuleSet:
        return self._rules

    # ---- tiles -------------------------------------------------------------

    def visible_tiles(self) -> Mapping[Coord, Tile]:
        """Tiles the player can currently see."""
        return {c: self._map.tile(c) for c in self._player.view.visible if self._map.in_bounds(c)}

    def remembered_tiles(self) -> Mapping[Coord, RememberedTile]:
        """Tiles the player has seen at some point but cannot see now."""
        return self._player.view.remembered

    def is_visible(self, c: Coord) -> bool:
        """True if `c` is currently in the player's visible set."""
        return c in self._player.view.visible

    # ---- own assets --------------------------------------------------------

    @property
    def own_cities(self) -> list[City]:
        return [c for c in self._map.cities() if c.owner is self._player]

    @property
    def own_units(self) -> list[Unit]:
        return [u for u in self._map.all_units() if u.owner is self._player]

    # ---- visible enemy/neutral assets --------------------------------------

    @property
    def known_enemy_cities(self) -> list[City]:
        """Enemy-owned cities whose coords are visible OR remembered."""
        result: list[City] = []
        view = self._player.view
        for city in self._map.cities():
            if city.owner is None or city.owner is self._player:
                continue
            if city.coord in view.visible or city.coord in view.remembered:
                result.append(city)
        return result

    @property
    def neutral_cities(self) -> list[City]:
        """Unowned cities whose coords are visible OR remembered."""
        result: list[City] = []
        view = self._player.view
        for city in self._map.cities():
            if city.owner is not None:
                continue
            if city.coord in view.visible or city.coord in view.remembered:
                result.append(city)
        return result

    @property
    def known_enemy_units(self) -> list[KnownEnemyUnit]:
        """All enemy units the player knows about.

        Visible enemies are included with `seen_at_turn = self.turn`. Remembered
        enemy units (extracted from `RememberedTile.last_units`) are included
        with their original `remembered_at` turn.
        """
        view = self._player.view
        result: list[KnownEnemyUnit] = []
        own_id: PlayerId = self._player.id

        # Currently visible enemies.
        for unit in self._map.all_units():
            if unit.owner is self._player:
                continue
            if unit.coord in view.visible:
                snap = UnitSnapshot(
                    unit_id=unit.id,
                    kind=unit.kind,
                    owner_id=unit.owner.id,
                    coord=unit.coord,
                    hits=unit.hits,
                )
                result.append(KnownEnemyUnit(snapshot=snap, seen_at_turn=self._turn))

        # Remembered enemies (from tiles we used to see).
        for tile_coord, remembered in view.remembered.items():
            if tile_coord in view.visible:
                continue  # superseded by current visibility
            for snap in remembered.last_units:
                if snap.owner_id == own_id:
                    continue
                result.append(KnownEnemyUnit(snapshot=snap, seen_at_turn=remembered.remembered_at))

        return result

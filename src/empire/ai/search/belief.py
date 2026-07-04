"""`BeliefBuilder`: a playable `Game` built from one player's fog view.

`SearchAI` is fog-honest: playouts simulate the world the searcher *knows*,
never the real map. The belief game contains:

- **Terrain**: visible cells as they are, remembered cells as last seen,
  never-seen cells **inferred from the nearest seen cell's domain** (land vs
  water). NOT blanket land — that turned unexplored ocean into walkable ground
  and wrecked naval geometry (a crossing looked like a march). The `on_board`
  frame is structural knowledge (map dimensions known a priori), not intel.
- **Cities**: the view's own/known-enemy/neutral city sets (the same fog
  filter every other AI layer uses). Own production state is copied; a
  known enemy city is modeled as building armies from scratch — the horde
  opponent model's default.
- **Units**: own units in full; enemy units at their last-known positions
  (visible sightings supersede remembered snapshots of the same unit).
- **Information**: the SEARCHER keeps its real fog (visible + remembered as
  the player actually knows them), so the playout must *scout* to learn more —
  reconnaissance has value, exploration is not free. (Previously both players
  were all-seeing; because `update_from_scan` spills old-visible into
  `remembered`, that left the whole map permanently "seen", so scouting could
  never change the position and the search valued recon at zero.) The opponent
  model is left all-seeing of the belief world — the strongest, most
  conservative version of itself.

Stale snapshots are trusted at face value in v1 (no decay); the projection
of unseen enemy production is the opponent model's job during the playout.
"""

from __future__ import annotations

from collections import deque

from empire.contracts.world_view import WorldView
from empire.core.city import City, ProductionState
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import PlayerId, UnitId
from empire.core.map import Map, UnitSnapshot, ViewMap
from empire.core.player import Player
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import UNIT_REGISTRY, UnitKind

# Distinct large primes keep per-turn playout RNG streams decorrelated
# across turns while staying deterministic (no wall-clock seeding).
_SEED_STRIDE = 7919


class BeliefBuilder:
    """Builds the searcher's belief game; one per real turn, cloned per
    candidate by `PlayoutModel`."""

    def build(self, view: WorldView) -> Game:
        """A self-contained `Game` representing everything `view` knows.

        The RNG is seeded deterministically from the turn number, so every
        candidate playout cloned from this belief shares one random stream —
        common random numbers, which cancels luck *between* candidates.
        """
        me = view.own_player
        belief_me = Player(
            id=me.id, name=me.name, is_ai=True, view=ViewMap(), color=me.color
        )
        enemy_id = self._known_enemy_id(view)
        players: list[Player] = [belief_me]
        belief_enemy: Player | None = None
        if enemy_id is not None:
            belief_enemy = Player(
                id=enemy_id, name="Believed enemy", is_ai=True,
                view=ViewMap(), color="default",
            )
            players.append(belief_enemy)
        players.sort(key=lambda p: int(p.id))  # canonical turn order

        by_id: dict[PlayerId, Player] = {p.id: p for p in players}
        real_map = view.real_map()
        seen = view.own_player.view.seen

        # Seen cells as known; unseen cells inferred from the nearest seen
        # cell's domain (land/water) so unexplored ocean stays ocean.
        inferred = self._infer_terrain(view, real_map, seen)
        tiles: dict[Coord, Tile] = {}
        for y in range(real_map.height):
            for x in range(real_map.width):
                c = Coord(x, y)
                tiles[c] = Tile(
                    coord=c,
                    terrain=inferred[c],
                    on_board=real_map.tile(c).on_board,
                )

        # Cities — same fog filter the other AI layers read.
        for city in view.own_cities:
            production = ProductionState(
                building=city.production.building, work=city.production.work
            )
            self._place_city(
                tiles,
                City(id=city.id, coord=city.coord, owner=belief_me,
                     production=production),
            )
        for city in view.known_enemy_cities:
            owner = by_id.get(city.owner.id) if city.owner is not None else None
            self._place_city(
                tiles,
                City(
                    id=city.id, coord=city.coord, owner=owner,
                    production=ProductionState(building=UnitKind.ARMY, work=0),
                ),
            )
        for city in view.neutral_cities:
            self._place_city(
                tiles, City(id=city.id, coord=city.coord, owner=None)
            )

        belief_map = Map(width=real_map.width, height=real_map.height, tiles=tiles)

        # Own units in full. Aboard cargo is skipped in v1 (the land-brawl
        # proving ground has no transports; revisit with the naval phase).
        for unit in view.own_units:
            if unit.carried_by is not None:
                continue
            copy = UNIT_REGISTRY[unit.kind](unit.id, belief_me, unit.coord)
            copy.hits = unit.hits
            belief_map.place_unit(copy, unit.coord)

        # Enemy units at last-known positions; visible supersedes remembered.
        for snap in self._best_sightings(view):
            owner = by_id.get(snap.owner_id)
            if owner is None:
                continue
            if belief_map.units_at(snap.coord):
                continue  # never synthesize a stack from stale intel
            copy = UNIT_REGISTRY[snap.kind](snap.unit_id, owner, snap.coord)
            copy.hits = snap.hits
            belief_map.place_unit(copy, snap.coord)

        # Fog discipline (§9.2, corrected): the searcher keeps its REAL fog, so
        # the playout must scout to learn more (recon has value). The opponent
        # model is left all-seeing of the belief world — strongest, conservative.
        me_view = view.own_player.view
        belief_me.view.visible = set(me_view.visible)
        belief_me.view.remembered = dict(me_view.remembered)
        if belief_enemy is not None:
            belief_enemy.view.visible = {
                Coord(x, y)
                for x in range(belief_map.width)
                for y in range(belief_map.height)
            }

        game = Game(
            rules=view.rules,
            real_map=belief_map,
            players=players,
            seed=view.turn * _SEED_STRIDE + 17,
        )
        game.turn = view.turn
        return game

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _infer_terrain(
        view: WorldView, real_map: Map, seen
    ) -> dict[Coord, TerrainKind]:
        """Terrain for every cell: seen cells as known; unseen cells take the
        domain (land/water) of the nearest seen cell, via one multi-source BFS
        seeded with all seen cells. Keeps unexplored ocean as ocean instead of
        the old blanket-land guess. City tiles propagate as LAND (a city sits on
        land); their own tile keeps CITY terrain (a city is re-placed later).
        Cells with no seen cell anywhere (fully blind) default to land."""
        width, height = real_map.width, real_map.height
        terr: dict[Coord, TerrainKind] = {}
        queue: deque[tuple[Coord, TerrainKind]] = deque()
        for y in range(height):
            for x in range(width):
                c = Coord(x, y)
                if seen(c):
                    tile = view.terrain_at(c)
                    assert tile is not None  # seen ⇒ terrain known
                    terr[c] = tile.terrain
                    domain = (
                        TerrainKind.WATER
                        if tile.terrain is TerrainKind.WATER
                        else TerrainKind.LAND
                    )
                    queue.append((c, domain))
        while queue:
            c, domain = queue.popleft()
            for n in c.neighbors():
                if real_map.in_bounds(n) and n not in terr:
                    terr[n] = domain  # nearest-seen domain wins (BFS order)
                    queue.append((n, domain))
        for y in range(height):
            for x in range(width):
                terr.setdefault(Coord(x, y), TerrainKind.LAND)  # fully blind
        return terr

    @staticmethod
    def _place_city(tiles: dict[Coord, Tile], city: City) -> None:
        prior = tiles[city.coord]
        tiles[city.coord] = Tile(
            coord=city.coord,
            terrain=TerrainKind.CITY,
            city=city,
            on_board=prior.on_board,
        )

    @staticmethod
    def _known_enemy_id(view: WorldView) -> PlayerId | None:
        """The opposing player id, if any sighting reveals one (2-player v1)."""
        for city in view.known_enemy_cities:
            if city.owner is not None:
                return city.owner.id
        for known in view.known_enemy_units:
            return known.snapshot.owner_id
        return None

    @staticmethod
    def _best_sightings(view: WorldView) -> list[UnitSnapshot]:
        """One snapshot per enemy unit id, keeping the most recent sighting."""
        best: dict[UnitId, tuple[int, UnitSnapshot]] = {}
        for known in view.known_enemy_units:
            snap = known.snapshot
            current = best.get(snap.unit_id)
            if current is None or known.seen_at_turn > current[0]:
                best[snap.unit_id] = (known.seen_at_turn, snap)
        return [snap for _, snap in best.values()]

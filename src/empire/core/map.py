"""`Map` (the authoritative grid) plus `ViewMap` and friends (per-player fog).

Phase-2 scope: structural plus the spatial-index discipline. `Map.move_unit`
maintains the spatial index but does *not* validate against `RuleSet`
(terrain legality, occupancy, etc.) — that lands in Phase 8. `ViewMap` is
naive (full visibility) for now; real fog-of-war logic lands in Phase 8.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.tile import TerrainKind, Tile

if TYPE_CHECKING:
    from empire.core.city import City
    from empire.core.unit import Unit, UnitKind


# -----------------------------------------------------------------------------
# Fog-of-war snapshots
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UnitSnapshot:
    """Stale information about a unit observed at some past turn."""

    unit_id: UnitId
    kind: UnitKind
    owner_id: PlayerId
    coord: Coord
    hits: int


def _empty_unit_snapshots() -> list[UnitSnapshot]:
    return []


@dataclass(slots=True)
class RememberedTile:
    """A tile the player has seen at some point but cannot currently see."""

    coord: Coord
    terrain: TerrainKind
    remembered_at: int  # turn number when this snapshot was captured
    last_units: list[UnitSnapshot] = field(default_factory=_empty_unit_snapshots)
    last_city_owner: PlayerId | None = None


class ViewMap:
    """A player's fog-filtered view of the world.

    Owns two structures:
      - `visible`: coordinates the player can see *this* turn.
      - `remembered`: coordinates the player has seen at some past turn,
        with stale snapshot data.

    `seen()` reports whether a coord is in either set. Population of these
    sets via scan logic (per-unit scan radii, city scan, satellite vision)
    lands in Phase 8; `update_from_scan()` is a stub until then.
    """

    def __init__(self) -> None:
        self.remembered: dict[Coord, RememberedTile] = {}
        self.visible: set[Coord] = set()

    def seen(self, c: Coord) -> bool:
        """True iff `c` is currently visible OR remembered from some past turn."""
        return c in self.visible or c in self.remembered

    def update_from_scan(
        self,
        scanned: Iterable[Coord],
        real_map: Map,
        turn: int,
    ) -> None:
        """Replace the visible set with `scanned`, committing any cells that
        were previously visible but no longer are into `remembered`.

        Cells that newly become visible are removed from `remembered` (the
        live state supersedes any stale snapshot).
        """
        new_visible: set[Coord] = {c for c in scanned if real_map.in_bounds(c)}

        # Cells leaving visibility: snapshot to remembered.
        for c in self.visible - new_visible:
            tile = real_map.tile(c)
            unit_snapshots: list[UnitSnapshot] = [
                UnitSnapshot(
                    unit_id=u.id,
                    kind=u.kind,
                    owner_id=u.owner.id,
                    coord=u.coord,
                    hits=u.hits,
                )
                for u in real_map.units_at(c)
            ]
            last_city_owner: PlayerId | None = None
            if tile.city is not None and tile.city.owner is not None:
                last_city_owner = tile.city.owner.id
            self.remembered[c] = RememberedTile(
                coord=c,
                terrain=tile.terrain,
                remembered_at=turn,
                last_units=unit_snapshots,
                last_city_owner=last_city_owner,
            )

        # Cells newly visible: drop any remembered snapshot (live state wins).
        for c in new_visible - self.visible:
            self.remembered.pop(c, None)

        self.visible = new_visible

    def render_char(self, c: Coord) -> str:
        """Phase 8: minimal renderer. Phase 17 will produce the full TUI glyphs."""
        del c
        return " "


# -----------------------------------------------------------------------------
# Map: the authoritative grid + spatial index
# -----------------------------------------------------------------------------


class Map:
    """The authoritative grid of tiles and the spatial index over units.

    The grid is fixed at construction; tiles are not added or removed during
    play. The spatial index (`_units_by_coord`) is *derived state* — `Map`
    is the sole writer; `Unit.coord` is the canonical source of truth for
    a unit's position, mutated only via this class's methods.

    Phase 2: `place_unit / move_unit / remove_unit` maintain the index but do
    not validate against `RuleSet`. Phase 8 adds rule validation.
    """

    def __init__(self, width: int, height: int, tiles: dict[Coord, Tile]) -> None:
        self.width: int = width
        self.height: int = height
        self._tiles: dict[Coord, Tile] = tiles
        self._units_by_coord: dict[Coord, list[Unit]] = {}
        self._all_units: dict[UnitId, Unit] = {}

    # ---- terrain / tile queries -----------------------------------------

    def in_bounds(self, c: Coord) -> bool:
        return 0 <= c.x < self.width and 0 <= c.y < self.height

    def tile(self, c: Coord) -> Tile:
        return self._tiles[c]

    def terrain_at(self, c: Coord) -> TerrainKind:
        return self._tiles[c].terrain

    def neighbors(self, c: Coord) -> Iterator[Tile]:
        """Yield each in-bounds neighboring tile (out-of-bounds neighbors skipped)."""
        for n in c.neighbors():
            if self.in_bounds(n):
                yield self._tiles[n]

    # ---- city queries ---------------------------------------------------

    def cities(self) -> Iterator[City]:
        for tile in self._tiles.values():
            if tile.city is not None:
                yield tile.city

    def city_by_id(self, city_id: CityId) -> City | None:
        for c in self.cities():
            if c.id == city_id:
                return c
        return None

    # ---- unit queries ---------------------------------------------------

    def units_at(self, c: Coord) -> Sequence[Unit]:
        """Return the units currently occupying `c`. Empty tuple if none."""
        return tuple(self._units_by_coord.get(c, ()))

    def all_units(self) -> Iterator[Unit]:
        return iter(self._all_units.values())

    def unit_by_id(self, unit_id: UnitId) -> Unit | None:
        return self._all_units.get(unit_id)

    # ---- unit mutations (the only place Unit positions change) ---------

    def place_unit(self, u: Unit, c: Coord) -> None:
        """Register a unit at coord `c`. The unit's coord is set as a side effect."""
        # Map is the privileged mutator of Unit position. The _set_coord method
        # is single-underscore by convention so callers know not to use it; only
        # Map.place_unit / Map.move_unit may call it.
        u._set_coord(c)  # pyright: ignore[reportPrivateUsage]
        self._all_units[u.id] = u
        self._units_by_coord.setdefault(c, []).append(u)

    def remove_unit(self, u: Unit) -> None:
        """Remove a unit from the map. The unit's coord is unchanged (caller-visible)."""
        coord = u.coord
        bucket = self._units_by_coord.get(coord)
        if bucket is not None and u in bucket:
            bucket.remove(u)
            if not bucket:
                del self._units_by_coord[coord]
        self._all_units.pop(u.id, None)

    def move_unit(self, u: Unit, to: Coord) -> None:
        """Move a unit from its current coord to `to`. No rule validation in Phase 2."""
        old = u.coord
        if old == to:
            return
        bucket = self._units_by_coord.get(old)
        if bucket is not None and u in bucket:
            bucket.remove(u)
            if not bucket:
                del self._units_by_coord[old]
        u._set_coord(to)  # pyright: ignore[reportPrivateUsage]
        self._units_by_coord.setdefault(to, []).append(u)

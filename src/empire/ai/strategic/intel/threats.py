"""Threat assessment: per-enemy projected reach and the friendly assets at
risk inside it.

Pure over `WorldView`: same view → same threats (see Phase 11 canary tests).
"""

from __future__ import annotations

from empire.ai.strategic.intel.report import Threat
from empire.contracts.world_view import KnownEnemyUnit, WorldView
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.tile import TerrainKind
from empire.core.unit import UNIT_REGISTRY

# How many turns ahead we project each enemy's movement. Three turns flags a
# slow army (speed 1) closing on a city from three cells out — the canonical
# "under threat" case in the design — while staying short enough that fast
# units' reach sets do not swamp the board.
PROJECTION_TURNS = 3


def assess_threats(view: WorldView) -> tuple[Threat, ...]:
    """Build one `Threat` per enemy unit the player knows about."""
    # One city per cell, so a coord->id map is lossless. Units can share a cell
    # (a carrier and its aboard cargo always do — cargo coords track the
    # carrier), so map each coord to *all* the friendly ids standing there;
    # otherwise the dict would silently drop every co-located unit but one.
    own_city_at = {c.coord: c.id for c in view.own_cities}
    own_units_at: dict[Coord, list[UnitId]] = {}
    for unit in view.own_units:
        own_units_at.setdefault(unit.coord, []).append(unit.id)

    threats: list[Threat] = []
    for known in _freshest_per_unit(view.known_enemy_units):
        snap = known.snapshot
        cls = UNIT_REGISTRY[snap.kind]
        reach = _project_reach(view, snap.coord, cls.speed * PROJECTION_TURNS, cls.legal_terrain)
        at_risk_cities = tuple(
            sorted((cid for coord, cid in own_city_at.items() if coord in reach), key=int)
        )
        at_risk_units = tuple(
            sorted(
                (uid for coord, uids in own_units_at.items() if coord in reach for uid in uids),
                key=int,
            )
        )
        threats.append(
            Threat(
                enemy_unit_id=snap.unit_id,
                enemy_owner_id=snap.owner_id,
                kind=snap.kind,
                origin=snap.coord,
                # Strength scaled by remaining hits: a battered battleship
                # projects less force than a fresh one.
                combat_power=cls.strength * snap.hits,
                staleness=view.turn - known.seen_at_turn,
                projected_reach=frozenset(reach),
                at_risk_city_ids=at_risk_cities,
                at_risk_unit_ids=at_risk_units,
            )
        )

    # Stable order: by origin then unit id, so the tuple is reproducible.
    threats.sort(key=lambda t: (t.origin.x, t.origin.y, int(t.enemy_unit_id)))
    return tuple(threats)


def _freshest_per_unit(known: list[KnownEnemyUnit]) -> list[KnownEnemyUnit]:
    """Collapse `known_enemy_units` to one sighting per unit, keeping the freshest.

    `WorldView.known_enemy_units` reports a unit both at its current cell (when
    visible) and at any cell still remembering it from a past sighting — so a
    unit that moved into view from a remembered tile appears twice. Without
    this collapse we would emit a real threat plus a phantom one at the stale
    coordinate. Highest `seen_at_turn` wins; ties keep the first encountered,
    which is deterministic given the view.
    """
    freshest: dict[UnitId, KnownEnemyUnit] = {}
    for sighting in known:
        uid = sighting.snapshot.unit_id
        current = freshest.get(uid)
        if current is None or sighting.seen_at_turn > current.seen_at_turn:
            freshest[uid] = sighting
    return list(freshest.values())


def _project_reach(
    view: WorldView, origin: Coord, max_steps: int, legal: frozenset[TerrainKind]
) -> set[Coord]:
    """Cells reachable from `origin` in at most `max_steps` 8-directional moves
    over terrain legal for the unit kind.

    Unknown terrain (never observed) is treated as traversable: an enemy can
    plausibly move through fog, and over-stating reach is the safe error for a
    defender. A bounded BFS by move count keeps this deterministic and cheap.
    """
    reach = {origin}
    frontier = {origin}
    for _ in range(max_steps):
        nxt: set[Coord] = set()
        for cell in frontier:
            for neighbor in cell.neighbors():
                if neighbor in reach or not view.in_bounds(neighbor):
                    continue
                if _passable(view, neighbor, legal):
                    nxt.add(neighbor)
        reach |= nxt
        frontier = nxt
        if not frontier:
            break
    return reach


def _passable(view: WorldView, c: Coord, legal: frozenset[TerrainKind]) -> bool:
    tile = view.terrain_at(c)
    if tile is None:
        return True  # unobserved — assume the enemy can traverse it
    return tile.terrain in legal

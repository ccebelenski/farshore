"""Theater detection: partition the known world into landmass-centred regions
and tag each with its strategic state.

A theater is one connected body of land/city cells plus the ring of cells
adjacent to it (its bordering water and the immediate unexplored fringe). The
design (`03-ai-design.md` §3.1) calls for predicted-terrain inference on
unexplored cells; we apply it as a *single-ring fringe* rather than unbounded
propagation, so a lone observed land cell cannot infect an entire unexplored
ocean. Connectivity — and therefore the theater count — is decided strictly by
*known* land, which keeps detection stable: two separated islands always yield
two theaters.

Pure over `WorldView`: same view → same theaters.
"""

from __future__ import annotations

from empire.ai.strategic.intel.report import Theater, TheaterState
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.tile import LAND_TERRAINS


def detect_theaters(view: WorldView) -> tuple[Theater, ...]:
    """Flood-fill known land into components and classify each as a theater."""
    land_cells = {
        c for c, tile in view.visible_tiles().items() if tile.terrain in LAND_TERRAINS
    }
    for c, remembered in view.remembered_tiles().items():
        if remembered.terrain in LAND_TERRAINS:
            land_cells.add(c)

    components = _components(land_cells)

    # Index cities by coordinate so each theater can read off who owns what.
    own = {c.coord: c.id for c in view.own_cities}
    enemy = {c.coord: c.id for c in view.known_enemy_cities}
    neutral = {c.coord: c.id for c in view.neutral_cities}

    theaters: list[Theater] = []
    for comp in components:
        # The theater footprint is the land component plus its one-ring fringe
        # (bordering water + immediately adjacent unexplored cells).
        cells = set(comp)
        for cell in comp:
            for neighbor in cell.neighbors():
                if view.in_bounds(neighbor):
                    cells.add(neighbor)

        friendly_ids = tuple(sorted((own[c] for c in comp if c in own), key=int))
        enemy_ids = tuple(sorted((enemy[c] for c in comp if c in enemy), key=int))
        neutral_ids = tuple(sorted((neutral[c] for c in comp if c in neutral), key=int))

        theaters.append(
            Theater(
                cells=frozenset(cells),
                state=_classify(friendly_ids, enemy_ids, neutral_ids),
                friendly_city_ids=friendly_ids,
                enemy_city_ids=enemy_ids,
                neutral_city_ids=neutral_ids,
            )
        )

    # Stable order: by the component's top-left-most cell.
    theaters.sort(key=lambda t: min((c.x, c.y) for c in t.cells))
    return tuple(theaters)


def _classify(
    friendly: tuple[int, ...], enemy: tuple[int, ...], neutral: tuple[int, ...]
) -> TheaterState:
    """Map city ownership on a landmass to its strategic state.

    Both sides present → CONTESTED. One side only → that side's core. No
    presence but neutral cities up for grabs → CONTESTED. No cities at all →
    UNEXPLORED (nothing here yet worth contesting).
    """
    if friendly and enemy:
        return TheaterState.CONTESTED
    if friendly:
        return TheaterState.FRIENDLY_CORE
    if enemy:
        return TheaterState.ENEMY_CORE
    if neutral:
        return TheaterState.CONTESTED
    return TheaterState.UNEXPLORED


def _components(cells: set[Coord]) -> list[list[Coord]]:
    """8-connected components of `cells`, each returned in sorted order."""
    remaining = set(cells)
    components: list[list[Coord]] = []
    while remaining:
        seed = min(remaining, key=lambda c: (c.x, c.y))
        comp: list[Coord] = []
        stack = [seed]
        remaining.discard(seed)
        while stack:
            cell = stack.pop()
            comp.append(cell)
            for neighbor in cell.neighbors():
                if neighbor in remaining:
                    remaining.discard(neighbor)
                    stack.append(neighbor)
        comp.sort(key=lambda c: (c.x, c.y))
        components.append(comp)
    return components

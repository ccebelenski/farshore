"""`PathCostProfile`: per-terrain costs and parameters for pathfinding.

A profile encodes how a particular unit / mission values different terrain
kinds. Different unit types use different profiles (Army costs WATER as
impassable; sea units cost LAND as impassable; Fighter costs everything 1).
"""

from __future__ import annotations

from dataclasses import dataclass

from empire.core.tile import TerrainKind


@dataclass(frozen=True, slots=True)
class PathCostProfile:
    """Per-terrain costs. `None` means impassable for this unit.

    `danger_weight` is multiplied by a caller-supplied threat value at each
    cell and added to that cell's terrain cost. The wiring exists;
    actual threat lookups come from `IntelService` in later phases.
    """

    land_cost: int | None
    water_cost: int | None
    city_cost: int | None
    unknown_cost: int | None = None
    danger_weight: float = 0.0

    def cost_for(self, terrain: TerrainKind) -> int | None:
        if terrain is TerrainKind.LAND:
            return self.land_cost
        if terrain is TerrainKind.WATER:
            return self.water_cost
        return self.city_cost


# --- presets -----------------------------------------------------------------

# Army: land/city OK, water impassable.
ARMY = PathCostProfile(land_cost=1, water_cost=None, city_cost=1, unknown_cost=1)

# Sea units (Patrol, Destroyer, Submarine, Transport, Carrier, Battleship):
# water OK, city OK (port), land impassable.
SEA = PathCostProfile(land_cost=None, water_cost=1, city_cost=1, unknown_cost=1)

# Air units (Fighter): can fly over anything.
AIR = PathCostProfile(land_cost=1, water_cost=1, city_cost=1, unknown_cost=1)

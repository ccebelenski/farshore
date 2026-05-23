"""Abstract `MapGenerator` interface and the `MapGenerationFailed` exception.

Concrete implementations live alongside (e.g.,
`empire.mapgen.height_field.HeightFieldMapGenerator`). The ABC is here so
callers can depend on the contract without coupling to a specific algorithm.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod

from empire.core.city import City
from empire.core.map import Map
from empire.core.ruleset import MapProfile


class MapGenerator(ABC):
    """Produces a populated `Map` plus its list of neutral starting cities."""

    @abstractmethod
    def generate(self, profile: MapProfile, rng: random.Random) -> tuple[Map, list[City]]:
        """Generate a fresh map and its cities for `profile`.

        Cities returned have `owner=None`; assigning capitals is the
        responsibility of game setup (see `01-game-rules-spec.md` §9.2).
        """


class MapGenerationError(RuntimeError):
    """The generator could not satisfy the profile after its retry budget.

    Typically raised when the profile demands more cities than fit at the
    given `min_city_distance` and `water_ratio`. Diagnose by relaxing one
    of those three knobs.
    """

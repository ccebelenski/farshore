"""Rule presets and map profiles.

A `RuleSet` is a coherent bundle of rule values, shipped as a named preset
(see `planning/02-design-decisions.md` D-003). A `MapProfile` is the size
and density parameters used by map generation.

`MapProfile` lives here rather than in `empire.mapgen` because `RuleSet`
holds one, and `core` cannot depend on `mapgen` per the dependency matrix.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MapProfile:
    """Map generation parameters."""

    width: int
    height: int
    water_ratio: int            # target water fraction, percent 0..100
    smooth_iterations: int      # height-field smoothing passes
    num_cities: int
    min_city_distance: int      # Chebyshev spacing between city sites


# Shipped profile presets.
SMALL = MapProfile(
    width=50, height=30,
    water_ratio=70, smooth_iterations=5,
    num_cities=25, min_city_distance=6,
)
STANDARD_PROFILE = MapProfile(
    width=100, height=60,
    water_ratio=70, smooth_iterations=5,
    num_cities=70, min_city_distance=8,
)
LARGE = MapProfile(
    width=150, height=90,
    water_ratio=70, smooth_iterations=5,
    num_cities=140, min_city_distance=10,
)


@dataclass(frozen=True)
class RuleSet:
    """A coherent bundle of rule values."""

    name: str
    map_profile: MapProfile

    # Per-unit rule values (initial v1 defaults; tuned by playtesting).
    fighter_base_range: int = 20            # cells a fighter can fly before refueling
    satellite_range: int = 50               # turns of orbital lifetime
    production_change_penalty_divisor: int = 5

    # Optional rule toggles. The STANDARD preset leaves all of these at False/0.
    allow_unit_stacking: bool = False
    army_capture_city_deterministic: bool = False
    asymmetric_combat_bonus: float = 0.0
    seven_terrain_types: bool = False
    transport_escort_required_for_unload: bool = False

    # Difficulty / cheat knobs.
    fog_cheat: bool = False                 # AI sees real map; OFF by default at all difficulties


# Shipped ruleset presets.
STANDARD = RuleSet(name="STANDARD", map_profile=STANDARD_PROFILE)

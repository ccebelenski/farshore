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


# Shipped profile presets. Values chosen so HeightFieldMapGenerator can
# pack the requested city count reliably; tuned by playtesting in later
# phases. The packing constraint is roughly num_cities * (2*d+1)^2 < land
# cells available; greedy random placement runs at ~60% efficiency vs
# perfect packing, so we stay well clear of the ceiling.
SMALL = MapProfile(
    width=50, height=30,
    water_ratio=50, smooth_iterations=5,
    num_cities=10, min_city_distance=3,
)
STANDARD_PROFILE = MapProfile(
    width=100, height=60,
    water_ratio=55, smooth_iterations=5,
    num_cities=25, min_city_distance=4,
)
LARGE = MapProfile(
    width=150, height=90,
    water_ratio=60, smooth_iterations=5,
    num_cities=50, min_city_distance=5,
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

    # City artillery (FortifiedCities preset; spec §4.7). When
    # `city_artillery_range > 0`, every city — owned or neutral — defends
    # itself with a single-target ranged strike: one shot per round, each
    # shot a `city_artillery_hit_prob` chance to deal 1 HP (an instant kill
    # vs a 1-HP army/fighter). 0 disables it (Classic / STANDARD). The
    # one-shot/round cadence is the anti-horde invariant: a lone attacker
    # crossing the gauntlet dies, but a concentrated assault punches through.
    # A fired-upon unit is *pinned* (loses its move this round; naval halved)
    # with `city_artillery_pin_prob` chance, hit or miss — a *chance* not an
    # absolute, so an unsupported attacker usually stalls but may slip through,
    # and a coordinated assault reliably lands someone. 1.0 = absolute pin
    # (makes cities uncapturable); 0.0 = no pinning.
    city_artillery_range: int = 0
    city_artillery_hit_prob: float = 0.5
    city_artillery_pin_prob: float = 0.5

    # Difficulty / cheat knobs.
    fog_cheat: bool = False                 # AI sees real map; OFF by default at all difficulties


# Shipped ruleset presets.
STANDARD = RuleSet(name="STANDARD", map_profile=STANDARD_PROFILE)

# FortifiedCities: the modern preset where cities fight back (spec §4.7).
# Cities have ranged artillery, so capture is gated by a gauntlet of fire
# rather than a coin flip — an army that survives the approach takes the city
# deterministically (the gauntlet was the cost). Classic/STANDARD keep the
# inert 50% capture roll. See `project_city_artillery` for the rationale.
FORTIFIED_CITIES = RuleSet(
    name="FORTIFIED_CITIES",
    map_profile=STANDARD_PROFILE,
    army_capture_city_deterministic=True,
    city_artillery_range=2,
    city_artillery_hit_prob=0.5,
    city_artillery_pin_prob=0.5,
)

"""Phase-1 canary tests for `RuleSet`, `MapProfile`, and the STANDARD preset."""

import dataclasses

import pytest

from empire.core.ruleset import (
    LARGE,
    SMALL,
    STANDARD,
    STANDARD_PROFILE,
    MapProfile,
    RuleSet,
)


def test_standard_preset_loads() -> None:
    assert isinstance(STANDARD, RuleSet)
    assert STANDARD.name == "STANDARD"
    assert STANDARD.map_profile is STANDARD_PROFILE


def test_standard_profile_dimensions() -> None:
    assert STANDARD_PROFILE.width == 100
    assert STANDARD_PROFILE.height == 60


def test_standard_leaves_all_optional_toggles_off() -> None:
    assert STANDARD.allow_unit_stacking is False
    assert STANDARD.army_capture_city_deterministic is False
    assert STANDARD.asymmetric_combat_bonus == 0.0
    assert STANDARD.seven_terrain_types is False
    assert STANDARD.transport_escort_required_for_unload is False
    assert STANDARD.fog_cheat is False


def test_standard_default_rule_values() -> None:
    assert STANDARD.production_change_penalty_divisor == 5
    assert STANDARD.fighter_base_range == 20
    assert STANDARD.satellite_range == 50


def test_profile_presets_are_distinct_and_ordered_by_size() -> None:
    assert SMALL.width < STANDARD_PROFILE.width < LARGE.width
    assert SMALL.height < STANDARD_PROFILE.height < LARGE.height
    assert SMALL.num_cities < STANDARD_PROFILE.num_cities < LARGE.num_cities


def test_ruleset_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        STANDARD.name = "MODERN"  # type: ignore[misc]


def test_map_profile_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        STANDARD_PROFILE.width = 999  # type: ignore[misc]


def test_can_construct_custom_ruleset_overriding_defaults() -> None:
    custom = RuleSet(
        name="STACKED",
        map_profile=STANDARD_PROFILE,
        allow_unit_stacking=True,
    )
    assert custom.allow_unit_stacking is True
    assert custom.army_capture_city_deterministic is False  # other defaults preserved


def test_map_profile_is_constructible() -> None:
    p = MapProfile(
        width=10, height=10,
        water_ratio=50, smooth_iterations=3,
        num_cities=4, min_city_distance=3,
    )
    assert p.width == 10

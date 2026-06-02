"""Campaign odds estimator (Phase 15.7) — pure-function unit tests.

These pin down the *shape* of each factor (monotonicity, bounds, the
no-artillery passthrough, the binary surprise) rather than exact tuned values,
since the knobs are explicitly arena-tuned and will move.
"""

from __future__ import annotations

from empire.ai.strategic.campaign import (
    COMMIT_THRESHOLD,
    arrival_discount,
    estimate_success,
    gauntlet_breakthrough,
    surprise_bonus,
    trend_factor,
)
from empire.core.ruleset import FORTIFIED_CITIES, STANDARD, RuleSet


def _fort(hit: float = 0.5, pin: float = 0.5, rng: int = 2) -> RuleSet:
    return RuleSet(
        name="T",
        map_profile=STANDARD.map_profile,
        city_artillery_range=rng,
        city_artillery_hit_prob=hit,
        city_artillery_pin_prob=pin,
    )


# --- gauntlet ----------------------------------------------------------------


def test_gauntlet_is_passthrough_without_artillery() -> None:
    assert gauntlet_breakthrough(1, STANDARD) == 1.0
    assert gauntlet_breakthrough(5, STANDARD) == 1.0


def test_gauntlet_rises_with_concentration() -> None:
    """More armies arriving together → higher breakthrough (the anti-trickle
    invariant)."""
    one = gauntlet_breakthrough(1, FORTIFIED_CITIES)
    three = gauntlet_breakthrough(3, FORTIFIED_CITIES)
    five = gauntlet_breakthrough(5, FORTIFIED_CITIES)
    assert one < three < five


def test_gauntlet_lone_army_is_grim() -> None:
    """A single army against a fortified city is a poor bet but not zero."""
    assert 0.0 < gauntlet_breakthrough(1, FORTIFIED_CITIES) <= 0.30


def test_gauntlet_bounded() -> None:
    for n in range(1, 12):
        assert 0.0 <= gauntlet_breakthrough(n, FORTIFIED_CITIES) <= 1.0


def test_gauntlet_harsher_when_guns_hit_harder() -> None:
    weak = gauntlet_breakthrough(3, _fort(hit=0.2, pin=0.2))
    strong = gauntlet_breakthrough(3, _fort(hit=0.9, pin=0.9))
    assert strong < weak


# --- arrival -----------------------------------------------------------------


def test_arrival_discount_monotonic_and_bounded() -> None:
    assert arrival_discount(0) == 1.0
    assert arrival_discount(2) > arrival_discount(8)
    assert 0.0 < arrival_discount(1000) <= 1.0  # floored, never zero/negative


# --- trend -------------------------------------------------------------------


def test_trend_neutral_when_not_outnumbered() -> None:
    assert trend_factor(5, 5) == 1.0
    assert trend_factor(8, 3) == 1.0  # ahead → no penalty


def test_trend_penalises_being_outproduced() -> None:
    assert trend_factor(2, 6) < trend_factor(4, 6) < 1.0


def test_trend_floored() -> None:
    assert trend_factor(1, 99) >= 0.5


# --- surprise (binary) -------------------------------------------------------


def test_surprise_is_binary() -> None:
    assert surprise_bonus(any_unit_spotted=True) == 1.0
    assert surprise_bonus(any_unit_spotted=False) > 1.0


# --- composite ---------------------------------------------------------------


def _est(**kw: object) -> float:
    base: dict[str, object] = {
        "field_odds": 1.0,
        "assault_size": 3,
        "formation_turns": 0,
        "my_city_count": 5,
        "enemy_city_count": 5,
        "any_unit_spotted": True,
        "rules": FORTIFIED_CITIES,
    }
    base.update(kw)
    return estimate_success(**base)  # type: ignore[arg-type]


def test_estimate_bounded() -> None:
    assert 0.0 <= _est() <= 1.0
    assert 0.0 <= _est(field_odds=0.0) <= 1.0
    # absurd inputs stay in range
    assert 0.0 <= _est(formation_turns=999, enemy_city_count=999) <= 1.0


def test_estimate_zero_field_odds_is_zero() -> None:
    assert _est(field_odds=0.0) == 0.0


def test_estimate_unspotted_beats_spotted() -> None:
    assert _est(any_unit_spotted=False) > _est(any_unit_spotted=True)


def test_estimate_concentration_beats_trickle() -> None:
    assert _est(assault_size=5) > _est(assault_size=1)


def test_estimate_fast_beats_slow() -> None:
    assert _est(formation_turns=0) > _est(formation_turns=10)


def test_estimate_outproduced_is_worse() -> None:
    assert _est(enemy_city_count=10) < _est(enemy_city_count=5)


def test_undefended_target_without_artillery_is_a_lock() -> None:
    """The early-game fast path: no artillery, full field odds, no lag → ~1.0,
    well above the commit threshold so a lone army just takes it."""
    p = estimate_success(
        field_odds=1.0,
        assault_size=1,
        formation_turns=0,
        my_city_count=3,
        enemy_city_count=3,
        any_unit_spotted=True,
        rules=STANDARD,
    )
    assert p >= COMMIT_THRESHOLD
    assert p == 1.0


def test_uncontested_grab_ignores_time_pressure() -> None:
    """A soft target (no artillery, full field odds) is NOT a race, so neither
    a long march nor being out-produced should drag it below the threshold.
    This is the Phase 15.7 step-1 fix: flat discounting over-analysed early
    grabs and collapsed the AI to a single front."""
    far_and_outproduced = estimate_success(
        field_odds=1.0,
        assault_size=1,
        formation_turns=20,
        my_city_count=1,
        enemy_city_count=12,
        any_unit_spotted=True,
        rules=STANDARD,
    )
    assert far_and_outproduced >= COMMIT_THRESHOLD
    assert far_and_outproduced == 1.0


def test_defended_target_still_feels_time_pressure() -> None:
    """A *contested* target (real mobile defenders) keeps the discounts —
    marching longer / being out-produced still lowers its odds."""
    near = estimate_success(
        field_odds=0.7, assault_size=1, formation_turns=0,
        my_city_count=5, enemy_city_count=5, any_unit_spotted=True, rules=STANDARD,
    )
    far = estimate_success(
        field_odds=0.7, assault_size=1, formation_turns=12,
        my_city_count=5, enemy_city_count=5, any_unit_spotted=True, rules=STANDARD,
    )
    assert far < near


def test_artillery_target_is_contested_even_at_full_field_odds() -> None:
    """No mobile defenders but the city has guns → still a race (must run the
    gauntlet), so time pressure applies and the lone-army P is well under 1."""
    p = estimate_success(
        field_odds=1.0, assault_size=1, formation_turns=8,
        my_city_count=5, enemy_city_count=5, any_unit_spotted=True,
        rules=FORTIFIED_CITIES,
    )
    assert p < COMMIT_THRESHOLD

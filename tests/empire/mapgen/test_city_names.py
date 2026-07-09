"""Frontier city-name generation: deterministic, unique, and always exactly
`count` names for any map size."""

from __future__ import annotations

import random

from empire.mapgen.city_names import generate_city_names


def test_deterministic_per_seed() -> None:
    assert generate_city_names(40, random.Random(7)) == generate_city_names(
        40, random.Random(7)
    )


def test_different_seed_gives_different_names() -> None:
    assert generate_city_names(40, random.Random(7)) != generate_city_names(
        40, random.Random(8)
    )


def test_exact_count_and_all_unique() -> None:
    names = generate_city_names(40, random.Random(3))
    assert len(names) == 40
    assert len(set(names)) == 40


def test_stays_unique_past_the_curated_pool() -> None:
    # More than standalone + every head×tail compound → exercises the fallback.
    names = generate_city_names(700, random.Random(1))
    assert len(names) == 700
    assert len(set(names)) == 700


def test_zero_count_is_empty() -> None:
    assert generate_city_names(0, random.Random(1)) == []

"""Schema migration framework.

A `Migration` upgrades a save payload from one schema version to the next.
The `MIGRATIONS` registry maps `from_version -> Migration` so the loader can
walk old saves up to current. No migrations exist while v1 is the only
schema version.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Migration:
    """One step in the schema-upgrade chain."""

    from_version: int
    to_version: int
    apply: Callable[[dict[str, Any]], dict[str, Any]]


MIGRATIONS: dict[int, Migration] = {}
"""Keyed by `from_version`. Lookup yields the migration that takes the payload
to `from_version + 1` (typically). Empty while v1 is current."""


def register(migration: Migration) -> None:
    """Register a migration in the chain. Errors if `from_version` already mapped."""
    if migration.from_version in MIGRATIONS:
        raise ValueError(
            f"Migration from version {migration.from_version} already registered"
        )
    MIGRATIONS[migration.from_version] = migration

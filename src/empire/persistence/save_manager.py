"""`SaveManager`: JSON file I/O plus the migration-chain dispatch on load.

Save format is JSON, pretty-printed for human readability. Schema version
is the top-level discriminator; older saves walk up the migration chain to
the current schema before deserialization. Newer saves are rejected (we
cannot down-migrate).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from empire.core.game import Game
from empire.persistence.migration import MIGRATIONS
from empire.persistence.schema import Serializer


class SaveManager:
    """File-level save/load with migration dispatch."""

    def save(self, game: Game, path: Path) -> None:
        payload = Serializer().to_dict(game)
        path.write_text(json.dumps(payload, indent=2, sort_keys=False))

    def load(self, path: Path) -> Game:
        raw = path.read_text()
        parsed: Any = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"Save file root must be an object, got {type(parsed).__name__}")
        return self._from_payload(cast(dict[str, Any], parsed))

    def _from_payload(self, payload: Mapping[str, Any]) -> Game:
        version = payload.get("schema_version")
        if version is None:
            raise ValueError("Save file missing 'schema_version' field")
        if not isinstance(version, int):
            raise ValueError(f"Save 'schema_version' must be int, got {type(version).__name__}")

        current = Serializer.SCHEMA_VERSION
        if version > current:
            raise ValueError(
                f"Save is from a newer schema version ({version}); "
                f"this binary supports up to {current}"
            )

        # Walk the migration chain upward until we reach the current schema.
        upgraded: dict[str, Any] = dict(payload)
        while version < current:
            migration = MIGRATIONS.get(version)
            if migration is None:
                raise ValueError(
                    f"No migration registered from schema version {version}; cannot load"
                )
            upgraded = migration.apply(upgraded)
            version = migration.to_version
            upgraded["schema_version"] = version

        return Serializer().from_dict(upgraded)

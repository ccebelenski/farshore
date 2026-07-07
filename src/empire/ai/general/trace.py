"""`EpochTraceWriter`: the general's war diary on disk.

One JSONL line per epoch attempt — briefing text, raw model answer,
accepted amendments, refusals, or the failure that aborted the epoch.
Two customers: post-game analysis (why did those armies cluster?) and
the fine-tuning corpus (every prompted game builds training data —
planning/08). Failures are as valuable as successes; both are written.

Writing is best-effort: a full disk or unwritable path must never take
the game down, so I/O errors are swallowed after disabling the writer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EpochTraceWriter:
    """Appends epoch records to a JSONL file, one line per record."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._enabled = True

    @property
    def path(self) -> Path:
        return self._path

    def write(self, record: dict[str, Any]) -> None:
        if not self._enabled:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Tracing must never endanger the game (the competence floor
            # extends to its diagnostics). One failure disables the writer.
            self._enabled = False

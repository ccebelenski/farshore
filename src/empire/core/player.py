"""Player: a participant in the game (human or AI).

Structural only — ownership queries live on `Game`/`Map`, which hold
the collections.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from empire.core.identity import PlayerId

if TYPE_CHECKING:
    from empire.core.map import ViewMap


# A simple color identifier for the UI. The TUI layer interprets this; the
# core doesn't care about the specific value.
Color = str


@dataclass(slots=True)
class Player:
    """A game participant.

    `view` is this player's `ViewMap` — what they currently see and remember.
    AI controllers live in `Game.controllers`, keyed by `PlayerId`, *not* on
    the player itself (this keeps `core` free of AI imports).
    """

    id: PlayerId
    name: str
    is_ai: bool
    view: ViewMap
    color: Color = "default"

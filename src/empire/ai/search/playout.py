"""`PlayoutModel`: clone a `Game` for forward simulation.

A clone is a schema-v1 serializer round-trip — the exact code path saves
use — so it is precisely as faithful as save/load. Two things a save payload
deliberately does not carry must be re-wired on the clone:

- the **combat resolver** (`from_dict` leaves the null resolver in place;
  a playout fights, so wiring one is mandatory here), and
- the **controllers** (policy objects are not game state).

RNG state IS carried, so a clone driven by the same controller types replays
the original game bit-for-bit; see `tests/empire/ai/search/test_playout.py`.
"""

from __future__ import annotations

from collections.abc import Mapping

from empire.combat.resolver import CombatResolver
from empire.contracts.controller import AIController
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.persistence.schema_v1 import V1Serializer


class PlayoutModel:
    """Forward-model factory: independent game copies for candidate playouts."""

    def __init__(self) -> None:
        self._serializer = V1Serializer()

    def clone(
        self,
        game: Game,
        controllers: Mapping[PlayerId, AIController],
    ) -> Game:
        """A deep, independent copy of `game` with `controllers` attached.

        Every player who should act in the playout needs an entry in
        `controllers`; players without one hold position (the engine skips
        controller-less players). Mutating the clone never touches `game`.
        """
        copy = self._serializer.from_dict(self._serializer.to_dict(game))
        copy.combat_resolver = CombatResolver()
        for player_id, controller in controllers.items():
            copy.attach_controller(player_id, controller)
        return copy

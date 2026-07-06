"""`FakeGeneral`: a deterministic scripted general for harness work.

Plays the general's role in the epoch loop with zero LLM involvement: it is
constructed with a sequence of `Doctrine` objects and hands out the next one
per epoch, ignoring the briefing's content. Once the script is exhausted it
issues empty doctrines (all-CONTINUE — the anti-thrash default), so a
harness can keep running turns past the scripted story without special
cases. The real general will answer `decide` by prompting a model; every
consumer of this seam must work identically against either.
"""

from __future__ import annotations

from collections.abc import Sequence

from empire.contracts.doctrine import Briefing, Doctrine


class FakeGeneral:
    """Replays a fixed doctrine script, one per epoch, in order."""

    def __init__(self, doctrines: Sequence[Doctrine]) -> None:
        self._doctrines: tuple[Doctrine, ...] = tuple(doctrines)
        self._epoch: int = 0

    def name(self) -> str:
        return "FakeGeneral"

    def decide(self, briefing: Briefing) -> Doctrine:
        """The next scripted doctrine; an empty one (stamped with the
        briefing's turn) once the script runs out."""
        if self._epoch < len(self._doctrines):
            doctrine = self._doctrines[self._epoch]
            self._epoch += 1
            return doctrine
        return Doctrine(turn=briefing.turn, amendments=())

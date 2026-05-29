"""Persistent unit orders that span turns.

A `StandingOrder` is attached to a `Unit` via `Unit.standing_order`. Each
turn, before the player's controller is consulted, the engine applies one
step of every owned unit's standing order. The order persists until either
exhausted or interrupted (see `planning/05-implementation-plan.md` Phase
10.6 for the interruption rules).

Three variants — all frozen value types:

- `Heading(direction)`: walk one cell per turn in a fixed cardinal/diagonal
  direction. Never exhausts on its own; only interruption clears it.
- `PatrolPath(remaining, original, reverse_on_end)`: walk a pre-built BFS
  path one cell per turn. When `remaining` empties: if `reverse_on_end`,
  re-arm with the path flipped; else, clear the order.
- `Sentry()`: do nothing. The unit skips auto-cycle until manually woken
  or auto-woken by a nearby enemy/city becoming visible. Per spec, a
  surprise NEVER causes a unit to enter sentry — see
  `[[feedback-no-auto-sentry-on-surprise]]`.

The variants are a union via `StandingOrder = Heading | PatrolPath |
Sentry`. We use isinstance dispatch in the engine rather than a tagged
enum so each variant carries its own payload type cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass

from empire.core.coord import Coord, Direction


@dataclass(frozen=True, slots=True)
class Heading:
    """Walk one cell per turn in `direction` until interrupted."""

    direction: Direction


@dataclass(frozen=True, slots=True)
class PatrolPath:
    """Walk a pre-built BFS path one cell per turn.

    `remaining` is the still-to-enter tail of the current pass through
    the path. `original` is the path as first set, kept so `reverse_on_end`
    can rebuild it. The engine replaces this dataclass each turn with a
    new instance reflecting one step of progress.
    """

    remaining: tuple[Coord, ...]
    original: tuple[Coord, ...]
    reverse_on_end: bool = False

    @classmethod
    def new(
        cls,
        cells: tuple[Coord, ...],
        *,
        reverse_on_end: bool = False,
    ) -> PatrolPath:
        """Construct a fresh patrol from a single path."""
        return cls(remaining=cells, original=cells, reverse_on_end=reverse_on_end)

    def next_cell(self) -> Coord | None:
        """The cell the unit would walk into next, or None if exhausted."""
        return self.remaining[0] if self.remaining else None

    def after_step(self) -> PatrolPath | None:
        """The patrol state after consuming one cell.

        Returns None when the order should clear (one-shot path exhausted).
        For `reverse_on_end`, returns a new patrol with the original
        reversed once the current pass empties.
        """
        tail = self.remaining[1:]
        if tail:
            return PatrolPath(
                remaining=tail,
                original=self.original,
                reverse_on_end=self.reverse_on_end,
            )
        if not self.reverse_on_end:
            return None
        flipped = tuple(reversed(self.original))
        return PatrolPath(
            remaining=flipped,
            original=flipped,
            reverse_on_end=True,
        )


@dataclass(frozen=True, slots=True)
class Sentry:
    """The unit holds position and is skipped by auto-cycle."""


StandingOrder = Heading | PatrolPath | Sentry

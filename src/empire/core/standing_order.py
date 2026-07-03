"""Persistent unit orders that span turns.

A `StandingOrder` is attached to a `Unit` via `Unit.standing_order`. Each
turn, before the player's controller is consulted, the engine applies one
step of every owned unit's standing order. The order persists until either
exhausted or interrupted.

Three variants — all frozen value types:

- `Heading(direction)`: walk one cell per turn in a fixed cardinal/diagonal
  direction. Never exhausts on its own; only interruption clears it.
- `PatrolPath(remaining, original, loop)`: walk a pre-built BFS path one
  cell per turn. When `remaining` empties: if `loop`, re-arm with the same
  cycle (a patrol route builds `original` as the full round trip, ending
  adjacent to its first cell); else, clear the order.
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
    the path. `original` is the path as first set, kept so `loop` can
    re-arm it. The engine replaces this dataclass each turn with a new
    instance reflecting one step of progress.

    A looping patrol's `original` must be a full ROUND TRIP whose last cell
    is adjacent to its first (the TUI builds A->B->A as one cycle) — the
    re-armed pass then starts with a legal step, never a step into the
    unit's own cell.
    """

    remaining: tuple[Coord, ...]
    original: tuple[Coord, ...]
    loop: bool = False

    @classmethod
    def new(
        cls,
        cells: tuple[Coord, ...],
        *,
        loop: bool = False,
    ) -> PatrolPath:
        """Construct a fresh patrol from a single path."""
        return cls(remaining=cells, original=cells, loop=loop)

    def next_cell(self) -> Coord | None:
        """The cell the unit would walk into next, or None if exhausted."""
        return self.remaining[0] if self.remaining else None

    def after_step(self) -> PatrolPath | None:
        """The patrol state after consuming one cell.

        Returns None when the order should clear (one-shot path exhausted).
        For `loop`, re-arms the same cycle once the current pass empties.
        """
        tail = self.remaining[1:]
        if tail:
            return PatrolPath(
                remaining=tail, original=self.original, loop=self.loop
            )
        if not self.loop:
            return None
        return PatrolPath(
            remaining=self.original, original=self.original, loop=True
        )


@dataclass(frozen=True, slots=True)
class Explore:
    """Autonomously reveal unexplored tiles until woken.

    Carries no payload: the behavior is recomputed each turn (the frontier
    recedes as tiles get revealed), fog-honestly over KNOWN terrain. Each
    turn the unit heads for the nearest reachable frontier cell — a known,
    passable cell adjacent to unexplored — with shore frontier taking
    priority. Wakes on enemy-in-scan, city discovery, an artillery ring,
    manually ('w'), or when no reachable frontier remains. Explorers
    coordinate minimally: each claims its target for the turn so two never
    chase the same cell."""


@dataclass(frozen=True, slots=True)
class ReturnToBase:
    """A fighter flies itself home: each turn it re-picks the nearest own
    city with airbase capacity OR own carrier with deck room (carriers move,
    cities fall — the destination is revalidated every turn) and heads there,
    landing/boarding on arrival. Deliberately does NOT wake on enemy contact
    or artillery rings — it is an emergency flight home, not a patrol. Wakes
    only if no landing spot remains reachable on current fuel (the player
    then chooses where to fly/crash)."""


@dataclass(frozen=True, slots=True)
class Sentry:
    """The unit holds position and is skipped by auto-cycle."""


@dataclass(frozen=True, slots=True)
class Loading:
    """A carrier (transport / carrier) holds position waiting to be loaded.

    Like `Sentry` it doesn't move and is skipped by auto-cycle, but the
    engine auto-wakes it the moment its hold is full (in addition to the
    usual enemy-in-scan surprise wake). Cargo boards by moving onto the
    carrier's cell as normal — and the carrier sweeps ADJACENT eligible
    cargo aboard every turn while it waits (a loading dock; see the engine's
    `load_adjacent_cargo`).
    """


StandingOrder = Heading | PatrolPath | Sentry | Loading | Explore | ReturnToBase

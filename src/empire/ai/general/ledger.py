"""`TaskForceLedger`: per-task-force event bookkeeping behind CURRENT TASKINGS.

The briefing's "since:" lines are a pure EVENT ledger (planning/08 "TASKING
CONTINUITY"): the ENGINE IS THE BOOKKEEPER, NOT THE ANALYST. Every line
states what happened — "t86: lost #5, #7 at (11,1)", "t86: transport #16
produced at (1,2)" — and NEVER a judgment: no "stalled", no "threatened",
no "no progress". Judging is what the general is FOR. Every line carries the
turn it happened on, and lines render in turn order. Causes are stated only when
an event actually carries them (`CityFiredEvent` knows it destroyed the
target; a bare `UnitRemovedEvent` does not say why the unit died, so the
line says "lost", not "lost in combat").

LAYERING SEAM: `empire.ai` may not import `empire.events` (import-linter),
so this module types against core only — the event dataclasses in
`empire.core.events` and the `EventBusProtocol` in `empire.core.event_bus`.
The CALLER that owns the concrete bus (the same layer that hands
controllers their views: the TUI app / game driver) constructs the ledger
and wires it with `attach`, exactly as the TUI's `LogPanel.attach_to` does.

FOG-HONESTY: the bus is game-global, so the ledger books only facts the
ledger's player is entitled to — events attributable to its own units (task
force membership, or ownership learned through the `own_unit_kind` oracle
while the unit was alive) and its own city captures. Everything else falls
on the floor unrecorded.

KNOWN FACT-GAP (reported, not patched): no bus event carries a failed city
assault — `StepOutcome.CAPTURE_FAILED` dies inside the engine and surfaces
only as an anonymous `UnitRemovedEvent` at the assault cell — so "capture
attempted twice, failed twice" lines cannot be written until the engine
emits that fact.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from empire.ai.general.registry import TaskForceRegistry
from empire.contracts.doctrine import Refusal, TaskForce, TaskForceId
from empire.core.coord import Coord
from empire.core.event_bus import EventBusProtocol
from empire.core.events import (
    CityCapturedEvent,
    CityFiredEvent,
    UnitDisbandedEvent,
    UnitMovedEvent,
    UnitPlacedEvent,
    UnitRemovedEvent,
)
from empire.core.identity import CityId, PlayerId, UnitId

RegistryProvider = Callable[[], TaskForceRegistry]
"""The current registry on demand. The registry is REPLACED, never mutated,
by `apply`/`prune`, so the ledger reads it through a provider — attribution
happens at RECORD time, while the lost unit is still on its force's roster
(by collect time `prune` has already dropped it)."""

OwnUnitKind = Callable[[UnitId], str | None]
"""Ownership-and-kind oracle over LIVE units: the unit's kind label (e.g.
"transport") if it is currently on the map AND owned by the ledger's
player, else `None`. The caller closes over its map and player."""

CityCoord = Callable[[CityId], Coord | None]
"""City-id to coordinate. City positions are static, so this leaks no
fog-gated truth."""

NowTurn = Callable[[], int]
"""The current game turn on demand. Every event is turn-stamped at RECORD
time (not collect time) so the ledger renders in temporal order — the caller
closes over the game it owns (layering: the ai layer never touches the
engine's turn counter directly, exactly like the other oracles here)."""

_Section = TaskForceId | None
"""Where a line is booked: a task-force id, or `None` for the general
(UNASSIGNED) section."""

# Matches the TF a rendered order addressed ("TF 3: ..." / "FORM TF 3: ...")
# so its refusal can be replayed under that force's ledger entry.
_TF_IN_ORDER_TEXT = re.compile(r"^(?:FORM )?TF ([^:\s]+):")


def _at(coord: Coord) -> str:
    return f"({coord.x},{coord.y})"


def _uid(unit_id: UnitId) -> str:
    return f"#{int(unit_id)}"


@dataclass(frozen=True, slots=True)
class LedgerReport:
    """One epoch's factual lines: `by_task_force` is exactly the mapping
    `BriefingRenderer.render` takes as `events`; `general` is the
    UNASSIGNED/general section (deliveries, unattributed facts, refusals
    of orders that named no standing force)."""

    by_task_force: Mapping[TaskForceId, tuple[str, ...]]
    general: tuple[str, ...]


class TaskForceLedger:
    """Accumulates factual event lines per task force between epochs.

    Lifecycle: construct once per player, `attach` to the bus (or feed the
    `record_*` handlers directly), replay each epoch's `Refusal`s with
    `note_refusals`, `collect` when rendering the briefing, then `reset`
    to open the next epoch's ledger. Ownership knowledge learned along the
    way (which unit ids are ours) survives `reset`; the booked lines do not.

    Recorded facts — and only facts (see module docstring): unit losses
    (with cause only when the event states one), city-artillery hits,
    disbands, arrivals at a force's objective target, own city captures,
    production deliveries, and refused orders. Misses and ordinary moves
    change nothing the board snapshot doesn't already show, so they are
    not booked.
    """

    def __init__(
        self,
        player_id: PlayerId,
        registry: RegistryProvider,
        own_unit_kind: OwnUnitKind,
        city_coord: CityCoord,
        now_turn: NowTurn,
    ) -> None:
        self._player_id = player_id
        self._registry = registry
        self._own_unit_kind = own_unit_kind
        self._city_coord = city_coord
        self._now_turn = now_turn

        # Persistent across epochs: unit ids known to be ours (kind label
        # remembered while the unit was alive), so a later loss of an
        # UNASSIGNED unit is still attributable after it left the map.
        self._known_own: dict[UnitId, str] = {}

        # Per-epoch books (cleared by `reset`). Each row leads with the turn
        # it was booked on, for temporal ordering at render time.
        self._losses: list[tuple[int, _Section, UnitId, Coord, str | None]] = []
        self._hits: list[tuple[int, _Section, UnitId, Coord]] = []
        self._disbands: list[tuple[int, _Section, UnitId, Coord]] = []
        self._arrivals: list[tuple[int, _Section, UnitId, Coord]] = []
        self._captures: list[tuple[int, _Section, Coord]] = []
        self._deliveries: list[tuple[int, str, UnitId, Coord]] = []
        self._refusals: list[tuple[int, _Section, Refusal]] = []
        # Units whose destruction was already booked with its cause from
        # `CityFiredEvent`; the engine's follow-up `UnitRemovedEvent` for
        # the same unit is bookkeeping-duplicate, not a second fact.
        self._cause_booked: set[UnitId] = set()

    # ---- wiring ----------------------------------------------------------------

    def attach(self, bus: EventBusProtocol) -> None:
        """Subscribe every handler on `bus`. Caller-owned bus: the ai layer
        never constructs one (layering — see module docstring)."""
        bus.subscribe(UnitPlacedEvent, self.record_unit_placed)
        bus.subscribe(UnitMovedEvent, self.record_unit_moved)
        bus.subscribe(UnitRemovedEvent, self.record_unit_removed)
        bus.subscribe(UnitDisbandedEvent, self.record_unit_disbanded)
        bus.subscribe(CityCapturedEvent, self.record_city_captured)
        bus.subscribe(CityFiredEvent, self.record_city_fired)

    # ---- event handlers ----------------------------------------------------------

    def record_unit_placed(self, event: UnitPlacedEvent) -> None:
        """Production delivery. Booked in the general section — new units
        are born UNASSIGNED. Enemy production is not our fact to book."""
        kind = self._own_unit_kind(event.unit_id)
        if kind is None:
            return
        self._known_own[event.unit_id] = kind
        self._deliveries.append((self._now_turn(), kind, event.unit_id, event.at))

    def record_unit_moved(self, event: UnitMovedEvent) -> None:
        """Moves are visible in the board snapshot and are not booked — with
        one exception: a member reaching its force's objective target is an
        arrival, a fact the snapshot alone doesn't call out."""
        kind = self._own_unit_kind(event.unit_id)
        if kind is not None:
            self._known_own.setdefault(event.unit_id, kind)
        force = self._force_of(event.unit_id)
        if (
            force is not None
            and isinstance(force.objective.target, Coord)
            and event.to == force.objective.target
        ):
            self._arrivals.append((self._now_turn(), force.tf_id, event.unit_id, event.to))

    def record_unit_removed(self, event: UnitRemovedEvent) -> None:
        """A loss. The event does not say WHY the unit died (combat, failed
        assault, fuel), so neither does the line."""
        if event.unit_id in self._cause_booked:
            self._cause_booked.discard(event.unit_id)
            return  # already booked with its cause via CityFiredEvent
        ours, section = self._claim(event.unit_id)
        if ours:
            self._losses.append(
                (self._now_turn(), section, event.unit_id, event.last_coord, None)
            )
        self._known_own.pop(event.unit_id, None)

    def record_unit_disbanded(self, event: UnitDisbandedEvent) -> None:
        """A disband (capture-time or support-limit — the event does not
        distinguish, so neither does the line)."""
        ours, section = self._claim(event.unit_id)
        if ours:
            self._disbands.append(
                (self._now_turn(), section, event.unit_id, event.last_coord)
            )
        self._known_own.pop(event.unit_id, None)

    def record_city_fired(self, event: CityFiredEvent) -> None:
        """City artillery against one of ours: a hit is a state change worth
        a line; a kill is a loss WITH a cause the event itself states. A
        miss changes nothing about the force and is not booked."""
        ours, section = self._claim(event.target_id)
        if not ours:
            kind = self._own_unit_kind(event.target_id)  # damaged, still alive
            if kind is None:
                return
            self._known_own.setdefault(event.target_id, kind)
            section = None
        if event.destroyed:
            self._cause_booked.add(event.target_id)
            self._known_own.pop(event.target_id, None)
            self._losses.append(
                (self._now_turn(), section, event.target_id, event.target_coord, "city artillery")
            )
        elif event.hit:
            self._hits.append(
                (self._now_turn(), section, event.target_id, event.target_coord)
            )

    def record_city_captured(self, event: CityCapturedEvent) -> None:
        """Our capture succeeding, booked under every force whose objective
        targets that city, else the general section. Enemy captures are not
        booked: the event carries no previous-owner fact to establish the
        city was ours, and a global bus line would leak through fog."""
        if event.new_owner_id != self._player_id:
            return
        coord = self._city_coord(event.city_id)
        if coord is None:
            return
        turn = self._now_turn()
        targeting = [
            tf.tf_id for tf in self._registry().forces if tf.objective.target == coord
        ]
        if targeting:
            self._captures.extend((turn, tf_id, coord) for tf_id in targeting)
        else:
            self._captures.append((turn, None, coord))

    # ---- refusal replay ------------------------------------------------------------

    def note_refusals(self, refusals: Iterable[Refusal]) -> None:
        """Replay the cannot-comply channel: each refused order appears in
        the next briefing under the force it addressed (if that force
        stands), else in the general section."""
        turn = self._now_turn()
        for refusal in refusals:
            match = _TF_IN_ORDER_TEXT.match(refusal.order_text)
            section: _Section = None
            if match is not None and self._registry().get(match.group(1)) is not None:
                section = match.group(1)
            self._refusals.append((turn, section, refusal))

    # ---- reading + epoch turnover -----------------------------------------------------

    def collect(self) -> LedgerReport:
        """Render everything booked since the last `reset`. Read-only: call
        as often as needed; `reset` opens the next epoch."""
        keys: list[TaskForceId] = []
        for section in self._booked_sections():
            if section is not None and section not in keys:
                keys.append(section)
        return LedgerReport(
            by_task_force={key: self._lines_for(key) for key in keys},
            general=self._lines_for(None),
        )

    def reset(self) -> None:
        """Open a new epoch: drop the booked lines. Ownership knowledge
        (`_known_own`) survives — which units are ours is not an epoch fact."""
        self._losses.clear()
        self._hits.clear()
        self._disbands.clear()
        self._arrivals.clear()
        self._captures.clear()
        self._deliveries.clear()
        self._refusals.clear()
        self._cause_booked.clear()

    # ---- internals -------------------------------------------------------------------

    def _force_of(self, unit_id: UnitId) -> TaskForce | None:
        for tf in self._registry().forces:
            if unit_id in tf.members:
                return tf
        return None

    def _claim(self, unit_id: UnitId) -> tuple[bool, _Section]:
        """(is it ours?, where its facts are booked): a task-force id, or
        `None` for the general section (a known own UNASSIGNED unit). Not
        ours means not our fact — an enemy's loss is never booked."""
        force = self._force_of(unit_id)
        if force is not None:
            return True, force.tf_id
        if unit_id in self._known_own:
            return True, None
        return False, None

    def _booked_sections(self) -> list[_Section]:
        return (
            [s for _, s, _, _, _ in self._losses]
            + [s for _, s, _, _ in self._hits]
            + [s for _, s, _, _ in self._disbands]
            + [s for _, s, _, _ in self._arrivals]
            + [s for _, s, _ in self._captures]
            + [s for _, s, _ in self._refusals]
        )

    def _lines_for(self, section: _Section) -> tuple[str, ...]:
        """One section's lines, each prefixed with its turn and rendered in
        turn order. Within a turn the order is deterministic: deliveries
        (general only), losses grouped by cell and cause, hits, disbands,
        arrivals grouped by cell, captures, refusals. The stable sort by turn
        preserves that within-turn order (planning/08 "TASKING CONTINUITY")."""
        entries: list[tuple[int, str]] = []
        if section is None:
            entries += [
                (turn, f"{kind} {_uid(unit_id)} produced at {_at(at)}")
                for turn, kind, unit_id, at in self._deliveries
            ]

        # Losses group per turn+cell+cause: same-turn deaths at one cell read
        # as one line; deaths across turns stay separate (each carries a turn).
        loss_groups: dict[tuple[int, Coord, str | None], list[UnitId]] = {}
        for turn, sec, unit_id, at, cause in self._losses:
            if sec == section:
                loss_groups.setdefault((turn, at, cause), []).append(unit_id)
        for (turn, at, cause), unit_ids in loss_groups.items():
            ids = ", ".join(_uid(u) for u in unit_ids)
            suffix = f" to {cause}" if cause is not None else ""
            entries.append((turn, f"lost {ids}{suffix} at {_at(at)}"))

        entries += [
            (turn, f"{_uid(unit_id)} hit by city artillery at {_at(at)}")
            for turn, sec, unit_id, at in self._hits
            if sec == section
        ]
        entries += [
            (turn, f"{_uid(unit_id)} disbanded at {_at(at)}")
            for turn, sec, unit_id, at in self._disbands
            if sec == section
        ]

        arrival_groups: dict[tuple[int, Coord], list[UnitId]] = {}
        for turn, sec, unit_id, at in self._arrivals:
            if sec == section:
                bucket = arrival_groups.setdefault((turn, at), [])
                if unit_id not in bucket:
                    bucket.append(unit_id)
        entries += [
            (turn, f"{', '.join(_uid(u) for u in unit_ids)} arrived at {_at(at)}")
            for (turn, at), unit_ids in arrival_groups.items()
        ]

        entries += [
            (turn, f"captured {_at(at)} — now ours")
            for turn, sec, at in self._captures
            if sec == section
        ]
        entries += [
            (turn, f"order refused: {refusal.order_text} — {refusal.reason}")
            for turn, sec, refusal in self._refusals
            if sec == section
        ]
        entries.sort(key=lambda entry: entry[0])  # stable: within-turn order kept
        return tuple(f"t{turn}: {line}" for turn, line in entries)

"""The `Goal` hierarchy's shared foundation: the `Goal` ABC and the value
types it is built from (`ForceComposition`, `ResourceBudget`).

A `Goal` is the strategist's unit of intent — an immutable proposal, frozen on
emission (see `planning/03-ai-design.md` §3.2). It carries a priority, the
resources earmarked for it, an estimated duration, and a `progress_signal`
that later layers read to judge whether it is being achieved. Concrete goals
live in `concrete.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from empire.contracts.world_view import WorldView
from empire.core.identity import CityId, GoalId, UnitId
from empire.core.unit import UnitKind


class GoalKind(Enum):
    """Discriminator for serialization and strategist dispatch."""

    CAPTURE_CITY = "capture_city"
    DEFEND_CITY = "defend_city"
    EXPLORE_AREA = "explore_area"
    PROJECT_POWER = "project_power"
    DENY_CONTINENT = "deny_continent"
    BUILD_FORCES = "build_forces"


@dataclass(frozen=True, slots=True)
class ForceComposition:
    """An immutable required-unit-count-by-kind bag (e.g. 3 armies + 1
    transport). Normalized: entries are sorted by kind and strictly positive,
    so equal compositions compare and hash equal."""

    entries: tuple[tuple[UnitKind, int], ...] = ()

    @classmethod
    def of(cls, counts: Mapping[UnitKind, int]) -> ForceComposition:
        normalized = tuple(
            sorted(
                ((k, n) for k, n in counts.items() if n > 0),
                key=lambda kv: kv[0].value,
            )
        )
        return cls(normalized)

    def count(self, kind: UnitKind) -> int:
        for k, n in self.entries:
            if k is kind:
                return n
        return 0

    def total(self) -> int:
        return sum(n for _, n in self.entries)

    def to_dict(self) -> dict[str, int]:
        return {k.value: n for k, n in self.entries}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ForceComposition:
        return cls.of({UnitKind(k): int(n) for k, n in data.items()})


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """The units, cities, and production slots a goal earmarks. Empty means
    'to be assembled' — the operational layer fills it in (Phase 13)."""

    unit_ids: tuple[UnitId, ...] = ()
    city_ids: tuple[CityId, ...] = ()
    production_slots: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "unit_ids": [int(u) for u in self.unit_ids],
            "city_ids": [int(c) for c in self.city_ids],
            "production_slots": self.production_slots,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResourceBudget:
        return cls(
            unit_ids=tuple(UnitId(int(u)) for u in data.get("unit_ids", [])),
            city_ids=tuple(CityId(int(c)) for c in data.get("city_ids", [])),
            production_slots=int(data.get("production_slots", 0)),
        )


@dataclass(frozen=True)
class Goal(ABC):
    """A strategic intent. Frozen on emission; the operational layer treats it
    as a read-only proposal it may pursue or reject.

    `priority` is 0..1 (higher = more important); `estimated_duration` is in
    turns; `rank()` combines them so the strategist can order goals.
    """

    id: GoalId
    priority: float
    estimated_duration: int
    # Keyword-only with a default so concrete goals can still declare required
    # positional fields without tripping dataclass field-ordering.
    budget: ResourceBudget = field(default_factory=ResourceBudget, kw_only=True)

    @property
    @abstractmethod
    def kind(self) -> GoalKind:
        """The goal's discriminator."""

    @abstractmethod
    def progress_signal(self, view: WorldView) -> float:
        """How complete this goal looks right now, in 0..1, from `view`.

        Pure and deterministic given the view. 1.0 means achieved; 0.0 means
        no progress (or the goal has become moot).
        """

    @abstractmethod
    def _payload(self) -> dict[str, object]:
        """The goal-specific fields, for serialization."""

    def rank(self) -> float:
        """Ordering key: priority discounted by how long the goal takes."""
        return self.priority / max(1, self.estimated_duration)

    def to_dict(self) -> dict[str, object]:
        return {
            "goal": self.kind.value,
            "id": int(self.id),
            "priority": self.priority,
            "estimated_duration": self.estimated_duration,
            "budget": self.budget.to_dict(),
            **self._payload(),
        }

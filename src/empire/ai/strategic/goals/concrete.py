"""The six concrete `Goal` subclasses and the `goal_from_dict` deserializer.

Each goal is a frozen value type carrying its target plus a `progress_signal`
that reads the live `WorldView` to report 0..1 completion. Per
`planning/03-ai-design.md` §3.2.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from empire.ai.strategic.goals.base import (
    ForceComposition,
    Goal,
    GoalKind,
    ResourceBudget,
)
from empire.contracts.world_view import WorldView
from empire.core.coord import Coord
from empire.core.identity import CityId, GoalId


def _coord(data: Any) -> Coord:
    return Coord(int(data[0]), int(data[1]))


def _coords(data: Any) -> tuple[Coord, ...]:
    return tuple(_coord(c) for c in data)


def _city_ids(data: Any) -> tuple[CityId, ...]:
    return tuple(CityId(int(c)) for c in data)


def _explored(view: WorldView, cells: tuple[Coord, ...]) -> float:
    if not cells:
        return 1.0
    seen = sum(
        1
        for c in cells
        if view.is_visible(c) or c in view.remembered_tiles()
    )
    return seen / len(cells)


@dataclass(frozen=True)
class CaptureCityGoal(Goal):
    """Take a specific neutral or enemy city."""

    target_city_id: CityId
    target_coord: Coord

    @property
    def kind(self) -> GoalKind:
        return GoalKind.CAPTURE_CITY

    def progress_signal(self, view: WorldView) -> float:
        # Done once the city is ours; otherwise no credit (capture is binary).
        if any(c.id == self.target_city_id for c in view.own_cities):
            return 1.0
        return 0.0

    def _payload(self) -> dict[str, object]:
        return {
            "target_city_id": int(self.target_city_id),
            "target_coord": [self.target_coord.x, self.target_coord.y],
        }


@dataclass(frozen=True)
class DefendCityGoal(Goal):
    """Hold one of our cities against a projected threat."""

    target_city_id: CityId
    target_coord: Coord
    garrison_size_needed: int

    @property
    def kind(self) -> GoalKind:
        return GoalKind.DEFEND_CITY

    def progress_signal(self, view: WorldView) -> float:
        own = next((c for c in view.own_cities if c.id == self.target_city_id), None)
        if own is None:
            return 0.0  # lost the city — the goal failed
        garrison = sum(
            1
            for u in view.own_units
            if u.coord.chebyshev_to(self.target_coord) <= 1
        )
        if self.garrison_size_needed <= 0:
            return 1.0
        return min(1.0, garrison / self.garrison_size_needed)

    def _payload(self) -> dict[str, object]:
        return {
            "target_city_id": int(self.target_city_id),
            "target_coord": [self.target_coord.x, self.target_coord.y],
            "garrison_size_needed": self.garrison_size_needed,
        }


@dataclass(frozen=True)
class ExploreAreaGoal(Goal):
    """Scout an unexplored region (a fixed set of target cells)."""

    target_region: tuple[Coord, ...]

    @property
    def kind(self) -> GoalKind:
        return GoalKind.EXPLORE_AREA

    def progress_signal(self, view: WorldView) -> float:
        return _explored(view, self.target_region)

    def _payload(self) -> dict[str, object]:
        return {"target_region": [[c.x, c.y] for c in self.target_region]}


@dataclass(frozen=True)
class ProjectPowerGoal(Goal):
    """Invade a target continent — ferry a strike force across the water."""

    target_region: tuple[Coord, ...]
    force_composition: ForceComposition
    transport_count: int

    @property
    def kind(self) -> GoalKind:
        return GoalKind.PROJECT_POWER

    def progress_signal(self, view: WorldView) -> float:
        # Progress = do we yet hold any foothold cell on the target shore?
        target = set(self.target_region)
        if any(c.coord in target for c in view.own_cities):
            return 1.0
        if any(u.coord in target for u in view.own_units):
            return 0.5
        return 0.0

    def _payload(self) -> dict[str, object]:
        return {
            "target_region": [[c.x, c.y] for c in self.target_region],
            "force_composition": self.force_composition.to_dict(),
            "transport_count": self.transport_count,
        }


@dataclass(frozen=True)
class DenyContinentGoal(Goal):
    """Win a contested landmass: take its neutral cities, push the enemy off."""

    target_region: tuple[Coord, ...]
    enemy_city_ids: tuple[CityId, ...]
    neutral_city_ids: tuple[CityId, ...]

    @property
    def kind(self) -> GoalKind:
        return GoalKind.DENY_CONTINENT

    def progress_signal(self, view: WorldView) -> float:
        contested = set(self.enemy_city_ids) | set(self.neutral_city_ids)
        if not contested:
            return 1.0
        owned = sum(1 for c in view.own_cities if c.id in contested)
        return owned / len(contested)

    def _payload(self) -> dict[str, object]:
        return {
            "target_region": [[c.x, c.y] for c in self.target_region],
            "enemy_city_ids": [int(c) for c in self.enemy_city_ids],
            "neutral_city_ids": [int(c) for c in self.neutral_city_ids],
        }


@dataclass(frozen=True)
class BuildForcesGoal(Goal):
    """Grow toward a standing force composition (baseline production intent)."""

    force_composition_target: ForceComposition

    @property
    def kind(self) -> GoalKind:
        return GoalKind.BUILD_FORCES

    def progress_signal(self, view: WorldView) -> float:
        target = self.force_composition_target
        if target.total() <= 0:
            return 1.0
        have = 0
        for kind, needed in target.entries:
            owned = sum(1 for u in view.own_units if u.kind is kind)
            have += min(owned, needed)
        return have / target.total()

    def _payload(self) -> dict[str, object]:
        return {"force_composition_target": self.force_composition_target.to_dict()}


def goal_from_dict(data: Mapping[str, Any]) -> Goal:
    """Reconstruct a `Goal` from its `to_dict()` form."""
    kind = GoalKind(data["goal"])
    gid = GoalId(int(data["id"]))
    priority = float(data["priority"])
    duration = int(data["estimated_duration"])
    budget = ResourceBudget.from_dict(data["budget"])
    if kind is GoalKind.CAPTURE_CITY:
        return CaptureCityGoal(
            id=gid,
            priority=priority,
            estimated_duration=duration,
            budget=budget,
            target_city_id=CityId(int(data["target_city_id"])),
            target_coord=_coord(data["target_coord"]),
        )
    if kind is GoalKind.DEFEND_CITY:
        return DefendCityGoal(
            id=gid,
            priority=priority,
            estimated_duration=duration,
            budget=budget,
            target_city_id=CityId(int(data["target_city_id"])),
            target_coord=_coord(data["target_coord"]),
            garrison_size_needed=int(data["garrison_size_needed"]),
        )
    if kind is GoalKind.EXPLORE_AREA:
        return ExploreAreaGoal(
            id=gid,
            priority=priority,
            estimated_duration=duration,
            budget=budget,
            target_region=_coords(data["target_region"]),
        )
    if kind is GoalKind.PROJECT_POWER:
        return ProjectPowerGoal(
            id=gid,
            priority=priority,
            estimated_duration=duration,
            budget=budget,
            target_region=_coords(data["target_region"]),
            force_composition=ForceComposition.from_dict(data["force_composition"]),
            transport_count=int(data["transport_count"]),
        )
    if kind is GoalKind.DENY_CONTINENT:
        return DenyContinentGoal(
            id=gid,
            priority=priority,
            estimated_duration=duration,
            budget=budget,
            target_region=_coords(data["target_region"]),
            enemy_city_ids=_city_ids(data["enemy_city_ids"]),
            neutral_city_ids=_city_ids(data["neutral_city_ids"]),
        )
    return BuildForcesGoal(
        id=gid,
        priority=priority,
        estimated_duration=duration,
        budget=budget,
        force_composition_target=ForceComposition.from_dict(
            data["force_composition_target"]
        ),
    )

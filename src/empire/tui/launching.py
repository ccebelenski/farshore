"""`GameConfig` + `GameLauncher`: one place that turns "what the player
picked" into a running game.

Both entry paths — the CLI flags (`play-tui`, `viewer`) and the launcher
screen's menus — produce a `GameConfig` and hand it here, so the rules for
"what does a brawl mean", "which profile", and "which opponent" live once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from empire.contracts.controller import AIController
from empire.core.game import Game
from empire.core.player import Player
from empire.core.ruleset import (
    FORTIFIED_CITIES,
    LARGE,
    SMALL,
    STANDARD,
    STANDARD_PROFILE,
    MapProfile,
    RuleSet,
)
from empire.persistence.save_manager import SaveManager

# The two shipped opponents: the classic greedy "horde" (BaselineAI) and the
# stateful multi-focus "portfolio" (PortfolioAI). "baseline" stays the internal
# key for the horde.
OPPONENTS: tuple[str, ...] = ("baseline", "portfolio")
PROFILE_NAMES: tuple[str, ...] = ("SMALL", "STANDARD", "LARGE")

_PROFILES: dict[str, MapProfile] = {
    "SMALL": SMALL,
    "STANDARD": STANDARD_PROFILE,
    "LARGE": LARGE,
}


@dataclass(frozen=True, slots=True)
class GameConfig:
    """Everything the launcher needs to start a new game."""

    opponent: str = "baseline"  # one of OPPONENTS
    fortified: bool = False  # FORTIFIED_CITIES vs classic STANDARD rules
    brawl: bool = False  # shared continent (land brawl) vs classic continents
    profile_name: str = "SMALL"  # one of PROFILE_NAMES
    seed: int = 0

    def with_(self, **changes: object) -> GameConfig:
        return replace(self, **changes)  # type: ignore[arg-type]

    @property
    def rules(self) -> RuleSet:
        return FORTIFIED_CITIES if self.fortified else STANDARD

    def describe(self) -> str:
        mode = "land brawl" if self.brawl else "continents"
        return (
            f"{self.opponent} · {self.rules.name} · {mode} · "
            f"{self.profile_name} · seed {self.seed}"
        )


@dataclass(frozen=True, slots=True)
class LaunchedGame:
    """A ready-to-play game: the human's seat plus the wired opponent kind."""

    game: Game
    human: Player
    opponent: str


class GameLauncher:
    """Builds or restores games from player choices."""

    def make_opponent(self, kind: str) -> AIController:
        # "portfolio" is the smart opponent (PortfolioAI). Legacy save strings
        # ("search"/"strategic") map to it too, so old games still load against a
        # real AI rather than silently dropping to the horde.
        if kind in ("portfolio", "search", "strategic"):
            from empire.ai.search.portfolio import PortfolioAI

            return PortfolioAI()
        from empire.ai.baseline import BaselineAI

        return BaselineAI()

    def build(self, config: GameConfig) -> LaunchedGame:
        """A new game per `config`, opponent attached, human seat open.

        Raises `RuntimeError` when no suitable map turns up for the seed
        (callers surface that as "try another seed").
        """
        if config.brawl:
            from empire._arena import ARENA_PROFILE, build_land_brawl

            # SMALL rarely yields a 5-city shared continent; the arena
            # profile is the tuned brawl map.
            profile = (
                _PROFILES[config.profile_name]
                if config.profile_name in ("STANDARD", "LARGE")
                else ARENA_PROFILE
            )
            built = build_land_brawl(profile, config.seed, config.rules)
            if built is None:
                raise RuntimeError(
                    f"no land-brawl map for seed {config.seed}; try another seed"
                )
            game, players = built
            players[0].is_ai = False
        else:
            from empire.setup import build_game

            game, players = build_game(
                _PROFILES[config.profile_name],
                config.seed,
                rules=config.rules,
                p1_is_ai=False,
                p2_is_ai=True,
            )
        game.attach_controller(players[1].id, self.make_opponent(config.opponent))
        return LaunchedGame(game=game, human=players[0], opponent=config.opponent)

    def restore(self, path: Path, opponent: str) -> LaunchedGame:
        """Load a save and seat the human as the first non-AI player (or the
        first player, for saves where both seats were AI)."""
        game = SaveManager().load(path)
        human = next(
            (p for p in game.players if not p.is_ai), game.players[0]
        )
        for p in game.players:
            if p is not human:
                game.attach_controller(p.id, self.make_opponent(opponent))
        return LaunchedGame(game=game, human=human, opponent=opponent)

    @staticmethod
    def list_saves(directory: Path | None = None) -> list[Path]:
        """Save files in `directory` (default: cwd), newest first. A save is
        any .json whose top level carries a schema_version."""
        root = directory if directory is not None else Path.cwd()
        found: list[Path] = []
        for path in sorted(root.glob("*.json")):
            try:
                with path.open() as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and "schema_version" in payload:
                found.append(path)
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return found

    @staticmethod
    def peek_turn(path: Path) -> int | None:
        """The saved game's turn number, for display; None if unreadable."""
        try:
            with path.open() as fh:
                payload = json.load(fh)
            return int(payload["turn"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None

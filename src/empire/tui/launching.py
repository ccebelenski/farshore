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
        # real AI rather than silently dropping to the horde. When the LLM
        # general is enabled in the app config, the smart seat gets the general
        # wrapped around that same PortfolioAI; disabled/absent config yields
        # plain PortfolioAI (build_general's total default).
        if kind in ("portfolio", "search", "strategic"):
            from empire.ai.general import build_general
            from empire.config import ConfigStore

            return build_general(ConfigStore().load())
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
        self._seat_opponent(game, players[1], config.opponent)
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
                self._seat_opponent(game, p, opponent)
        return LaunchedGame(game=game, human=human, opponent=opponent)

    def _seat_opponent(self, game: Game, player: Player, kind: str) -> None:
        """Attach the opponent controller — and, when the seat is the LLM
        general, wire its event ledger onto the game's bus (the launcher is
        the layer that owns both the game and the bus, so the ai layer never
        constructs one — see empire.ai.general.ledger)."""
        controller = self.make_opponent(kind)
        game.attach_controller(player.id, controller)
        from empire.ai.general.controller import LlmGeneralController

        if not isinstance(controller, LlmGeneralController):
            return
        from empire.ai.general.ledger import TaskForceLedger

        board = game.map
        ledger = TaskForceLedger(
            player_id=player.id,
            registry=lambda: controller.registry,
            own_unit_kind=lambda uid: (
                u.kind.value
                if (u := board.unit_by_id(uid)) is not None and u.owner is player
                else None
            ),
            city_coord=lambda cid: (
                c.coord if (c := board.city_by_id(cid)) is not None else None
            ),
        )
        ledger.attach(game.event_bus)
        controller.attach_ledger(ledger)

        # The general's war diary: one JSONL per game, next to the config
        # (post-game analysis + the fine-tuning corpus, planning/08).
        from datetime import datetime

        from empire.ai.general.trace import EpochTraceWriter
        from empire.config import ConfigStore

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        trace_path = ConfigStore.default_path().parent / "traces" / f"general-{stamp}.jsonl"
        controller.attach_trace(EpochTraceWriter(trace_path))

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

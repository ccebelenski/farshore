"""`Game`: the root aggregate. Holds the whole game state.

Phase-4 scope:
- Construction with `RuleSet`, `Map`, players, optional seed, optional bus.
- `attach_controller` / `controllers` dict for AI wiring (controllers are
  externally injected; they do *not* round-trip through save/load — see
  `empire.persistence.schema_v1`).
- `run_turn()` delegates to a `TurnManager` whose phase methods are empty
  placeholders; Phase 8 fills them in with the real production / movement /
  combat / fog logic.
- `is_over()` / `winner()` use the spec's "zero cities" rule
  (`planning/01-game-rules-spec.md` §8). Phase 8 refines the end-of-turn
  check; the predicate itself is correct as written.

Game lives in `core` per the planning layout, so it must not import from
`empire.events` (dep matrix). It talks to the bus via the
`EventBusProtocol` defined in `empire.core.event_bus`.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from empire.core.event_bus import EventBusProtocol, NullEventBus
from empire.core.identity import PlayerId

if TYPE_CHECKING:
    from empire.contracts.controller import AIController
    from empire.core.map import Map
    from empire.core.player import Player
    from empire.core.ruleset import RuleSet


class Game:
    """The root aggregate. Holds the whole authoritative game state.

    Construction is direct: pass in fully-built `RuleSet`, `Map`, and
    `Player` list. `SaveManager.load` reconstructs these in topological
    order and then calls `Game.__init__` with the assembled pieces.

    Controllers are *not* held by Players; they live on `Game.controllers`
    so `core` stays free of AI-package imports (see
    `planning/04-class-hierarchy.md` §2).
    """

    def __init__(
        self,
        rules: RuleSet,
        real_map: Map,
        players: list[Player],
        seed: int | None = None,
        event_bus: EventBusProtocol | None = None,
    ) -> None:
        self.rules: RuleSet = rules
        self.map: Map = real_map
        self.players: list[Player] = players
        self.turn: int = 0
        self.rng: random.Random = random.Random(seed)
        self.event_bus: EventBusProtocol = event_bus if event_bus is not None else NullEventBus()
        self.controllers: dict[PlayerId, AIController] = {}
        self.turn_manager: TurnManager = TurnManager(self)

    # ---- controller wiring -------------------------------------------------

    def attach_controller(self, player_id: PlayerId, controller: AIController) -> None:
        """Wire an AI controller to a player. Replaces any existing controller."""
        self.controllers[player_id] = controller

    # ---- player lookup -----------------------------------------------------

    def player_by_id(self, player_id: PlayerId) -> Player | None:
        for p in self.players:
            if p.id == player_id:
                return p
        return None

    # ---- turn loop ---------------------------------------------------------

    def run_turn(self) -> None:
        """Advance one full round (every player's turn + end-of-round bookkeeping)."""
        from empire.core.events import GameEndedEvent, TurnAdvancedEvent

        self.turn_manager.run_round()
        self.turn += 1
        self.event_bus.publish(TurnAdvancedEvent(turn=self.turn))
        if self.is_over():
            winner = self.winner()
            self.event_bus.publish(
                GameEndedEvent(
                    winner_id=winner.id if winner is not None else None,
                    final_turn=self.turn,
                )
            )

    # ---- endgame predicate -------------------------------------------------

    def is_over(self) -> bool:
        """Per spec §8: a player loses when they have zero cities at the end
        of any turn. Game is over if any player has lost.

        Conditions:
        - No cities on map at all: not over (early/test state; nobody can lose).
        - All cities neutral: not over (nobody has played yet).
        - Some player has zero cities AND some other player has at least one:
          the city-less player has lost; game is over.
        - Every player has at least one city: not over.
        """
        cities = list(self.map.cities())
        if not cities:
            return False
        for player in self.players:
            player_has_city = any(c.owner is player for c in cities)
            if not player_has_city:
                anyone_else_has = any(
                    c.owner is not None and c.owner is not player for c in cities
                )
                if anyone_else_has:
                    return True
        return False

    def winner(self) -> Player | None:
        """The sole owner of cities, if `is_over()`; otherwise None.

        Returns None for draws (multiple players have lost simultaneously
        and no single player remains with cities).
        """
        if not self.is_over():
            return None
        owners_with_cities = {c.owner.id for c in self.map.cities() if c.owner is not None}
        if len(owners_with_cities) == 1:
            winner_id = PlayerId(next(iter(owners_with_cities)))
            return self.player_by_id(winner_id)
        return None


class TurnManager:
    """Orchestrates phases within a round.

    Phase 4: phase methods exist but are no-ops. Phase 8 fills them in
    with the real production / movement / combat / fog logic.
    """

    def __init__(self, game: Game) -> None:
        self.game: Game = game

    def run_round(self) -> None:
        """Run every player's turn in order, then end-of-round bookkeeping."""
        for player in self.game.players:
            self._production_phase(player)
            self._movement_phase(player)
        self._end_of_round()

    def _production_phase(self, player: Player) -> None:
        """Phase 8: tick every city owned by `player`, emit any new units."""
        del player  # stubbed

    def _movement_phase(self, player: Player) -> None:
        """Phase 8: ask the controller (or wait for human input) for a TurnPlan;
        apply moves one cell at a time, resolving combat and fog updates.
        """
        del player  # stubbed

    def _end_of_round(self) -> None:
        """Phase 8: decay any time-based state (Satellite lifetime, etc.)."""

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

from empire.core.coord import Coord
from empire.core.engine import (
    CombatResolverProtocol,
    execute_unit_path,
    run_production_tick,
    scan_set_for_player,
)
from empire.core.event_bus import EventBusProtocol, NullEventBus
from empire.core.identity import PlayerId, UnitId

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
        combat_resolver: CombatResolverProtocol | None = None,
    ) -> None:
        self.rules: RuleSet = rules
        self.map: Map = real_map
        self.players: list[Player] = players
        self.turn: int = 0
        self.rng: random.Random = random.Random(seed)
        self.event_bus: EventBusProtocol = event_bus if event_bus is not None else NullEventBus()
        self.controllers: dict[PlayerId, AIController] = {}
        self.combat_resolver: CombatResolverProtocol = (
            combat_resolver if combat_resolver is not None else _NullCombatResolver()
        )
        # Monotonic counter for newly-produced unit IDs. Initialized below
        # max existing unit ID so new units don't collide with loaded ones.
        existing_ids = [int(u.id) for u in real_map.all_units()]
        self._next_unit_id: int = (max(existing_ids) if existing_ids else 0) + 1
        self.turn_manager: TurnManager = TurnManager(self)

    def allocate_unit_id(self) -> UnitId:
        """Return the next fresh `UnitId` for newly-produced units.

        Called by the production phase via `TurnManager`. Counter is
        initialized above the max existing id at construction so loaded
        saves continue numbering without collision.
        """
        nid = UnitId(self._next_unit_id)
        self._next_unit_id += 1
        return nid

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

    Per round:
    1. For each player in order:
       a. Production phase: each owned city ticks; ready ones emit units.
       b. Movement phase: ask the player's controller for a TurnPlan, apply
          it (move validation, combat, city capture), then update the
          player's fog state from their new scan.
    2. End-of-round bookkeeping (placeholder for satellite decay, etc.).
    """

    def __init__(self, game: Game) -> None:
        self.game: Game = game

    def run_round(self) -> None:
        for player in self.game.players:
            self._production_phase(player)
            self._movement_phase(player)
            self._scan_phase(player)
        self._end_of_round()

    def _production_phase(self, player: Player) -> None:
        produced = run_production_tick(
            player=player,
            real_map=self.game.map,
            rules=self.game.rules,
            next_unit_id_fn=self.game.allocate_unit_id,
        )
        # Emit events for produced units. Imports kept local to avoid a
        # core → events import cycle (the dataclasses live in core/events).
        from empire.core.events import UnitPlacedEvent

        for u in produced:
            self.game.event_bus.publish(UnitPlacedEvent(unit_id=u.id, at=u.coord))

    def _movement_phase(self, player: Player) -> None:
        controller = self.game.controllers.get(player.id)
        if controller is None:
            # Human-controlled or no controller yet wired; nothing to do
            # at this layer. Higher layers (TUI / scripted drivers) drive
            # human moves out-of-band.
            return

        # Build the WorldView for this controller's call.
        from empire.contracts.world_view import WorldView

        view = WorldView(
            real_map=self.game.map,
            player=player,
            turn=self.game.turn,
            rules=self.game.rules,
        )
        plan = controller.plan_turn(view)
        self._apply_turn_plan(player, plan)

    def _apply_turn_plan(self, player: Player, plan: object) -> None:
        """Apply a controller's TurnPlan against the game state.

        `plan` typed as `object` here to avoid a core-layer import of
        `empire.contracts.turn_plan`; we duck-type the fields we need.
        Caller wires up a real `TurnPlan` per `empire.contracts`.
        """
        from empire.core.events import (
            CityCapturedEvent,
            UnitMovedEvent,
            UnitRemovedEvent,
        )

        # Production orders first (so any subsequent production accounting
        # uses the new target).
        for order in getattr(plan, "production_orders", ()):
            city = self.game.map.tile(_coord_of_city(order, self.game.map)).city
            if city is None or city.owner is not player:
                continue
            if order.target is None:
                city.production.building = None
                city.production.work = 0
            else:
                city.production.set_target(
                    order.target, self.game.rules.production_change_penalty_divisor
                )

        # Apply each unit's planned path step-by-step.
        for move in getattr(plan, "moves", ()):
            unit = self.game.map.unit_by_id(move.unit_id)
            if unit is None or unit.owner is not player:
                continue
            start = unit.coord
            outcome = execute_unit_path(
                unit=unit,
                path=tuple(move.path),
                real_map=self.game.map,
                rules=self.game.rules,
                combat_resolver=self.game.combat_resolver,
                rng=self.game.rng,
            )
            # Emit events. Simplified: one moved event if the unit moved at
            # all, one removed event per destroyed unit, one captured event
            # per captured city.
            if outcome.steps_taken > 0 and self.game.map.unit_by_id(unit.id) is not None:
                self.game.event_bus.publish(
                    UnitMovedEvent(unit_id=unit.id, from_=start, to=unit.coord)
                )
            for uid in outcome.units_destroyed:
                self.game.event_bus.publish(
                    UnitRemovedEvent(unit_id=uid, last_coord=start)
                )
            for cid in outcome.cities_captured:
                self.game.event_bus.publish(
                    CityCapturedEvent(
                        city_id=cid,
                        new_owner_id=player.id,
                        previous_owner_id=None,  # not tracked at this layer
                    )
                )

    def _scan_phase(self, player: Player) -> None:
        scanned = scan_set_for_player(player, self.game.map)
        player.view.update_from_scan(scanned, self.game.map, self.game.turn)

    def _end_of_round(self) -> None:
        # Placeholder: satellite lifetime decay, fighter fuel attrition,
        # repair logic. None of these are wired in Phase 8.
        pass


def _coord_of_city(order: object, real_map: Map) -> Coord:
    """Look up the coord of the city referenced by a ProductionOrder.

    Returns a dummy coord if not found (caller filters out None city).
    """
    cid = getattr(order, "city_id", None)
    if cid is None:
        return Coord(0, 0)
    found = real_map.city_by_id(cid)
    if found is None:
        return Coord(0, 0)
    return found.coord


# -----------------------------------------------------------------------------
# Default combat resolver: errors if invoked.
# -----------------------------------------------------------------------------


class _NullCombatResolver:
    """Default combat resolver for `Game` instances created without one.

    Raises on `resolve` — combat needs a real implementation. Pass
    `combat_resolver=CombatResolver()` from `empire.combat.resolver` to
    enable combat. This default keeps `core` free of an
    `empire.combat` dependency at import time; callers from any layer
    that *can* depend on combat (engine, drivers, tests) wire the real
    resolver explicitly.
    """

    def resolve(
        self,
        attacker: object,
        defender: object,
        rng: random.Random,
    ) -> object:
        del attacker, defender, rng
        raise RuntimeError(
            "No combat resolver wired. Pass combat_resolver=CombatResolver() "
            "from empire.combat.resolver to Game() to enable combat."
        )

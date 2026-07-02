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
    ArtilleryResult,
    CombatResolverProtocol,
    MoveOutcome,
    advance_satellites,
    apply_standing_orders,
    crash_out_of_fuel_fighters,
    disband_overcrowded_city_units,
    execute_unit_path,
    refresh_player_view,
    repair_in_cities,
    run_production_tick,
    wake_sentried_units,
)
from empire.core.event_bus import EventBusProtocol, NullEventBus
from empire.core.identity import CityId, PlayerId, UnitId

if TYPE_CHECKING:
    from empire.contracts.controller import AIController
    from empire.core.map import Map
    from empire.core.player import Player
    from empire.core.ruleset import RuleSet
    from empire.core.unit import Unit


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
        next_unit_id: int | None = None,
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
        # Monotonic counter for newly-produced unit IDs. Loaders pass the
        # saved counter (`next_unit_id`) verbatim so ids of *dead* units are
        # never reissued — a reissued id could collide with `UnitSnapshot`
        # refs in players' remembered intel — and so a loaded game replays
        # exactly like the original. The max-derivation fallback covers
        # fresh games and pre-counter saves.
        if next_unit_id is not None:
            self._next_unit_id: int = next_unit_id
        else:
            existing_ids = [int(u.id) for u in real_map.all_units()]
            self._next_unit_id = (max(existing_ids) if existing_ids else 0) + 1
        self.turn_manager: TurnManager = TurnManager(self)

    @property
    def next_unit_id(self) -> int:
        """The id the next produced unit will receive (persisted by saves)."""
        return self._next_unit_id

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
        from empire.core.engine import clear_movement_pins, reset_city_artillery

        # Re-arm every city's one-shot artillery and clear last round's
        # movement pins before anything moves (spec §4.7).
        reset_city_artillery(self.game.map)
        clear_movement_pins(self.game.map)
        # Opening barrage: ALL cities (every player's + neutral) fire once,
        # before anyone moves. This is symmetric — no unit has moved yet, so
        # there is no first-mover advantage — and it is what shoots an army
        # sitting adjacent *before* it can step onto the city to capture it.
        self._opening_barrage_phase()
        for player in self.game.players:
            produced = self._production_phase(player)
            self._standing_orders_phase(player)
            self._movement_phase(player)
            self._scan_phase(player)
            # City artillery (spec §4.7) fires AFTER this player has moved and
            # scanned — so it has already discovered any gun about to shell it
            # — and never as a pre-emptive interrupt of the move itself. The
            # shells "take time to walk in": a city acts on its own, deferred.
            self._city_artillery_phase(player)
            # Turn-end (spec §5.4): the disband runs at the END of the
            # owner's own segment, so a conquering army is never still
            # standing when the opponent moves. Units produced this very
            # segment are exempt — they were born after their owner's plan
            # was made and get until next turn-end to march out.
            self._disband_phase(player, exempt=frozenset(u.id for u in produced))
        self._end_of_round()

    def _opening_barrage_phase(self) -> None:
        """City artillery's opening salvo (spec §4.7): before any player moves,
        every city — owned or neutral — fires once at its most dangerous
        in-range enemy. Symmetric across players (nobody has moved), so it
        carries no turn-order advantage and neutralises first-mover bias in a
        standoff. A city that fires here has spent its one shot for the round;
        the per-segment `_city_artillery_phase` then only fires cities that
        still have their shot (a fresh contact that moved into range this
        round). No-op unless the ruleset enables artillery.
        """
        if self.game.rules.city_artillery_range <= 0:
            return
        from empire.core.engine import resolve_city_artillery

        self._publish_artillery(
            resolve_city_artillery(self.game.map, self.game.rules, self.game.rng)
        )

    def _city_artillery_phase(self, player: Player) -> None:
        """Deferred defensive volley (spec §4.7): after `player` has moved and
        scanned, every hostile city with a shot still in hand fires one salvo
        at `player`'s highest-priority unit in range.

        Deferred, not reactive: `player` already scanned this segment, so a
        gun it moved next to is discovered before it fires — no "hit from
        nowhere". A city fires as its own action, never as a pre-emptive
        interrupt of the move. Most cities already spent their shot in the
        opening barrage; this catches units that moved into range afterwards.
        """
        if self.game.rules.city_artillery_range <= 0:
            return
        from empire.core.engine import resolve_city_artillery

        self._publish_artillery(
            resolve_city_artillery(
                self.game.map, self.game.rules, self.game.rng, target_owner=player
            )
        )

    def _publish_artillery(
        self, results: list[tuple[CityId, ArtilleryResult, Coord]]
    ) -> None:
        """Emit the bus events for one artillery volley's results (shared by
        the opening barrage and the per-segment phase). The reveal-the-gun
        fog update already happened inside `_fire_artillery`."""
        from empire.core.engine import ArtilleryOutcome
        from empire.core.events import CityFiredEvent, UnitRemovedEvent

        for city_id, result, tcoord in results:
            if result.target_id is None:
                continue
            destroyed = result.outcome is ArtilleryOutcome.TARGET_DESTROYED
            hit = destroyed or result.outcome is ArtilleryOutcome.TARGET_DAMAGED
            self.game.event_bus.publish(
                CityFiredEvent(
                    city_id=city_id,
                    target_id=result.target_id,
                    target_coord=tcoord,
                    hit=hit,
                    destroyed=destroyed,
                )
            )
            if destroyed:
                self.game.event_bus.publish(
                    UnitRemovedEvent(unit_id=result.target_id, last_coord=tcoord)
                )

    def _disband_phase(
        self, player: Player, exempt: frozenset[UnitId] = frozenset()
    ) -> None:
        """Turn-end for `player`: disband units left in a friendly city beyond
        its support limit (spec §5.4).

        Runs at the END of the player's own segment — a conquering army
        disbands into the city it just took before any opponent acts (it
        used to survive a full enemy turn as a corpse-shield, and the TUI
        would even offer it orders that then evaporated; playtest bug,
        2026-06-12). `exempt` carries this segment's freshly-produced units:
        their owner plans before production runs, so they get until next
        turn-end to march out.
        """
        from empire.core.events import UnitDisbandedEvent

        # Snapshot coords before removal so the event carries a real location.
        coords = {u.id: u.coord for u in self.game.map.all_units()}
        for uid in disband_overcrowded_city_units(player, self.game.map, exempt):
            self.game.event_bus.publish(
                UnitDisbandedEvent(unit_id=uid, last_coord=coords[uid])
            )

    def _standing_orders_phase(self, player: Player) -> None:
        """Apply each owned unit's persistent order, before the controller plans.

        Order: wake sentried units who can see an enemy, then step every
        Heading/PatrolPath. The controller's TurnPlan still runs after; the
        TUI / AI is expected to skip units whose `standing_order` is not
        None (engine drove them this turn).
        """
        from empire.core.events import UnitMovedEvent

        woken = wake_sentried_units(player, self.game.map)
        # Note: bus event for wake-up is intentionally light — log shows
        # the unit re-entering auto-cycle. A dedicated SentryWokeEvent
        # could land later if intel/UX needs it.
        del woken

        # Snapshot start coords so we can emit accurate movement events.
        starts: dict[UnitId, Coord] = {
            u.id: u.coord
            for u in self.game.map.all_units()
            if u.owner is player and u.standing_order is not None
        }
        result = apply_standing_orders(
            player=player,
            real_map=self.game.map,
            rules=self.game.rules,
            combat_resolver=self.game.combat_resolver,
            rng=self.game.rng,
        )
        # Artillery no longer fires reactively mid-move; hostile cities shell
        # this player's units in the deferred `_city_artillery_phase` after
        # the scan, so there is nothing to publish here.
        for uid in result.moved_unit_ids:
            unit = self.game.map.unit_by_id(uid)
            if unit is None:
                continue
            start = starts.get(uid, unit.coord)
            if start != unit.coord:
                self.game.event_bus.publish(
                    UnitMovedEvent(unit_id=uid, from_=start, to=unit.coord)
                )

    def _production_phase(self, player: Player) -> list[Unit]:
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
        return produced

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

        # Standing-order set/clear declarations. Applied before moves so
        # that a controller declaring "clear my heading" + "walk this
        # path" sequences cleanly; the cleared order doesn't try to step
        # post hoc. The set_orders are picked up by the NEXT turn's
        # standing-orders phase.
        for so in getattr(plan, "set_orders", ()):
            unit = self.game.map.unit_by_id(so.unit_id)
            if unit is None or unit.owner is not player:
                continue
            unit.standing_order = so.order

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
            self._publish_move_outcome(player, move.unit_id, start, outcome)

        # Amphibious landings, after moves so a carrier can sail adjacent to
        # the target this turn and then disembark.
        from empire.core.engine import execute_unload

        for unload in getattr(plan, "unloads", ()):
            cargo = self.game.map.unit_by_id(unload.cargo_id)
            if cargo is None or cargo.owner is not player:
                continue
            carrier = (
                self.game.map.unit_by_id(cargo.carried_by)
                if cargo.carried_by is not None
                else None
            )
            start = carrier.coord if carrier is not None else cargo.coord
            outcome = execute_unload(
                cargo=cargo,
                to=Coord(unload.to[0], unload.to[1]),
                real_map=self.game.map,
                rules=self.game.rules,
                combat_resolver=self.game.combat_resolver,
                rng=self.game.rng,
            )
            self._publish_move_outcome(player, unload.cargo_id, start, outcome)

    def _publish_move_outcome(
        self, player: Player, unit_id: UnitId, start: Coord, outcome: MoveOutcome
    ) -> None:
        """Shared by `moves` and amphibious `unloads`. City artillery is not
        part of a move — it fires in the deferred `_city_artillery_phase`."""
        from empire.core.reporting import publish_move_outcome

        publish_move_outcome(
            self.game.event_bus, self.game.map, player.id, unit_id, start, outcome
        )

    def _scan_phase(self, player: Player) -> None:
        refresh_player_view(player, self.game.map, self.game.turn)

    def _end_of_round(self) -> None:
        from empire.core.events import UnitMovedEvent, UnitRemovedEvent

        # Last-known coords for informational removal/move events (some units
        # are gone or relocated by the time we publish).
        last_coords = {u.id: u.coord for u in self.game.map.all_units()}

        # Clear the per-turn cargo guard so units loaded this round may
        # unload next round (spec §3.4: unload uses the *next* turn's budget).
        for unit in self.game.map.all_units():
            unit.loaded_this_turn = False

        # Fighters out of fuel and not landed crash (spec §3.5).
        for uid in crash_out_of_fuel_fighters(self.game.map, self.game.rules):
            self.game.event_bus.publish(
                UnitRemovedEvent(unit_id=uid, last_coord=last_coords[uid])
            )

        # Satellites orbit one cell and lose a turn of lifetime (spec §2.4).
        moved, deorbited = advance_satellites(self.game.map)
        for uid in moved:
            sat = self.game.map.unit_by_id(uid)
            if sat is not None:
                self.game.event_bus.publish(
                    UnitMovedEvent(
                        unit_id=uid, from_=last_coords[uid], to=sat.coord
                    )
                )
        for uid in deorbited:
            self.game.event_bus.publish(
                UnitRemovedEvent(unit_id=uid, last_coord=last_coords[uid])
            )

        # Stationary units in friendly cities repair (spec §2.3). Also clears
        # the per-round movement flags.
        repair_in_cities(self.game.map)


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

"""`LogPanel` fog-of-war filter tests.

The widget subscribes to `EventBus` and writes filtered lines into its
embedded `RichLog`. Filter rules:
  - Turn ticks and GameEnded always show (game-wide events).
  - Own-unit events (production / movement) always show.
  - Enemy production / movement / destruction / capture (LIVE events) show
    only if the relevant coord is CURRENTLY visible — not merely remembered
    (stale terrain you can't watch).
  - City capture shows if the city is now own, or its coord is visible.

These tests mount the widget in a minimal Textual app so its `query_one`
plumbing works, publish events on a bus, then introspect the
underlying `RichLog.lines` deque to verify which lines were emitted.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import RichLog

from empire.core.city import City
from empire.core.coord import Coord
from empire.core.events import (
    CityCapturedEvent,
    CityFiredEvent,
    GameEndedEvent,
    TurnAdvancedEvent,
    UnitMovedEvent,
    UnitPlacedEvent,
    UnitRemovedEvent,
)
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army
from empire.events.bus import EventBus
from empire.tui.widgets import LogPanel
from tests.empire.support import land_map as _land_map


class _LogHost(App[None]):
    """Minimal app that mounts a single LogPanel for testing."""

    def __init__(self) -> None:
        super().__init__()
        self.panel = LogPanel()

    def compose(self) -> ComposeResult:
        yield self.panel


def _lines(panel: LogPanel) -> list[str]:
    """Plain-text content of every line in the panel's RichLog."""
    log = panel.query_one(RichLog)
    # RichLog.lines is a deque of Strip; each Strip's `.text` is the joined
    # rendered text (markup tags consumed).
    return [strip.text for strip in log.lines]


async def test_turn_tick_always_logs() -> None:
    """Game-wide events fire regardless of fog state."""
    p = Player(id=PlayerId(1), name="P", is_ai=False, view=ViewMap())
    real_map = _land_map(4, 4)
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, p)
        bus.publish(TurnAdvancedEvent(turn=5))
        await host.workers.wait_for_complete()
        assert any("turn 5" in line for line in _lines(host.panel))


async def test_own_unit_production_always_logs() -> None:
    """Own production fires even when the cell isn't 'visible' — the
    player owns the unit, so fog doesn't apply."""
    p = Player(id=PlayerId(1), name="P", is_ai=False, view=ViewMap())
    real_map = _land_map(4, 4)
    army = Army(UnitId(1), p, Coord(2, 2))
    real_map.place_unit(army, Coord(2, 2))
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, p)
        # Note: viewer's visible set is empty, but the unit is OURS.
        bus.publish(UnitPlacedEvent(unit_id=UnitId(1), at=Coord(2, 2)))
        await host.workers.wait_for_complete()
        assert any("army#1" in line for line in _lines(host.panel))


async def test_enemy_production_at_unseen_cell_does_not_log() -> None:
    """The headline fog leak we just fixed: enemy production at an unseen
    cell must not appear in the log. (Was the "2 armies on turn 5" bug.)"""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="Enemy", is_ai=True, view=ViewMap())
    real_map = _land_map(4, 4)
    # Place an enemy unit somewhere the viewer can't see.
    enemy_army = Army(UnitId(99), enemy, Coord(3, 3))
    real_map.place_unit(enemy_army, Coord(3, 3))
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(UnitPlacedEvent(unit_id=UnitId(99), at=Coord(3, 3)))
        await host.workers.wait_for_complete()
        assert not any("#99" in line for line in _lines(host.panel))


async def test_enemy_production_at_visible_cell_logs() -> None:
    """Once the viewer can see the cell, enemy events at that cell appear."""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="Enemy", is_ai=True, view=ViewMap())
    real_map = _land_map(4, 4)
    enemy_army = Army(UnitId(99), enemy, Coord(3, 3))
    real_map.place_unit(enemy_army, Coord(3, 3))
    me.view.visible.add(Coord(3, 3))
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(UnitPlacedEvent(unit_id=UnitId(99), at=Coord(3, 3)))
        await host.workers.wait_for_complete()
        assert any("army#99" in line for line in _lines(host.panel))


async def test_enemy_movement_at_unseen_cells_does_not_log() -> None:
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="Enemy", is_ai=True, view=ViewMap())
    real_map = _land_map(4, 4)
    enemy_army = Army(UnitId(99), enemy, Coord(3, 3))
    real_map.place_unit(enemy_army, Coord(3, 3))
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(
            UnitMovedEvent(unit_id=UnitId(99), from_=Coord(3, 3), to=Coord(2, 3)),
        )
        await host.workers.wait_for_complete()
        assert not any("#99" in line for line in _lines(host.panel))


async def test_capture_of_own_city_always_logs() -> None:
    """If the captured city is now ours, log it (great news). Coord
    visibility doesn't matter — we owned it before *or* we just captured."""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    city = City(id=CityId(1), coord=Coord(3, 3), owner=me)
    real_map = _land_map(4, 4, cities={Coord(3, 3): city})
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(
            CityCapturedEvent(
                city_id=CityId(1),
                new_owner_id=me.id,
                previous_owner_id=None,
            ),
        )
        await host.workers.wait_for_complete()
        assert any("city#1" in line for line in _lines(host.panel))


async def test_capture_of_unseen_enemy_city_does_not_log() -> None:
    """Enemy-on-enemy or enemy-on-neutral capture in fog: stays hidden."""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="Enemy", is_ai=True, view=ViewMap())
    city = City(id=CityId(1), coord=Coord(3, 3), owner=enemy)
    real_map = _land_map(4, 4, cities={Coord(3, 3): city})
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(
            CityCapturedEvent(
                city_id=CityId(1),
                new_owner_id=enemy.id,
                previous_owner_id=None,
            ),
        )
        await host.workers.wait_for_complete()
        assert not any("city#1" in line for line in _lines(host.panel))


async def test_unit_destruction_at_unseen_cell_does_not_log() -> None:
    """Destruction is treated as a conflict event — only emitted if the
    coord is visible/remembered. (Can't look up ownership, the unit is
    already gone.)"""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    real_map = _land_map(4, 4)
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(
            UnitRemovedEvent(unit_id=UnitId(99), last_coord=Coord(3, 3)),
        )
        await host.workers.wait_for_complete()
        assert not any("destroyed" in line for line in _lines(host.panel))


async def test_game_ended_always_logs() -> None:
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    real_map = _land_map(4, 4)
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(GameEndedEvent(winner_id=me.id, final_turn=42))
        await host.workers.wait_for_complete()
        assert any("game over" in line for line in _lines(host.panel))


async def test_destroyed_unit_keeps_its_kind_label_via_cache() -> None:
    """A destruction event fires after the unit is off the map, but the log
    still names its kind: the id->kind cache, primed at attach and topped up
    on observation, survives the unit's removal (kinds are immutable)."""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="Enemy", is_ai=True, view=ViewMap())
    real_map = _land_map(4, 4)
    enemy_army = Army(UnitId(99), enemy, Coord(3, 3))
    real_map.place_unit(enemy_army, Coord(3, 3))
    me.view.visible.add(Coord(3, 3))
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        # Cache is primed from the map at attach (army#99 present).
        host.panel.attach_to(bus, real_map, me)
        # Now the unit dies and is gone from the map before the event.
        real_map.remove_unit(enemy_army)
        bus.publish(UnitRemovedEvent(unit_id=UnitId(99), last_coord=Coord(3, 3)))
        await host.workers.wait_for_complete()
        assert any("army#99 destroyed" in line for line in _lines(host.panel))


async def test_never_observed_unit_falls_back_to_generic_label() -> None:
    """A unit the log never cached (never on the map, never moved) degrades
    gracefully to 'unit#N' rather than crashing."""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    real_map = _land_map(4, 4)
    me.view.visible.add(Coord(2, 2))
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(UnitRemovedEvent(unit_id=UnitId(404), last_coord=Coord(2, 2)))
        await host.workers.wait_for_complete()
        assert any("unit#404 destroyed" in line for line in _lines(host.panel))


async def test_enemy_movement_in_remembered_only_cells_does_not_log() -> None:
    """Regression: a LIVE enemy move is observable only where the viewer can
    CURRENTLY see — NOT in cells it merely remembers (stale terrain). The log
    used to leak enemy moves through fogged-but-remembered areas."""
    from empire.core.map import RememberedTile

    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="Enemy", is_ai=True, view=ViewMap())
    real_map = _land_map(4, 4)
    real_map.place_unit(Army(UnitId(99), enemy, Coord(3, 3)), Coord(3, 3))
    # Both endpoints remembered (seen once) but not currently visible.
    for c in (Coord(3, 3), Coord(2, 3)):
        me.view.remembered[c] = RememberedTile(
            coord=c, terrain=TerrainKind.LAND, remembered_at=1
        )
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(
            UnitMovedEvent(unit_id=UnitId(99), from_=Coord(3, 3), to=Coord(2, 3)),
        )
        await host.workers.wait_for_complete()
        assert not any("#99" in line for line in _lines(host.panel))


async def test_enemy_movement_into_visible_cell_logs() -> None:
    """An enemy arriving in a currently-visible cell IS observed."""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="Enemy", is_ai=True, view=ViewMap())
    real_map = _land_map(4, 4)
    real_map.place_unit(Army(UnitId(99), enemy, Coord(3, 3)), Coord(3, 3))
    me.view.visible.add(Coord(2, 3))  # the destination is in sight
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(
            UnitMovedEvent(unit_id=UnitId(99), from_=Coord(3, 3), to=Coord(2, 3)),
        )
        await host.workers.wait_for_complete()
        assert any("#99" in line for line in _lines(host.panel))


async def test_own_city_artillery_is_marked_yours() -> None:
    """Your own city's artillery reads as YOURS (green, 'your city#…'), so you
    can tell your guns firing from an enemy/neutral city shelling you."""
    me = Player(id=PlayerId(1), name="Me", is_ai=False, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="Foe", is_ai=True, view=ViewMap())
    mycity = City(id=CityId(1), coord=Coord(2, 2), owner=me)
    real_map = _land_map(6, 6, cities={Coord(2, 2): mycity})
    real_map.place_unit(Army(UnitId(99), enemy, Coord(3, 3)), Coord(3, 3))
    me.view.visible.add(Coord(3, 3))  # you can see the shellfall
    bus = EventBus()
    host = _LogHost()
    async with host.run_test():
        host.panel.attach_to(bus, real_map, me)
        bus.publish(
            CityFiredEvent(
                city_id=CityId(1), target_id=UnitId(99),
                target_coord=Coord(3, 3), hit=True, destroyed=True,
            )
        )
        await host.workers.wait_for_complete()
        lines = _lines(host.panel)
        assert any("your city#1" in line and "DESTROYS" in line for line in lines)

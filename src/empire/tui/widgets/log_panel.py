"""`LogPanel`: scrolling event log. Subscribes to `EventBus` and appends
human-readable lines for unit moves, captures, removals, and turn ticks.

Fog of war: a LIVE event is written only where the human can CURRENTLY see
(`view.visible`) — NOT in merely *remembered* cells, which are stale terrain you
can't actually watch. Own units always show (fog never hides your own actions
from yourself). Enemy production at an unseen city, an enemy unit walking across
fog (even fog you've previously scouted), etc. are all suppressed — otherwise
the log silently betrays information the player should have to scout for.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import RichLog

from empire.core.coord import Coord
from empire.core.events import (
    CityCapturedEvent,
    CityFiredEvent,
    GameEndedEvent,
    TurnAdvancedEvent,
    UnitDisbandedEvent,
    UnitMovedEvent,
    UnitPlacedEvent,
    UnitRemovedEvent,
)
from empire.core.map import Map
from empire.core.player import Player
from empire.events.bus import EventBus


class LogPanel(VerticalScroll):
    """A `RichLog`-backed event panel."""

    DEFAULT_CSS = """
    LogPanel {
        height: 8;
        border-top: solid $accent;
    }
    LogPanel > RichLog {
        background: $surface;
    }
    """

    # The log is read-only context for the player; it must not steal focus
    # from the screen, or screen-level letter bindings stop firing.
    can_focus = False

    def compose(self):  # type: ignore[no-untyped-def]
        # `can_focus=False` is critical: RichLog defaults to focusable, and
        # if it grabs focus from the screen, single-letter keypresses (e,
        # u, p, etc.) hit the log instead of the PlayScreen bindings.
        # `markup=True` so event lines can carry `[red]...[/red]` style
        # tags for color-coded ownership / conflict highlighting.
        log = RichLog(id="log", wrap=False, highlight=False, markup=True)
        log.can_focus = False
        yield log

    def attach_to(self, bus: EventBus, real_map: Map, viewer: Player) -> None:
        """Wire bus subscriptions that append to the log, filtered by `viewer`'s fog.

        Color scheme (Rich markup):
          - default            own routine events (your production / movement)
          - [red]...[/red]     enemy routine events (their production / movement)
          - [magenta]...[/]    conflict events (unit destroyed; enemy captures own city)
          - [green]...[/]      own gains (you capture an enemy/neutral city)
        """
        from empire.core.identity import UnitId

        log = self.query_one("#log", RichLog)

        # id -> unit-kind label ("army", "destroyer", ...). A unit's kind is
        # immutable, so once cached it's good forever — which lets the log
        # name a unit even in the events that fire after it leaves the map
        # (destroyed, disbanded, shelled). Primed from units present now;
        # topped up whenever a unit is observed (placed / moved / damaged).
        kind_by_id: dict[int, str] = {
            int(u.id): u.kind.value for u in real_map.all_units()
        }

        def remember(uid: int) -> None:
            if uid in kind_by_id:
                return
            unit = real_map.unit_by_id(UnitId(uid))
            if unit is not None:
                kind_by_id[uid] = unit.kind.value

        def label(uid: int) -> str:
            kind = kind_by_id.get(uid)
            return f"{kind}#{uid}" if kind is not None else f"unit#{uid}"

        def write(line: str) -> None:
            log.write(line)

        def visible_now(c: Coord) -> bool:
            # LIVE events (move / produce / destroy / capture / shellfall) are
            # only observable where the viewer can CURRENTLY see. `remembered`
            # cells are stale terrain you can't watch — including them (the old
            # `seen`) leaked enemy movement through fogged-but-remembered areas.
            return c in viewer.view.visible

        def own_unit(uid: int) -> bool:
            unit = real_map.unit_by_id(UnitId(uid))
            return unit is not None and unit.owner is viewer

        def on_turn(e: TurnAdvancedEvent) -> None:
            write(f"-- turn {e.turn} --")

        def on_placed(e: UnitPlacedEvent) -> None:
            remember(int(e.unit_id))
            mine = own_unit(int(e.unit_id))
            if not (mine or visible_now(e.at)):
                return
            line = f"  produced {label(int(e.unit_id))} at ({e.at.x},{e.at.y})"
            write(line if mine else f"[red]{line}[/red]")

        def on_moved(e: UnitMovedEvent) -> None:
            remember(int(e.unit_id))
            mine = own_unit(int(e.unit_id))
            if not (mine or visible_now(e.from_) or visible_now(e.to)):
                return
            line = (
                f"  {label(int(e.unit_id))} moved "
                f"({e.from_.x},{e.from_.y})->({e.to.x},{e.to.y})"
            )
            write(line if mine else f"[red]{line}[/red]")

        def on_removed(e: UnitRemovedEvent) -> None:
            # The unit is gone — we can't tell whose it was. Treat every
            # destruction as a conflict event (combat, capture failure).
            if not visible_now(e.last_coord):
                return
            write(
                f"  [magenta]{label(int(e.unit_id))} destroyed at "
                f"({e.last_coord.x},{e.last_coord.y})[/magenta]",
            )

        def on_disbanded(e: UnitDisbandedEvent) -> None:
            # Disband fires on the owner's own city cell, which the owner can
            # always see; fog-filter by location so enemy disbands at unseen
            # cities stay hidden. Yellow: a (usually self-inflicted) loss.
            if not visible_now(e.last_coord):
                return
            write(
                f"  [yellow]{label(int(e.unit_id))} disbanded (no city room) at "
                f"({e.last_coord.x},{e.last_coord.y})[/yellow]",
            )

        def on_captured(e: CityCapturedEvent) -> None:
            from empire.core.identity import CityId

            city = real_map.city_by_id(CityId(int(e.city_id)))
            is_own_now = city is not None and city.owner is viewer
            if not is_own_now and (city is None or not visible_now(city.coord)):
                return
            owner = "?" if e.new_owner_id is None else f"P#{int(e.new_owner_id)}"
            line = f"  city#{int(e.city_id)} captured by {owner}"
            if is_own_now:
                write(f"[green]{line}[/green]")  # we just gained it
            else:
                # An enemy-on-enemy or enemy-on-neutral capture in our view.
                # Either way it's a hostile development for us.
                write(f"[magenta]{line}[/magenta]")

        def on_city_fired(e: CityFiredEvent) -> None:
            # Attribution for artillery losses ("why did my army just die?").
            # Filter by the target's location: if you can see the shellfall,
            # you learn about the shot.
            if not visible_now(e.target_coord):
                return
            from empire.core.identity import CityId

            city = real_map.city_by_id(CityId(int(e.city_id)))
            mine = city is not None and city.owner is viewer
            where = (
                f"{'your ' if mine else ''}city#{int(e.city_id)} "
                f"({city.coord.x},{city.coord.y})"
                if city is not None
                else f"city#{int(e.city_id)}"
            )
            remember(int(e.target_id))  # damaging hits leave it on the map
            target = (
                f"{label(int(e.target_id))} at "
                f"({e.target_coord.x},{e.target_coord.y})"
            )
            # Your guns firing on a foe is good news (green); a hostile city
            # shelling you is a conflict event (magenta).
            color = "green" if mine else "magenta"
            if e.destroyed:
                write(f"  [{color}]{where} artillery DESTROYS {target}[/{color}]")
            elif e.hit:
                write(f"  [{color}]{where} artillery hits {target}[/{color}]")
            else:
                write(f"  {where} artillery fires at {target} — miss")

        def on_ended(e: GameEndedEvent) -> None:
            winner = "?" if e.winner_id is None else f"P#{int(e.winner_id)}"
            is_us = e.winner_id is not None and e.winner_id == viewer.id
            line = f"=== game over at turn {e.final_turn}; winner={winner} ==="
            write(f"[green]{line}[/green]" if is_us else f"[magenta]{line}[/magenta]")

        bus.subscribe(TurnAdvancedEvent, on_turn)
        bus.subscribe(UnitPlacedEvent, on_placed)
        bus.subscribe(UnitMovedEvent, on_moved)
        bus.subscribe(UnitRemovedEvent, on_removed)
        bus.subscribe(UnitDisbandedEvent, on_disbanded)
        bus.subscribe(CityCapturedEvent, on_captured)
        bus.subscribe(CityFiredEvent, on_city_fired)
        bus.subscribe(GameEndedEvent, on_ended)

"""`TaskForceLedger` canaries: factual event lines per task force.

Synthetic events through the REAL core event types, asserting exact ledger
lines: loss attribution by task-force membership, artillery cause
bookkeeping, delivery lines in the general (UNASSIGNED) section, refusal
replay, arrival/capture attribution, epoch reset — plus one end-to-end
test driving a real `Game` turn onto the ledger over a live bus.
"""

from __future__ import annotations

from dataclasses import replace

from empire.ai.general.ledger import LedgerReport, TaskForceLedger
from empire.ai.general.registry import TaskForceRegistry
from empire.combat.resolver import CombatResolver
from empire.contracts.controller import NullController
from empire.contracts.doctrine import Objective, Refusal, TaskForce, Verb
from empire.contracts.turn_plan import TurnPlan, UnitMove
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.events import (
    CityCapturedEvent,
    CityFiredEvent,
    UnitDisbandedEvent,
    UnitMovedEvent,
    UnitPlacedEvent,
    UnitRemovedEvent,
)
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.events.bus import EventBus
from tests.empire.support import build_map

# --- helpers -----------------------------------------------------------------


def _ids(*ns: int) -> frozenset[UnitId]:
    return frozenset(UnitId(n) for n in ns)


_TARGET = Coord(11, 1)


def _tf(
    tf_id: str = "1",
    members: frozenset[UnitId] | None = None,
    verb: Verb = Verb.CAPTURE,
    target: Coord = _TARGET,
) -> TaskForce:
    return TaskForce(
        tf_id=tf_id,
        members=members if members is not None else _ids(3, 4, 5, 7),
        objective=Objective(verb, target),
        why="as ordered",
        formed_turn=38,
    )


def _ledger(
    registry: TaskForceRegistry,
    own_kinds: dict[int, str] | None = None,
    cities: dict[int, Coord] | None = None,
) -> TaskForceLedger:
    """A ledger for player 1 with dict-backed oracles. `own_kinds` mimics
    the live-map lookup: present = alive and ours, absent = enemy or dead."""
    kinds = own_kinds if own_kinds is not None else {}
    coords = cities if cities is not None else {}
    return TaskForceLedger(
        player_id=PlayerId(1),
        registry=lambda: registry,
        own_unit_kind=lambda uid: kinds.get(int(uid)),
        city_coord=lambda cid: coords.get(int(cid)),
    )


# --- loss attribution ----------------------------------------------------------


def test_combat_losses_attribute_to_the_member_task_force_grouped_by_cell() -> None:
    """Two members dead at the assault cell -> ONE grouped factual line under
    their TF; a loss elsewhere stays its own line."""
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)))
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(5), last_coord=Coord(11, 1)))
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(7), last_coord=Coord(11, 1)))
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(3), last_coord=Coord(9, 4)))
    report = ledger.collect()
    assert report.by_task_force == {
        "1": ("lost #5, #7 at (11,1)", "lost #3 at (9,4)"),
    }
    assert report.general == ()


def test_unclaimable_unit_loss_is_not_booked() -> None:
    """A removed unit in no TF and never known to be ours (an enemy's loss)
    is not our fact: nothing is booked anywhere."""
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)))
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(99), last_coord=Coord(2, 2)))
    assert ledger.collect() == LedgerReport(by_task_force={}, general=())


def test_own_unassigned_loss_lands_in_the_general_section() -> None:
    """A unit delivered earlier (so the ledger learned it is ours) and lost
    while UNASSIGNED is booked in the general section — even though the
    live-map oracle can no longer answer for a dead unit."""
    own_kinds = {16: "transport"}
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)), own_kinds)
    ledger.record_unit_placed(UnitPlacedEvent(unit_id=UnitId(16), at=Coord(1, 2)))
    del own_kinds[16]  # dead: the live-map oracle now knows nothing of #16
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(16), last_coord=Coord(4, 2)))
    report = ledger.collect()
    assert report.general == (
        "transport #16 delivered at (1,2)",
        "lost #16 at (4,2)",
    )
    assert report.by_task_force == {}


# --- deliveries ------------------------------------------------------------------


def test_delivery_lines_book_own_production_only() -> None:
    """Our production makes a general-section delivery line with the unit's
    kind; enemy production (oracle answers None) is never booked."""
    ledger = _ledger(TaskForceRegistry(), own_kinds={16: "transport"})
    ledger.record_unit_placed(UnitPlacedEvent(unit_id=UnitId(16), at=Coord(1, 2)))
    ledger.record_unit_placed(UnitPlacedEvent(unit_id=UnitId(17), at=Coord(8, 8)))
    assert ledger.collect().general == ("transport #16 delivered at (1,2)",)


# --- city artillery ----------------------------------------------------------------


def test_artillery_kill_books_the_stated_cause_and_swallows_the_duplicate_removal() -> None:
    """`CityFiredEvent(destroyed=True)` is the one event that states WHY a
    unit died, so the loss line carries the cause — and the engine's
    follow-up `UnitRemovedEvent` for the same unit adds no second line."""
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)))
    ledger.record_city_fired(
        CityFiredEvent(
            city_id=CityId(9),
            target_id=UnitId(5),
            target_coord=Coord(10, 1),
            hit=True,
            destroyed=True,
        )
    )
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(5), last_coord=Coord(10, 1)))
    assert ledger.collect().by_task_force == {
        "1": ("lost #5 to city artillery at (10,1)",),
    }


def test_artillery_hit_and_miss_on_a_member() -> None:
    """A damaging hit is a state change -> one line; a miss changes nothing
    about the force and is not booked."""
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)))
    ledger.record_city_fired(
        CityFiredEvent(
            city_id=CityId(9),
            target_id=UnitId(4),
            target_coord=Coord(10, 2),
            hit=True,
            destroyed=False,
        )
    )
    ledger.record_city_fired(
        CityFiredEvent(
            city_id=CityId(9),
            target_id=UnitId(4),
            target_coord=Coord(10, 2),
            hit=False,
            destroyed=False,
        )
    )
    assert ledger.collect().by_task_force == {
        "1": ("#4 hit by city artillery at (10,2)",),
    }


def test_artillery_against_enemy_units_is_not_booked() -> None:
    """Our own city shelling the enemy is the ENEMY's ledger fact, not ours:
    the target is in no TF of ours and the own-unit oracle answers None."""
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)))
    ledger.record_city_fired(
        CityFiredEvent(
            city_id=CityId(1),
            target_id=UnitId(50),
            target_coord=Coord(3, 3),
            hit=True,
            destroyed=True,
        )
    )
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(50), last_coord=Coord(3, 3)))
    assert ledger.collect() == LedgerReport(by_task_force={}, general=())


# --- captures ------------------------------------------------------------------------


def test_capture_attributes_to_the_force_targeting_that_city() -> None:
    """Our capture books under every TF whose objective targets the city;
    an untargeted capture books in the general section; an enemy capture
    is not booked at all."""
    registry = TaskForceRegistry(forces=(_tf(target=Coord(11, 1)),))
    cities = {2: Coord(11, 1), 3: Coord(0, 5), 4: Coord(7, 7)}
    ledger = _ledger(registry, cities=cities)
    ledger.record_city_captured(
        CityCapturedEvent(city_id=CityId(2), new_owner_id=PlayerId(1), previous_owner_id=None)
    )
    ledger.record_city_captured(
        CityCapturedEvent(city_id=CityId(3), new_owner_id=PlayerId(1), previous_owner_id=None)
    )
    ledger.record_city_captured(
        CityCapturedEvent(city_id=CityId(4), new_owner_id=PlayerId(2), previous_owner_id=None)
    )
    report = ledger.collect()
    assert report.by_task_force == {"1": ("captured (11,1) — now ours",)}
    assert report.general == ("captured (0,5) — now ours",)


# --- arrivals ---------------------------------------------------------------------


def test_arrival_at_the_objective_target_books_once_per_unit() -> None:
    """A member reaching its force's STAGE target is an arrival line; the
    same unit re-crossing the cell adds nothing, and ordinary moves are
    never booked (the board snapshot already shows position)."""
    registry = TaskForceRegistry(
        forces=(_tf(members=_ids(3, 4), verb=Verb.STAGE, target=Coord(5, 2)),)
    )
    ledger = _ledger(registry)
    ledger.record_unit_moved(UnitMovedEvent(unit_id=UnitId(3), from_=Coord(4, 2), to=Coord(5, 2)))
    ledger.record_unit_moved(UnitMovedEvent(unit_id=UnitId(4), from_=Coord(5, 1), to=Coord(5, 2)))
    ledger.record_unit_moved(UnitMovedEvent(unit_id=UnitId(3), from_=Coord(5, 2), to=Coord(6, 2)))
    ledger.record_unit_moved(UnitMovedEvent(unit_id=UnitId(3), from_=Coord(6, 2), to=Coord(5, 2)))
    assert ledger.collect().by_task_force == {"1": ("#3, #4 arrived at (5,2)",)}


# --- disbands ---------------------------------------------------------------------


def test_member_disband_books_under_its_force() -> None:
    """A disband is booked as the plain fact it is — the event does not say
    whether it was capture-time or support-limit, so neither does the line."""
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)))
    ledger.record_unit_disbanded(UnitDisbandedEvent(unit_id=UnitId(7), last_coord=Coord(11, 1)))
    assert ledger.collect().by_task_force == {"1": ("#7 disbanded at (11,1)",)}


# --- refusal replay ----------------------------------------------------------------


def test_refusals_replay_under_the_addressed_standing_force() -> None:
    """A refusal naming a standing TF replays under that TF; one naming no
    standing force (refused FORM, unparseable text) goes to the general
    section. Reasons are replayed verbatim — the cannot-comply channel."""
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)))
    ledger.note_refusals(
        (
            Refusal(order_text="TF 1: REINFORCE UNITS #9", reason="no such unit: #9"),
            Refusal(
                order_text="FORM TF 4: UNITS #8 | CAPTURE (2, 2)",
                reason="already assigned to another task force: #8",
            ),
        )
    )
    report = ledger.collect()
    assert report.by_task_force == {
        "1": ("order refused: TF 1: REINFORCE UNITS #9 — no such unit: #9",),
    }
    assert report.general == (
        "order refused: FORM TF 4: UNITS #8 | CAPTURE (2, 2)"
        " — already assigned to another task force: #8",
    )


# --- epoch reset -------------------------------------------------------------------


def test_reset_drops_booked_lines_but_keeps_ownership_knowledge() -> None:
    """`reset` opens a clean epoch (no lines carry over), yet the ledger
    still knows which units are ours: a post-reset loss of a previously
    delivered UNASSIGNED unit is booked."""
    ledger = _ledger(TaskForceRegistry(forces=(_tf(),)), own_kinds={16: "army"})
    ledger.record_unit_placed(UnitPlacedEvent(unit_id=UnitId(16), at=Coord(1, 2)))
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(5), last_coord=Coord(11, 1)))
    ledger.reset()
    assert ledger.collect() == LedgerReport(by_task_force={}, general=())
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(16), last_coord=Coord(2, 2)))
    assert ledger.collect().general == ("lost #16 at (2,2)",)


# --- record-time attribution ---------------------------------------------------------


def test_attribution_happens_at_record_time_not_collect_time() -> None:
    """After combat the engine prunes dead members off their roster; the
    loss line must survive because the ledger attributed when the event
    fired, while #5 was still on TF-1's books."""
    registry_holder = [TaskForceRegistry(forces=(_tf(),))]
    ledger = TaskForceLedger(
        player_id=PlayerId(1),
        registry=lambda: registry_holder[0],
        own_unit_kind=lambda _uid: None,
        city_coord=lambda _cid: None,
    )
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(5), last_coord=Coord(11, 1)))
    registry_holder[0] = registry_holder[0].prune(living_unit_ids=_ids(3, 4, 7))
    assert ledger.collect().by_task_force == {"1": ("lost #5 at (11,1)",)}


# --- end-to-end over a real Game ------------------------------------------------------


class _Scripted:
    """Returns the prepared TurnPlan once; empty plans after."""

    def __init__(self, prepared: TurnPlan) -> None:
        self._plan: TurnPlan | None = prepared

    def name(self) -> str:
        return "Scripted"

    def plan_turn(self, view: object) -> TurnPlan:
        del view
        plan, self._plan = self._plan, None
        return plan if plan is not None else TurnPlan()

    def revise_move(self, unit_id: UnitId, surprise: object, view: object) -> UnitMove:
        del surprise, view
        return UnitMove(unit_id=unit_id)


def test_real_game_turn_feeds_the_ledger_over_a_live_bus() -> None:
    """Drive one deterministic capture turn through a real `Game` + real
    `EventBus`: the TF's army takes the neutral city, and the ledger books
    the capture-time disband and the capture from real engine events."""
    from empire.core.unit import Army

    p1 = Player(id=PlayerId(1), name="P1", is_ai=True, view=ViewMap())
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    target = City(id=CityId(2), coord=Coord(2, 0), owner=None)
    home = City(id=CityId(1), coord=Coord(0, 0), owner=p2)  # keeps the war alive
    m = build_map(["LLC"], cities={Coord(0, 0): home, Coord(2, 0): target})
    army = Army(UnitId(1), p1, Coord(1, 0))
    m.place_unit(army, Coord(1, 0))

    registry = TaskForceRegistry(
        forces=(
            TaskForce(
                tf_id="1",
                members=_ids(1),
                objective=Objective(Verb.CAPTURE, Coord(2, 0)),
                why="take the far shore",
                formed_turn=1,
            ),
        )
    )
    bus = EventBus()
    ledger = TaskForceLedger(
        player_id=p1.id,
        registry=lambda: registry,
        own_unit_kind=lambda uid: (
            u.kind.value
            if (u := m.unit_by_id(uid)) is not None and u.owner is p1
            else None
        ),
        city_coord=lambda cid: (c.coord if (c := m.city_by_id(cid)) is not None else None),
    )
    ledger.attach(bus)

    rules = replace(STANDARD, army_capture_city_deterministic=True)
    game = Game(
        rules=rules,
        real_map=m,
        players=[p1, p2],
        seed=0,
        combat_resolver=CombatResolver(),
        event_bus=bus,
    )
    game.attach_controller(p1.id, _Scripted(TurnPlan(moves=(UnitMove(unit_id=UnitId(1), path=((2, 0),)),))))
    game.attach_controller(p2.id, NullController())
    game.run_turn()

    assert target.owner is p1
    report = ledger.collect()
    assert report.by_task_force == {
        "1": ("#1 disbanded at (2,0)", "captured (2,0) — now ours"),
    }
    assert report.general == ()

"""`LlmGeneralController` behavior with scripted clients: epoch cadence,
doctrine-steered movement merged with the executor's complement, BUILD
directive overrides, and — the non-negotiable competence floor — EVERY
failure mode degrading to a plan equal to a control `PortfolioAI`'s."""

from __future__ import annotations

import pytest

from empire.ai.general.client import ChatAnswer
from empire.ai.general.compiler import DoctrineCompiler, TaskForceView
from empire.ai.general.controller import LlmGeneralController
from empire.ai.general.factory import build_general
from empire.ai.general.ledger import TaskForceLedger
from empire.ai.search.portfolio import PortfolioAI
from empire.config import AppConfig, LlmConnection
from empire.contracts.surprise import BlockedBy, PathBlocked
from empire.contracts.turn_plan import ProductionOrder, TurnPlan, UnitMove
from empire.contracts.world_view import WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.events import UnitPlacedEvent, UnitRemovedEvent
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, UnitKind

CAPTURE_TARGET = Coord(11, 2)
OWN_CITY = Coord(0, 3)

# One valid epoch answer for the staged board's opening (no standing TFs):
# task #1 and #2 east, leave #3 to the staff, and direct the city's build.
FORM_ANSWER = (
    "FORM TF 1: UNITS #1 #2 | CAPTURE (11,2) | strike the eastern city\n"
    "BUILD (0,3): FIGHTER | air cover for the push"
)
CONTINUE_ANSWER = "TF 1: CONTINUE | keep pressing east"
# Plan-first: a PLAN line ahead of the same orders (the commander's plan).
PLAN = "Strike east and take (11,2); this plan ends when the enemy city falls."
PLAN_FORM_ANSWER = f"PLAN: {PLAN}\n{FORM_ANSWER}"


# ---- staged board (the compiler test's shape) --------------------------------------


def _flat_game(width: int = 12, height: int = 6) -> tuple[Game, Player, Player]:
    p1 = Player(id=PlayerId(1), name="P1", is_ai=True, view=ViewMap(), color="red")
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap(), color="blue")
    tiles: dict[Coord, Tile] = {
        Coord(x, y): Tile(coord=Coord(x, y), terrain=TerrainKind.LAND)
        for x in range(width)
        for y in range(height)
    }
    real_map = Map(width=width, height=height, tiles=tiles)
    game = Game(rules=STANDARD, real_map=real_map, players=[p1, p2], seed=1)
    return game, p1, p2


def _add_city(game: Game, owner: Player | None, at: Coord, city_id: int) -> City:
    city = City(id=CityId(city_id), coord=at, owner=owner)
    game.map._tiles[at] = Tile(  # pyright: ignore[reportPrivateUsage]
        coord=at, terrain=TerrainKind.CITY, city=city
    )
    return city


def _staged_board() -> tuple[Game, Player]:
    """Flat land, all seen: our city west, an enemy city east, armies #1/#2
    west (the doctrine's strike force) and #3 mid-board (the staff's)."""
    game, p1, p2 = _flat_game()
    p1.view.visible = {
        Coord(x, y) for x in range(game.map.width) for y in range(game.map.height)
    }
    _add_city(game, p1, OWN_CITY, 1)
    _add_city(game, p2, CAPTURE_TARGET, 2)
    for unit_id, at in ((1, Coord(1, 2)), (2, Coord(2, 2)), (3, Coord(6, 2))):
        game.map.place_unit(Army(UnitId(unit_id), p1, at), at)
    return game, p1


def _view(game: Game, player: Player, turn: int) -> WorldView:
    game.turn = turn
    return WorldView(real_map=game.map, player=player, turn=turn, rules=game.rules)


# ---- scripted clients ----------------------------------------------------------------


class _ScriptedClient:
    """Answers per epoch, in order; the last answer repeats. Records calls."""

    def __init__(self, answers: list[str], finish_reason: str = "stop") -> None:
        self._answers = answers
        self._finish = finish_reason
        self.calls = 0
        self.seeds: list[int] = []

    def complete(self, prompt: str, *, seed: int, system: str | None = None) -> ChatAnswer:
        del prompt
        text = self._answers[min(self.calls, len(self._answers) - 1)]
        self.calls += 1
        self.seeds.append(seed)
        return ChatAnswer(text=text, finish_reason=self._finish, attempts=1, model="stub")


class _RaisingClient:
    """Every call raises the given exception. Records calls."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    def complete(self, prompt: str, *, seed: int, system: str | None = None) -> ChatAnswer:
        del prompt, seed
        self.calls += 1
        raise self._error


# ---- epoch cadence -------------------------------------------------------------------


def test_epoch_cadence_first_turn_then_every_n() -> None:
    game, p1 = _staged_board()
    client = _ScriptedClient([FORM_ANSWER, CONTINUE_ANSWER])
    controller = LlmGeneralController(client=client, cadence=3)
    for turn in range(1, 8):
        controller.plan_turn(_view(game, p1, turn))
    # Epochs at t1 (first plan is always an epoch), t4, t7 — nowhere else.
    assert client.calls == 3


def test_epoch_seed_is_deterministic_per_turn() -> None:
    game, p1 = _staged_board()
    client = _ScriptedClient([FORM_ANSWER, CONTINUE_ANSWER])
    controller = LlmGeneralController(client=client, cadence=3, seed=100)
    for turn in range(1, 5):
        controller.plan_turn(_view(game, p1, turn))
    assert client.seeds == [101, 104]  # seed + turn, one per epoch


# ---- doctrine steering + the executor's complement -------------------------------------


def test_doctrine_moves_for_tasked_units_executor_for_complement() -> None:
    """The merged plan is exactly: the compiler's moves for TF members plus
    a control PortfolioAI's moves for the complement-scoped view."""
    game, p1 = _staged_board()
    controller = LlmGeneralController(client=_ScriptedClient([FORM_ANSWER]))
    view = _view(game, p1, 1)
    plan = controller.plan_turn(view)
    assert controller.last_failure is None

    tf = controller.registry.get("1")
    assert tf is not None
    assert tf.members == frozenset({UnitId(1), UnitId(2)})

    # Tasked movement == what the compiler alone emits for this registry.
    doctrine_plan = DoctrineCompiler().plan_moves(controller.registry, view)
    tasked = {m.unit_id: m for m in plan.moves if m.unit_id in tf.members}
    assert tasked == {m.unit_id: m for m in doctrine_plan.moves}
    # ...and it actually advances the strike force east toward the target.
    starts = {u.id: u.coord.x for u in view.own_units}
    for move in tasked.values():
        assert move.path[0][0] > starts[move.unit_id]

    # Complement movement == a control executor planning the scoped view.
    control = PortfolioAI().plan_turn(TaskForceView(view, frozenset({UnitId(3)})))
    complement = [m for m in plan.moves if m.unit_id == UnitId(3)]
    assert complement == [m for m in control.moves if m.unit_id == UnitId(3)]


def test_build_directive_overrides_that_city_and_persists() -> None:
    """BUILD (0,3): FIGHTER overrides the executor's production for that
    city — this epoch AND on later non-epoch turns (standing directive)."""
    game, p1 = _staged_board()
    controller = LlmGeneralController(client=_ScriptedClient([FORM_ANSWER]), cadence=8)
    for turn in (1, 2, 3):
        plan = controller.plan_turn(_view(game, p1, turn))
        orders = [o for o in plan.production_orders if o.city_id == CityId(1)]
        assert orders == [ProductionOrder(city_id=CityId(1), target=UnitKind.FIGHTER)]


# ---- the competence floor: every failure degrades to pure PortfolioAI -------------------


def _degradation_clients() -> list[tuple[str, object]]:
    return [
        ("client raises typed unavailable", _RaisingClient(RuntimeError("server down"))),
        ("client raises unexpected", _RaisingClient(ZeroDivisionError("boom"))),
        ("answer not delivered", _ScriptedClient(["half an ans"], finish_reason="length")),
        ("nothing validates", _ScriptedClient(["I think we should attack the east!"])),
    ]


@pytest.mark.parametrize(
    ("label", "client"),
    _degradation_clients(),
    ids=[label for label, _ in _degradation_clients()],
)
def test_every_failure_mode_degrades_to_pure_portfolio(label: str, client: object) -> None:
    """The degraded turn's plan EQUALS a control PortfolioAI's plan for the
    same board — the general can lower nothing below the executor floor."""
    del label
    game, p1 = _staged_board()
    controller = LlmGeneralController(client=client)  # type: ignore[arg-type]
    view = _view(game, p1, 1)
    plan = controller.plan_turn(view)
    control = PortfolioAI().plan_turn(_view(game, p1, 1))
    assert plan == control
    assert controller.last_failure is not None
    assert controller.failure_log  # recorded for the TUI


def test_epoch_internal_exception_degrades_to_pure_portfolio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a bug INSIDE the epoch path (validator explodes) cannot leak: the
    turn degrades to the executor and the reason is recorded."""
    game, p1 = _staged_board()
    controller = LlmGeneralController(client=_ScriptedClient([FORM_ANSWER]))

    def _explode(text: str, context: object) -> object:
        raise RuntimeError("validator bug")

    monkeypatch.setattr(controller._validator, "validate", _explode)  # pyright: ignore[reportPrivateUsage]
    plan = controller.plan_turn(_view(game, p1, 1))
    control = PortfolioAI().plan_turn(_view(game, p1, 1))
    assert plan == control
    assert controller.last_failure is not None
    assert "validator bug" in controller.last_failure


def test_failed_epoch_leaves_registry_untouched_and_records_refusals() -> None:
    game, p1 = _staged_board()
    controller = LlmGeneralController(client=_ScriptedClient(["gibberish, not orders"]))
    controller.plan_turn(_view(game, p1, 1))
    assert controller.registry.forces == ()
    assert controller.last_refusals  # the cannot-comply channel, kept


def test_failure_retries_next_epoch_not_next_turn() -> None:
    game, p1 = _staged_board()
    client = _RaisingClient(RuntimeError("down"))
    controller = LlmGeneralController(client=client, cadence=4)
    controller.plan_turn(_view(game, p1, 1))
    assert client.calls == 1
    for turn in (2, 3, 4):  # inside the failed epoch's window: no calls
        controller.plan_turn(_view(game, p1, turn))
    assert client.calls == 1
    controller.plan_turn(_view(game, p1, 5))  # next epoch: try again
    assert client.calls == 2


def test_recovery_after_failed_epoch() -> None:
    """A failed first epoch does not poison the next: the retry applies its
    doctrine normally."""
    game, p1 = _staged_board()

    class _FailsOnce:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str, *, seed: int, system: str | None = None) -> ChatAnswer:
            del prompt, seed
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("down")
            return ChatAnswer(
                text=FORM_ANSWER, finish_reason="stop", attempts=1, model="stub"
            )

    controller = LlmGeneralController(client=_FailsOnce(), cadence=2)
    controller.plan_turn(_view(game, p1, 1))
    assert controller.last_failure is not None
    controller.plan_turn(_view(game, p1, 3))
    assert controller.last_failure is None
    assert controller.registry.get("1") is not None


# ---- revise_move ------------------------------------------------------------------------


def test_revise_move_stays_total_for_tasked_and_untasked_units() -> None:
    game, p1 = _staged_board()
    controller = LlmGeneralController(client=_ScriptedClient([FORM_ANSWER]))
    view = _view(game, p1, 1)
    controller.plan_turn(view)
    surprise = PathBlocked(blocked_at=Coord(3, 2), by=BlockedBy.OWN_UNIT)
    for unit_id in (UnitId(1), UnitId(3)):
        revised = controller.revise_move(unit_id, surprise, view)
        assert isinstance(revised, UnitMove)
        assert revised.unit_id == unit_id


def test_revise_move_failure_means_stay_put(monkeypatch: pytest.MonkeyPatch) -> None:
    game, p1 = _staged_board()
    controller = LlmGeneralController(client=_ScriptedClient([FORM_ANSWER]))
    view = _view(game, p1, 1)
    controller.plan_turn(view)

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("follower bug")

    for follower, _members in controller._scoped.values():  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(follower, "revise_move", _explode)
    surprise = PathBlocked(blocked_at=Coord(3, 2), by=BlockedBy.OWN_UNIT)
    revised = controller.revise_move(UnitId(1), surprise, view)
    assert revised == UnitMove(unit_id=UnitId(1))  # stay put, never crash


# ---- attrition bookkeeping ---------------------------------------------------------------


def test_dead_members_are_pruned_before_planning() -> None:
    """A tasked unit that died leaves its TF between turns; a TF with no
    survivors dissolves — the engine bookkeeping the general never does."""
    game, p1 = _staged_board()
    controller = LlmGeneralController(client=_ScriptedClient([FORM_ANSWER]), cadence=8)
    controller.plan_turn(_view(game, p1, 1))
    unit = game.map.unit_by_id(UnitId(1))
    assert unit is not None
    game.map.remove_unit(unit)
    controller.plan_turn(_view(game, p1, 2))
    tf = controller.registry.get("1")
    assert tf is not None
    assert tf.members == frozenset({UnitId(2)})


# ---- FLEET DISPATCHES: the ledger's general section reaches the briefing --------------------


class _RecordingClient:
    """Records the exact prompt each epoch, returns answers in order."""

    def __init__(self, answers: list[str]) -> None:
        self._answers = answers
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, prompt: str, *, seed: int, system: str | None = None) -> ChatAnswer:
        del seed
        self.prompts.append(prompt)
        text = self._answers[min(self.calls, len(self._answers) - 1)]
        self.calls += 1
        return ChatAnswer(text=text, finish_reason="stop", attempts=1, model="stub")


def test_unassigned_loss_surfaces_in_next_epochs_fleet_dispatches() -> None:
    """The playtest bug end-to-end: a transport that drifted UNASSIGNED and
    was sunk is booked in the ledger's general section; the NEXT epoch's
    briefing shows it as a turn-stamped `lost` line under FLEET DISPATCHES —
    the section the controller previously never rendered (every briefing said
    'since: (nothing reported)' and the general was blind to the loss)."""
    game, p1 = _staged_board()
    client = _RecordingClient([FORM_ANSWER, CONTINUE_ANSWER])
    controller = LlmGeneralController(client=client, cadence=4)

    own_kinds = {16: "transport"}
    clock = [1]
    ledger = TaskForceLedger(
        player_id=p1.id,
        registry=lambda: controller.registry,
        own_unit_kind=lambda uid: own_kinds.get(int(uid)),
        city_coord=lambda cid: None,
        now_turn=lambda: clock[0],
    )
    controller.attach_ledger(ledger)

    # Epoch 1 (t1): nothing booked yet, so no FLEET DISPATCHES block.
    controller.plan_turn(_view(game, p1, 1))
    assert "FLEET DISPATCHES" not in client.prompts[0]

    # Between epochs the general builds a transport that drifts UNASSIGNED and
    # the human sinks it — booked into the ledger's general section at t3.
    clock[0] = 3
    ledger.record_unit_placed(UnitPlacedEvent(unit_id=UnitId(16), at=Coord(0, 3)))
    del own_kinds[16]  # sunk: the live-map oracle no longer answers for #16
    ledger.record_unit_removed(
        UnitRemovedEvent(unit_id=UnitId(16), last_coord=Coord(3, 3))
    )

    # Epoch 2 (t5): the loss reaches the general under FLEET DISPATCHES.
    controller.plan_turn(_view(game, p1, 5))
    assert "FLEET DISPATCHES" in client.prompts[1]
    assert "t3: transport #16 produced at (0,3)" in client.prompts[1]
    assert "t3: lost #16 at (3,3)" in client.prompts[1]


def test_dissolved_task_force_loss_surfaces_in_fleet_dispatches() -> None:
    """The playtest bug: a single-unit scout TF whose only member is lost. The
    loss is booked to that TF at death time (the unit still belonged to it),
    then the empty TF is pruned out of the registry on the next turn. Because
    CURRENT TASKINGS renders a `since:` block only for LIVE forces, the
    dissolved TF's booked line reaches no section and the unit leaves the board
    with NO report anywhere. The fix routes such orphaned per-TF lines into
    FLEET DISPATCHES, attributed `TF-<id> (dissolved): <line>`."""
    game, p1 = _staged_board()
    scout_form = "FORM TF 7: UNITS #3 | SCOUT (10,2) | scout the eastern approach"
    client = _RecordingClient([scout_form, CONTINUE_ANSWER])
    controller = LlmGeneralController(client=client, cadence=4)

    own_kinds = {3: "army"}
    clock = [1]
    ledger = TaskForceLedger(
        player_id=p1.id,
        registry=lambda: controller.registry,
        own_unit_kind=lambda uid: own_kinds.get(int(uid)),
        city_coord=lambda cid: None,
        now_turn=lambda: clock[0],
    )
    controller.attach_ledger(ledger)

    # Epoch 1 (t1): forms TF-7 around the lone scout #3.
    controller.plan_turn(_view(game, p1, 1))
    assert controller.registry.get("7") is not None

    # Between epochs the scout is killed at t3 (booked to TF-7 while it still
    # belonged), then removed from the board so the next prune dissolves TF-7.
    clock[0] = 3
    del own_kinds[3]  # sunk: the live-map oracle no longer answers for #3
    ledger.record_unit_removed(UnitRemovedEvent(unit_id=UnitId(3), last_coord=Coord(9, 2)))
    unit = game.map.unit_by_id(UnitId(3))
    assert unit is not None
    game.map.remove_unit(unit)

    # Epoch 2 (t5): TF-7 is gone from the registry; its loss must still be
    # reported — folded into FLEET DISPATCHES, attributed to the dissolved TF.
    controller.plan_turn(_view(game, p1, 5))
    briefing = client.prompts[1]
    assert controller.registry.get("7") is None
    assert "FLEET DISPATCHES" in briefing
    assert "TF-7 (dissolved): t3: lost #3 at (9,2)" in briefing


# ---- COMMANDER'S PLAN: the plan persists and replays across epochs ----------------------


def test_plan_from_epoch_one_replays_in_epoch_two_briefing() -> None:
    """The commander's PLAN line is captured in epoch 1 and replayed, turn-
    stamped, in the next briefing's COMMANDER'S PLAN block — the first briefing
    (no prior plan) omits it."""
    game, p1 = _staged_board()
    client = _RecordingClient([PLAN_FORM_ANSWER, CONTINUE_ANSWER])
    controller = LlmGeneralController(client=client, cadence=4)

    controller.plan_turn(_view(game, p1, 1))  # epoch 1
    assert "COMMANDER'S PLAN" not in client.prompts[0]

    controller.plan_turn(_view(game, p1, 5))  # epoch 2
    assert f"COMMANDER'S PLAN (as of t1): {PLAN}" in client.prompts[1]
    assert "Evaluate your plan against the current situation" in client.prompts[1]


def test_plan_survives_a_failed_epoch() -> None:
    """A failed (undelivered) epoch keeps the previously stored plan; it still
    replays in the next successful epoch's briefing."""
    game, p1 = _staged_board()

    class _PlanThenFailThenContinue:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.calls = 0

        def complete(self, prompt: str, *, seed: int, system: str | None = None) -> ChatAnswer:
            del seed
            self.prompts.append(prompt)
            self.calls += 1
            if self.calls == 1:
                return ChatAnswer(
                    text=PLAN_FORM_ANSWER, finish_reason="stop", attempts=1, model="stub"
                )
            if self.calls == 2:  # undelivered: the epoch fails
                return ChatAnswer(
                    text="half an ans", finish_reason="length", attempts=1, model="stub"
                )
            return ChatAnswer(
                text=CONTINUE_ANSWER, finish_reason="stop", attempts=1, model="stub"
            )

    client = _PlanThenFailThenContinue()
    controller = LlmGeneralController(client=client, cadence=4)
    controller.plan_turn(_view(game, p1, 1))  # epoch 1: stores the plan
    controller.plan_turn(_view(game, p1, 5))  # epoch 2: fails, plan retained
    controller.plan_turn(_view(game, p1, 9))  # epoch 3: plan still replayed
    assert f"COMMANDER'S PLAN (as of t1): {PLAN}" in client.prompts[2]


# ---- the factory (build_general) -----------------------------------------------------------


def test_disabled_config_yields_plain_portfolio_and_never_touches_a_client() -> None:
    for config in (
        AppConfig(),  # absent/default
        AppConfig(llm=LlmConnection(enabled=False, base_url="http://x/v1")),
        AppConfig(llm=LlmConnection(enabled=True, base_url="")),  # unconfigured
    ):
        opponent = build_general(config)
        assert isinstance(opponent, PortfolioAI)
        assert not isinstance(opponent, LlmGeneralController)


def test_enabled_config_yields_the_general() -> None:
    config = AppConfig(llm=LlmConnection(enabled=True, base_url="http://127.0.0.1:1/v1"))
    opponent = build_general(config)
    assert isinstance(opponent, LlmGeneralController)


def test_disabled_general_plan_is_byte_identical_to_portfolio() -> None:
    """The default experience: whatever build_general returns for a disabled
    config plans EXACTLY like today's PortfolioAI."""
    game, p1 = _staged_board()
    plan = build_general(AppConfig()).plan_turn(_view(game, p1, 1))
    control = PortfolioAI().plan_turn(_view(game, p1, 1))
    assert plan == control
    assert isinstance(plan, TurnPlan)

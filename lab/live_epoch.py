"""Integration handshake (b): one LIVE epoch through the full production path.

Stages a real `Game` shaped like the lab's LIFT scenario (board_A2: a staged
strike force whose awaited second transport has just arrived), renders the
briefing with the real `BriefingRenderer`, sends primer + briefing to an
OpenAI-compatible endpoint, and pushes the answer through `DoctrineValidator`
-> `TaskForceRegistry.apply` -> `DoctrineCompiler.plan_moves`. This answers:
do REAL rendered briefings elicit the same quality as the lab's hand-built
boards?

The endpoint is never configured in a committed file: set OPENAI_BASE_URL
(and OPENAI_API_KEY for hosted; defaults to "none" for local llama-server).

Usage:
    OPENAI_BASE_URL=http://host:8080/v1 uv run python lab/live_epoch.py [--seed N]
    uv run python lab/live_epoch.py --dry-run    # offline: canned known-good answer

Exit code 0 = PASS (delivered, zero validation refusals, zero apply refusals).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from empire.ai.general.briefing import BriefingRenderer
from empire.ai.general.compiler import DoctrineCompiler
from empire.ai.general.registry import TaskForceRegistry
from empire.ai.general.validator import DoctrineValidator, ValidationContext, ValidationResult
from empire.contracts.doctrine import (
    Amendment,
    Briefing,
    BuildDirective,
    Compass,
    ContinueOrder,
    DisbandOrder,
    FormOrder,
    Objective,
    ReinforceOrder,
    RetaskOrder,
    TaskForce,
    TaskForceId,
    Verb,
)
from empire.contracts.turn_plan import TurnPlan
from empire.contracts.world_view import WorldView
from empire.core.city import City, ProductionState
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, RememberedTile, UnitSnapshot, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Destroyer, Transport, UnitKind

PRIMER_PATH = Path(__file__).parent / "prompts" / "stability" / "primer_d.txt"

# Pinned sampling (the production delivery profile — wrinkle-ledger item 4).
TEMPERATURE = 1.0
TOP_P = 0.95
TOP_K = 20
MIN_P = 0.0
PRESENCE_PENALTY = 1.5
MAX_TOKENS = 12288
RETRY_ON_LENGTH = 2

# The trigger-v6 B2-s3 launch pair, rewritten as contract v7's one-line form
# for THIS scenario's ids: commit the just-arrived transport and flip the
# staged force to CAPTURE. Known-good — the dry-run proves the harness.
CANNED_ANSWER = """\
TF 1: RETASK CAPTURE (11,1) ADDING #16 | the awaited lift has arrived; strike east now
TF 2: CONTINUE | keep the capital garrisoned
TF 3: CONTINUE | keep screening the crossing lane
"""


# --- staged scenario ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Staging:
    """The staged epoch inputs: a live game, one player's view, the standing
    registry, and the per-TF event ledger lines."""

    game: Game
    view: WorldView
    registry: TaskForceRegistry
    events: Mapping[TaskForceId, Sequence[str]]

    @property
    def turn(self) -> int:
        return self.game.turn


class LiftScenario:
    """The canonical LIFT trigger board as a real `Game` (board_A2's shape).

    14x6: home continent x0-5 with three owned cities, a sea gap x6-9, a
    partially-explored enemy continent x10-13 with two known enemy cities.
    TF-1 (six armies) STAGEs at the coast awaiting lift; TF-2 (two armies)
    DEFENDs the capital; TF-3 (transport + destroyer) PATROLs the gap. The
    awaited second transport (#16) was just delivered and sits UNASSIGNED.
    """

    TURN = 54
    CAPITAL = Coord(2, 0)
    PORT = Coord(1, 2)  # the coastal city that just delivered transport #16
    THIRD_CITY = Coord(4, 3)
    ENEMY_CITIES = (Coord(11, 1), Coord(11, 2))
    STAGE_AT = Coord(5, 2)
    PATROL_AT = Coord(7, 2)

    def build(self) -> Staging:
        p1 = Player(id=PlayerId(1), name="Us", is_ai=True, view=ViewMap(), color="red")
        p2 = Player(id=PlayerId(2), name="Them", is_ai=True, view=ViewMap(), color="blue")
        game = Game(rules=STANDARD, real_map=self._map(p1, p2), players=[p1, p2], seed=1)
        game.turn = self.TURN
        self._units(game, p1)
        self._fog(p1)
        view = WorldView(real_map=game.map, player=p1, turn=game.turn, rules=game.rules)
        return Staging(game=game, view=view, registry=self._registry(), events=self._events())

    # ---- board ----------------------------------------------------------------

    def _map(self, p1: Player, p2: Player) -> Map:
        width, height = 14, 6
        tiles: dict[Coord, Tile] = {}
        for x in range(width):
            for y in range(height):
                c = Coord(x, y)
                home_land = x <= 5 and y <= 4
                enemy_land = x >= 10 and y <= 4
                terrain = TerrainKind.LAND if home_land or enemy_land else TerrainKind.WATER
                tiles[c] = Tile(coord=c, terrain=terrain)
        cities = [
            City(
                id=CityId(1),
                coord=self.CAPITAL,
                owner=p1,
                production=ProductionState(building=UnitKind.ARMY, work=4),
            ),
            City(
                id=CityId(2),
                coord=self.PORT,
                owner=p1,
                # Restarted after delivering #16 this turn: full 30 turns left.
                production=ProductionState(building=UnitKind.TRANSPORT, work=0),
            ),
            City(
                id=CityId(3),
                coord=self.THIRD_CITY,
                owner=p1,
                production=ProductionState(building=UnitKind.ARMY, work=2),
            ),
            City(id=CityId(4), coord=self.ENEMY_CITIES[0], owner=p2),
            City(id=CityId(5), coord=self.ENEMY_CITIES[1], owner=p2),
        ]
        for city in cities:
            tiles[city.coord] = Tile(coord=city.coord, terrain=TerrainKind.CITY, city=city)
        return Map(width=width, height=height, tiles=tiles)

    def _units(self, game: Game, p1: Player) -> None:
        army_posts = {
            1: self.CAPITAL,  # TF-2, garrisoning the capital
            2: Coord(2, 1),  # TF-2
            3: Coord(5, 0),  # TF-1, staged along the coast column
            4: Coord(5, 1),
            5: self.STAGE_AT,
            6: Coord(5, 3),
            7: Coord(4, 2),
            8: Coord(4, 4),
        }
        for unit_id, at in army_posts.items():
            game.map.place_unit(Army(UnitId(unit_id), p1, at), at)
        game.map.place_unit(Transport(UnitId(9), p1, self.PATROL_AT), self.PATROL_AT)
        game.map.place_unit(Destroyer(UnitId(10), p1, Coord(7, 3)), Coord(7, 3))
        # The just-delivered second transport, in the port city, UNASSIGNED.
        game.map.place_unit(Transport(UnitId(16), p1, self.PORT), self.PORT)

    def _fog(self, p1: Player) -> None:
        """Fog-honest view: home continent + sea gap visible; the enemy coast
        only REMEMBERED from an old probe; the rest of their continent fog."""
        p1.view.visible = {
            Coord(x, y)
            for x in range(10)
            for y in range(6)
            if Coord(x, y) != Coord(8, 3)  # kept remembered: the stale destroyer sighting
        }
        p1.view.remembered[Coord(8, 3)] = RememberedTile(
            coord=Coord(8, 3),
            terrain=TerrainKind.WATER,
            remembered_at=49,
            last_units=[
                UnitSnapshot(
                    unit_id=UnitId(19),
                    kind=UnitKind.DESTROYER,
                    owner_id=PlayerId(2),
                    coord=Coord(8, 3),
                    hits=3,
                ),
            ],
        )
        for c in (Coord(10, 2), Coord(12, 2), Coord(10, 3), Coord(11, 3), Coord(12, 3)):
            p1.view.remembered[c] = RememberedTile(
                coord=c, terrain=TerrainKind.LAND, remembered_at=45
            )
        p1.view.remembered[self.ENEMY_CITIES[0]] = RememberedTile(
            coord=self.ENEMY_CITIES[0],
            terrain=TerrainKind.CITY,
            remembered_at=38,
            last_city_owner=PlayerId(2),
            last_units=[
                UnitSnapshot(
                    unit_id=UnitId(21),
                    kind=UnitKind.ARMY,
                    owner_id=PlayerId(2),
                    coord=self.ENEMY_CITIES[0],
                    hits=1,
                ),
            ],
        )
        p1.view.remembered[self.ENEMY_CITIES[1]] = RememberedTile(
            coord=self.ENEMY_CITIES[1],
            terrain=TerrainKind.CITY,
            remembered_at=45,
            last_city_owner=PlayerId(2),
        )

    # ---- standing taskings -------------------------------------------------------

    def _registry(self) -> TaskForceRegistry:
        return TaskForceRegistry(
            forces=(
                TaskForce(
                    tf_id="1",
                    members=frozenset(UnitId(i) for i in range(3, 9)),
                    objective=Objective(verb=Verb.STAGE, target=self.STAGE_AT),
                    why="awaiting second transport before striking east at the enemy cities",
                    formed_turn=38,
                ),
                TaskForce(
                    tf_id="2",
                    members=frozenset({UnitId(1), UnitId(2)}),
                    objective=Objective(verb=Verb.DEFEND, target=self.CAPITAL),
                    why="keep the capital garrisoned",
                    formed_turn=38,
                ),
                TaskForce(
                    tf_id="3",
                    members=frozenset({UnitId(9), UnitId(10)}),
                    objective=Objective(verb=Verb.PATROL, target=self.PATROL_AT),
                    why="screen the crossing lane",
                    formed_turn=38,
                ),
            )
        )

    def _events(self) -> dict[TaskForceId, list[str]]:
        return {
            "1": [
                "holding along column 5; no contact; no losses",
                f"t{self.TURN}: the awaited second transport (#16) has arrived"
                f" at ({self.PORT.x},{self.PORT.y})",
            ],
            "2": ["garrisoned; no contact; no losses"],
            "3": ["on station; enemy destroyer not seen since t49; no losses"],
        }


# --- answer sources ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelAnswer:
    """What came back from the general: the answer text plus delivery facts."""

    text: str
    reasoning: str | None
    finish_reason: str
    attempts: int
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_s: float

    @property
    def delivered(self) -> bool:
        return self.finish_reason == "stop"


class CannedGeneral:
    """--dry-run: the known-good launch pair, no tokens burned."""

    def answer(self, prompt: str) -> ModelAnswer:
        del prompt  # the canned general does not read; it proves the pipeline
        return ModelAnswer(
            text=CANNED_ANSWER,
            reasoning=None,
            finish_reason="stop",
            attempts=1,
            prompt_tokens=None,
            completion_tokens=None,
            duration_s=0.0,
        )


class LiveGeneral:
    """One OpenAI-compatible chat completion with the pinned delivery profile:
    token cap -> seed-shifted retries on finish=length (wrinkle #4)."""

    def __init__(self, seed: int) -> None:
        from openai import OpenAI  # llmlab dependency group; imported lazily

        if not os.environ.get("OPENAI_BASE_URL"):
            raise SystemExit("OPENAI_BASE_URL is not set (or use --dry-run)")
        self._client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "none"))
        self._model = next(iter(self._client.models.list())).id
        self._seed = seed

    @property
    def model(self) -> str:
        return self._model

    def answer(self, prompt: str) -> ModelAnswer:
        started = time.monotonic()
        for attempt in range(1 + RETRY_ON_LENGTH):
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=TEMPERATURE,
                top_p=TOP_P,
                presence_penalty=PRESENCE_PENALTY,
                max_tokens=MAX_TOKENS,
                seed=self._seed + 1000 * attempt,  # reproducible re-rolls
                timeout=1800,
                extra_body={
                    "top_k": TOP_K,
                    "min_p": MIN_P,
                    "chat_template_kwargs": {"enable_thinking": True},
                },
            )
            if response.choices[0].finish_reason != "length":
                break
            print(f"  finish=length on attempt {attempt + 1}, retrying...", flush=True)
        choice = response.choices[0]
        message = choice.message.model_dump()
        usage = response.usage
        return ModelAnswer(
            text=message.get("content") or "",
            reasoning=message.get("reasoning_content"),
            finish_reason=str(choice.finish_reason),
            attempts=attempt + 1,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            duration_s=time.monotonic() - started,
        )


# --- the epoch pipeline --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EpochReport:
    """Everything observable about one epoch through the production path."""

    briefing: Briefing
    answer: ModelAnswer
    validation: ValidationResult
    registry: TaskForceRegistry  # after apply
    apply_refusals: tuple[object, ...]
    plan: TurnPlan

    @property
    def passed(self) -> bool:
        return self.answer.delivered and self.validation.clean and not self.apply_refusals


class EpochPipeline:
    """Runs ONE epoch end-to-end through the real production components:
    render -> (model) -> validate -> apply -> compile. The answer source is
    injected so the same pipeline serves live and dry runs."""

    def __init__(self, primer: str) -> None:
        self._primer = primer

    def run(
        self, staging: Staging, source: CannedGeneral | LiveGeneral
    ) -> EpochReport:
        task_forces = {tf.tf_id: tf for tf in staging.registry.forces}
        briefing = BriefingRenderer().render(
            staging.view, task_forces, staging.events, staging.turn
        )
        answer = source.answer(self._primer + "\n" + briefing.text)
        validation = DoctrineValidator().validate(
            answer.text, self._context(staging, task_forces)
        )
        roster = frozenset(u.id for u in staging.view.own_units)
        registry, refusals = staging.registry.apply(validation.doctrine, roster)
        plan = DoctrineCompiler().plan_moves(registry, staging.view)
        return EpochReport(
            briefing=briefing,
            answer=answer,
            validation=validation,
            registry=registry,
            apply_refusals=tuple(refusals),
            plan=plan,
        )

    @staticmethod
    def _context(
        staging: Staging, task_forces: Mapping[TaskForceId, TaskForce]
    ) -> ValidationContext:
        view = staging.view
        real = view.real_map()
        members = {tf_id: tf.members for tf_id, tf in task_forces.items()}
        roster = frozenset(u.id for u in view.own_units)
        # Markers exactly as the renderer assigns them: a, b, c... in id order.
        markers = {
            chr(ord("a") + i): unit.id
            for i, unit in enumerate(sorted(view.own_units, key=lambda u: int(u.id)))
            if i < 26
        }
        return ValidationContext(
            turn=staging.turn,
            board_width=real.width,
            board_height=real.height,
            task_forces=members,
            unassigned=staging.registry.unassigned(roster),
            markers=markers,
            owned_cities=frozenset(c.coord for c in view.own_cities),
        )


# --- report rendering ------------------------------------------------------------------


class ReportPrinter:
    """Renders one `EpochReport` as clearly sectioned terminal output."""

    def print(self, report: EpochReport, staging: Staging) -> None:
        self._section("RENDERED BRIEFING")
        print(report.briefing.text, end="")
        self._answer(report.answer)
        self._validation(report.validation)
        self._registry(report)
        self._plan(report, staging)
        self._verdict(report)

    @staticmethod
    def _section(title: str) -> None:
        print(f"\n{'=' * 18} {title} {'=' * max(1, 58 - len(title))}")

    def _answer(self, answer: ModelAnswer) -> None:
        self._section("MODEL ANSWER")
        tokens = (
            f"tokens: {answer.prompt_tokens}+{answer.completion_tokens}"
            if answer.completion_tokens is not None
            else "tokens: n/a (dry run)"
        )
        print(
            f"finish={answer.finish_reason} · attempts={answer.attempts} · "
            f"{tokens} · {answer.duration_s:.0f}s"
        )
        if answer.reasoning is not None:
            print(
                f"reasoning_content: {len(answer.reasoning)} chars"
                f" (~{len(answer.reasoning) // 4} tokens)"
            )
        print()
        print(answer.text.rstrip("\n"))

    def _validation(self, validation: ValidationResult) -> None:
        self._section("VALIDATION")
        print(f"amendments accepted: {len(validation.doctrine.amendments)}")
        for amendment in validation.doctrine.amendments:
            print(f"  + {self._amendment(amendment)}")
        print(f"refusals: {len(validation.refusals)}")
        for refusal in validation.refusals:
            print(f"  - {refusal.order_text!r}: {refusal.reason}")
        print(f"warnings: {len(validation.warnings)}")
        for warning in validation.warnings:
            print(f"  ! {warning.order_text!r}: {warning.reason}")
        print(f"notes: {len(validation.notes)}")
        for note in validation.notes:
            print(f"  · {note}")

    @staticmethod
    def _amendment(amendment: Amendment) -> str:
        def target(objective: Objective) -> str:
            t = objective.target
            body = t.value if isinstance(t, Compass) else f"({t.x},{t.y})"
            return f"{objective.verb.value} {body}"

        def ids(unit_ids: tuple[UnitId, ...]) -> str:
            return " ".join(f"#{int(u)}" for u in unit_ids)

        match amendment:
            case ContinueOrder(tf_id=tf, why=why):
                return f'TF {tf}: CONTINUE — "{why}"'
            case ReinforceOrder(tf_id=tf, unit_ids=units, why=why):
                return f'TF {tf}: REINFORCE UNITS {ids(units)} — "{why}"'
            case RetaskOrder(tf_id=tf, objective=obj, adding=add, why=why):
                extra = f" ADDING {ids(add)}" if add else ""
                return f'TF {tf}: RETASK {target(obj)}{extra} — "{why}"'
            case DisbandOrder(tf_id=tf, why=why):
                return f'TF {tf}: DISBAND — "{why}"'
            case FormOrder(tf_id=tf, unit_ids=units, objective=obj, why=why):
                return f'FORM TF {tf}: UNITS {ids(units)} | {target(obj)} — "{why}"'
            case BuildDirective(city=city, kind=kind, why=why):
                return f'BUILD ({city.x},{city.y}): {kind.value.upper()} — "{why}"'
        return repr(amendment)

    def _registry(self, report: EpochReport) -> None:
        self._section("REGISTRY AFTER APPLY")
        for tf in report.registry.forces:
            members = " ".join(f"#{int(u)}" for u in sorted(tf.members, key=int))
            t = tf.objective.target
            body = t.value if isinstance(t, Compass) else f"({t.x},{t.y})"
            print(
                f'  TF-{tf.tf_id} (formed t{tf.formed_turn}):'
                f' {tf.objective.verb.value} {body} — "{tf.why}"'
            )
            print(f"        members: {members}")
        print(f"apply refusals: {len(report.apply_refusals)}")
        for refusal in report.apply_refusals:
            print(f"  - {refusal}")

    def _plan(self, report: EpochReport, staging: Staging) -> None:
        self._section("COMPILED TURN PLAN")
        units = {u.id: u for u in staging.view.own_units}
        if not report.plan.moves and not report.plan.unloads:
            print("  (no movement emitted this turn)")
        for move in report.plan.moves:
            unit = units.get(move.unit_id)
            kind = unit.kind.value if unit else "?"
            origin = f"({unit.coord.x},{unit.coord.y})" if unit else "(?)"
            path = " -> ".join(f"({x},{y})" for x, y in move.path) or "(holds)"
            print(f"  #{int(move.unit_id):<3} {kind:<10} {origin} -> {path}")
        for unload in report.plan.unloads:
            print(f"  unload #{int(unload.cargo_id)} -> ({unload.to[0]},{unload.to[1]})")

    def _verdict(self, report: EpochReport) -> None:
        self._section("VERDICT")
        checks = [
            ("delivered (finish=stop)", report.answer.delivered),
            ("validation refusals = 0", report.validation.clean),
            ("registry apply refusals = 0", not report.apply_refusals),
        ]
        for label, ok in checks:
            print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        print("PASS" if report.passed else "FAIL")


# --- entry point ---------------------------------------------------------------------


class LiveEpochLab:
    """CLI entry: stage the scenario, pick the answer source, run, report."""

    def main(self, argv: list[str]) -> int:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--dry-run", action="store_true", help="canned answer, no model")
        parser.add_argument("--seed", type=int, default=1, help="sampling seed (default 1)")
        args = parser.parse_args(argv)

        staging = LiftScenario().build()
        source: CannedGeneral | LiveGeneral
        if args.dry_run:
            print("dry run: canned trigger answer, no model call")
            source = CannedGeneral()
        else:
            source = LiveGeneral(seed=args.seed)
            print(f"live epoch against {source.model} (seed {args.seed})")
        report = EpochPipeline(PRIMER_PATH.read_text()).run(staging, source)
        ReportPrinter().print(report, staging)
        return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(LiveEpochLab().main(sys.argv[1:]))

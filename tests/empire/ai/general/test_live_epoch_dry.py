"""Offline canary for the live-epoch harness (integration handshake (b)).

Loads `lab/live_epoch.py` and drives its full pipeline with the canned
trigger answer — real staged `Game` -> `BriefingRenderer` -> validator ->
registry apply -> compiler — proving the harness itself before any tokens
burn. No network, no `openai` import (the live client is imported lazily).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from empire.contracts.doctrine import Verb
from empire.core.coord import Coord
from empire.core.identity import UnitId

_LAB_FILE = Path(__file__).resolve().parents[4] / "lab" / "live_epoch.py"


@pytest.fixture(scope="module")
def live_epoch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("live_epoch", _LAB_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def staging(live_epoch: ModuleType):
    return live_epoch.LiftScenario().build()


@pytest.fixture(scope="module")
def report(live_epoch: ModuleType, staging):
    pipeline = live_epoch.EpochPipeline(live_epoch.PRIMER_PATH.read_text())
    return pipeline.run(staging, live_epoch.CannedGeneral())


def test_dry_run_passes_end_to_end(report) -> None:
    assert report.answer.delivered
    assert report.validation.clean
    assert report.apply_refusals == ()
    assert report.passed


def test_briefing_carries_the_lift_trigger(report) -> None:
    text = report.briefing.text
    assert report.briefing.turn == 54
    assert "the awaited second transport (#16) has arrived at (1,2)" in text
    assert 'STAGE (5,2) — "awaiting second transport before striking east' in text
    # Fog honesty: the enemy interior was never seen and stays out of reach.
    assert "city (11,1); city (11,2)" in text
    assert "It is TURN 54." in text


def test_launch_pair_applies_to_the_registry(report) -> None:
    tf1 = report.registry.get("1")
    assert tf1 is not None
    assert tf1.objective.verb is Verb.CAPTURE
    assert tf1.objective.target == Coord(11, 1)
    assert UnitId(16) in tf1.members  # the new lift committed via ADDING
    assert len(tf1.members) == 7
    # The untouched task forces stand exactly as before.
    tf2 = report.registry.get("2")
    tf3 = report.registry.get("3")
    assert tf2 is not None and tf2.objective.verb is Verb.DEFEND
    assert tf3 is not None and tf3.members == frozenset({UnitId(9), UnitId(10)})


def test_briefing_carries_the_renderer_marker_key(report) -> None:
    """The `Briefing` contract exposes the renderer's own marker assignment;
    the validation context consumes it instead of re-deriving the rule."""
    markers = report.briefing.markers
    assert len(markers) == 11  # 8 armies + 2 transports + 1 destroyer
    assert markers["a"] == UnitId(1)
    assert markers["k"] == UnitId(16)  # highest id takes the last marker


def test_launch_pair_moves_the_fist(report, staging, live_epoch: ModuleType) -> None:
    """The point of the CAPTURE->INVADE upgrade: the overseas capture no
    longer compiles to a frozen land assault. The just-committed lift floats
    out of dry-dock and every strike army closes on the embarkation port."""
    port = live_epoch.LiftScenario.PORT
    units = {int(u.id): u for u in staging.view.own_units}
    steps = {int(m.unit_id): Coord(*m.path[-1]) for m in report.plan.moves}
    assert 16 in steps  # the transport left dry-dock for open water
    for uid in (3, 4, 5, 6, 7, 8):  # TF-1's whole staged fist is under way
        assert uid in steps
        assert steps[uid].chebyshev_to(port) < units[uid].coord.chebyshev_to(port)


def test_compiled_plan_stays_inside_task_force_lines(report) -> None:
    members = {
        int(u): tf.tf_id for tf in report.registry.forces for u in tf.members
    }
    for move in report.plan.moves:
        assert int(move.unit_id) in members  # no unassigned unit is ever moved
    for unload in report.plan.unloads:
        assert int(unload.cargo_id) in members


def test_cli_dry_run_exits_zero(live_epoch: ModuleType, capsys) -> None:
    exit_code = live_epoch.LiveEpochLab().main(["--dry-run"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "PASS" in out
    assert "RENDERED BRIEFING" in out
    assert "REGISTRY AFTER APPLY" in out

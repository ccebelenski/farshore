"""Sanity tests for the doctrine contracts (shared v7 types)."""

from empire.contracts.doctrine import (
    Compass,
    ContinueOrder,
    Doctrine,
    Objective,
    RetaskOrder,
    TaskForce,
    Verb,
)
from empire.core.coord import Coord
from empire.core.identity import UnitId


def test_objective_accepts_coord_and_compass_targets() -> None:
    assert Objective(Verb.CAPTURE, Coord(11, 1)).target == Coord(11, 1)
    assert Objective(Verb.SCOUT, Compass.E).target is Compass.E


def test_amendments_are_frozen_values() -> None:
    launch = RetaskOrder(
        tf_id="1",
        objective=Objective(Verb.CAPTURE, Coord(11, 1)),
        adding=(UnitId(16),),
        why="strike east with the new lift",
    )
    assert launch == RetaskOrder(
        "1", Objective(Verb.CAPTURE, Coord(11, 1)), (UnitId(16),),
        "strike east with the new lift",
    )
    doctrine = Doctrine(turn=54, amendments=(launch, ContinueOrder("2")))
    assert doctrine.amendments[1] == ContinueOrder("2", "")


def test_task_force_record_replays_its_why() -> None:
    tf = TaskForce(
        tf_id="1",
        members=frozenset({UnitId(3), UnitId(4)}),
        objective=Objective(Verb.STAGE, Coord(5, 2)),
        why="awaiting second transport before striking east",
        formed_turn=38,
    )
    assert "awaiting" in tf.why and tf.formed_turn == 38

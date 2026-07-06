"""`TaskForceRegistry` canaries: amendment application, attrition pruning,
and refusal totality (garbage in -> refusals out, never exceptions)."""

from __future__ import annotations

from empire.ai.general.registry import TaskForceRegistry
from empire.contracts.doctrine import (
    BuildDirective,
    Compass,
    ContinueOrder,
    DisbandOrder,
    Doctrine,
    FormOrder,
    Objective,
    ReinforceOrder,
    RetaskOrder,
    TaskForce,
    Verb,
)
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.unit import UnitKind


def _ids(*ns: int) -> frozenset[UnitId]:
    return frozenset(UnitId(n) for n in ns)


_STAGE_AT = Coord(5, 2)


def _tf(
    tf_id: str = "1",
    members: frozenset[UnitId] | None = None,
    verb: Verb = Verb.STAGE,
    target: Coord = _STAGE_AT,
    why: str = "",
    formed_turn: int = 38,
) -> TaskForce:
    return TaskForce(
        tf_id=tf_id,
        members=members if members is not None else _ids(3, 4, 5),
        objective=Objective(verb, target),
        why=why,
        formed_turn=formed_turn,
    )


def test_retask_capture_adding_flips_objective_and_grows_members() -> None:
    """The launch pair: RETASK CAPTURE ... ADDING <transport> in one line —
    the objective flips AND the membership grows (contract v7)."""
    staged = _tf(members=_ids(3, 4, 5, 6, 7, 9), why="awaiting second transport")
    registry = TaskForceRegistry(forces=(staged,))
    roster = _ids(3, 4, 5, 6, 7, 9, 16)
    doctrine = Doctrine(
        turn=52,
        amendments=(
            RetaskOrder(
                tf_id="1",
                objective=Objective(Verb.CAPTURE, Coord(11, 1)),
                adding=(UnitId(16),),
                why="strike east now that the lift is in formation",
            ),
        ),
    )
    updated, refusals = registry.apply(doctrine, roster)
    assert refusals == ()
    tf = updated.get("1")
    assert tf is not None
    assert tf.objective == Objective(Verb.CAPTURE, Coord(11, 1))
    assert tf.members == _ids(3, 4, 5, 6, 7, 9, 16)
    assert tf.why == "strike east now that the lift is in formation"
    assert tf.formed_turn == 38  # formation date survives a retask
    # The original registry is untouched — records are replaced, not mutated.
    assert registry.get("1") is staged


def test_disband_release_then_form_within_one_doctrine() -> None:
    """DISBAND releases survivors to the pool; a later FORM in the SAME
    doctrine may re-home them (amendments apply in order)."""
    registry = TaskForceRegistry(forces=(_tf("1", _ids(3, 4)),))
    roster = _ids(3, 4, 10)
    doctrine = Doctrine(
        turn=60,
        amendments=(
            DisbandOrder(tf_id="1"),
            FormOrder(
                tf_id="4",
                unit_ids=(UnitId(3), UnitId(4), UnitId(10)),
                objective=Objective(Verb.CAPTURE, Coord(11, 1)),
                why="one strike force from the released stage",
            ),
        ),
    )
    updated, refusals = registry.apply(doctrine, roster)
    assert refusals == ()
    assert updated.get("1") is None
    tf = updated.get("4")
    assert tf is not None
    assert tf.members == _ids(3, 4, 10)
    assert tf.formed_turn == 60
    assert updated.unassigned(roster) == frozenset()


def test_reinforce_adds_members_and_keeps_objective_and_why() -> None:
    staged = _tf(why="awaiting second transport")
    registry = TaskForceRegistry(forces=(staged,))
    doctrine = Doctrine(
        turn=50,
        amendments=(ReinforceOrder(tf_id="1", unit_ids=(UnitId(16),), why="dock the transport"),),
    )
    updated, refusals = registry.apply(doctrine, _ids(3, 4, 5, 16))
    assert refusals == ()
    tf = updated.get("1")
    assert tf is not None
    assert tf.members == _ids(3, 4, 5, 16)
    assert tf.objective is staged.objective  # objective unchanged
    assert tf.why == "awaiting second transport"  # WHY set at FORM/RETASK, not REINFORCE


def test_prune_drops_dead_members_and_dissolves_empty_forces() -> None:
    registry = TaskForceRegistry(
        forces=(_tf("1", _ids(3, 4, 5)), _tf("2", _ids(7, 9), verb=Verb.PATROL))
    )
    pruned = registry.prune(_ids(3, 5, 20))  # #4, #7, #9 died; #20 is new production
    tf1 = pruned.get("1")
    assert tf1 is not None
    assert tf1.members == _ids(3, 5)
    assert pruned.get("2") is None  # nobody left -> dissolved
    assert pruned.unassigned(_ids(3, 5, 20)) == _ids(20)


def test_nonsense_doctrine_yields_refusals_never_raises() -> None:
    """Totality: every malformed order refuses with a reason; the registry
    survives unchanged; nothing raises."""
    registry = TaskForceRegistry(forces=(_tf("1", _ids(3, 4)),))
    roster = _ids(3, 4, 10)
    doctrine = Doctrine(
        turn=61,
        amendments=(
            ContinueOrder(tf_id="99"),  # unknown TF
            ReinforceOrder(tf_id="99", unit_ids=(UnitId(10),)),  # unknown TF
            ReinforceOrder(tf_id="1", unit_ids=()),  # no units named
            ReinforceOrder(tf_id="1", unit_ids=(UnitId(77),)),  # phantom unit
            RetaskOrder(tf_id="99", objective=Objective(Verb.SCOUT, Compass.E)),  # unknown TF
            DisbandOrder(tf_id="99"),  # unknown TF
            FormOrder(  # duplicate id
                tf_id="1", unit_ids=(UnitId(10),), objective=Objective(Verb.SCOUT, Compass.E)
            ),
            FormOrder(  # poaching a unit already in TF 1
                tf_id="5", unit_ids=(UnitId(3),), objective=Objective(Verb.SCOUT, Compass.E)
            ),
            FormOrder(tf_id="6", unit_ids=(), objective=Objective(Verb.SCOUT, Compass.E)),
        ),
    )
    updated, refusals = registry.apply(doctrine, roster)
    assert len(refusals) == len(doctrine.amendments)
    assert all(r.reason for r in refusals)
    assert all(r.order_text for r in refusals)  # ledger-ready text on every refusal
    assert updated.forces == registry.forces  # nothing applied


def test_feasibility_predicate_refuses_with_its_reason() -> None:
    """Board feasibility is the caller's oracle: a rejecting predicate turns
    the amendment into a refusal carrying the predicate's reason."""
    registry = TaskForceRegistry(forces=(_tf("1", _ids(3, 4), verb=Verb.CAPTURE),))

    def landlocked(tf: TaskForce) -> str | None:
        if UnitId(10) in tf.members:
            return "unit #10 cannot reach TF 1's theater (no land route, no lift)"
        return None

    doctrine = Doctrine(
        turn=62, amendments=(ReinforceOrder(tf_id="1", unit_ids=(UnitId(10),)),)
    )
    updated, refusals = registry.apply(doctrine, _ids(3, 4, 10), feasible=landlocked)
    assert len(refusals) == 1
    assert "no land route" in refusals[0].reason
    tf = updated.get("1")
    assert tf is not None
    assert tf.members == _ids(3, 4)  # reinforcement did not apply


def test_continue_keeps_and_build_directive_passes_through() -> None:
    registry = TaskForceRegistry(forces=(_tf("1", _ids(3, 4)),))
    doctrine = Doctrine(
        turn=63,
        amendments=(
            ContinueOrder(tf_id="1", why="hold the line"),
            BuildDirective(city=Coord(2, 0), kind=UnitKind.TRANSPORT),
        ),
    )
    updated, refusals = registry.apply(doctrine, _ids(3, 4))
    assert refusals == ()
    assert updated.get("1") == registry.get("1")


def test_unassigned_is_roster_minus_all_members() -> None:
    registry = TaskForceRegistry(
        forces=(_tf("1", _ids(3, 4)), _tf("2", _ids(9), verb=Verb.PATROL))
    )
    assert registry.unassigned(_ids(3, 4, 9, 16, 17)) == _ids(16, 17)
    assert TaskForceRegistry().unassigned(_ids(1, 2)) == _ids(1, 2)

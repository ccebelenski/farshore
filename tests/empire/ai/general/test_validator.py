"""Validator tests: the lab transcript corpus replayed as fixtures.

Every fixture under `fixtures/` is a REAL model answer extracted verbatim
from `lab/transcripts/<battery>/<run>.json`, validated here against the
board context the model actually saw (rebuilt from the corresponding
`lab/prompts/...` board file). Every observed drift is replayed forever;
grammar rules the corpus never exercised get direct tests at the bottom.
"""

from __future__ import annotations

from pathlib import Path

from empire.ai.general.validator import DoctrineValidator, ValidationContext, ValidationResult
from empire.contracts.doctrine import (
    BuildDirective,
    Compass,
    ContinueOrder,
    DisbandOrder,
    FormOrder,
    Objective,
    ReinforceOrder,
    RetaskOrder,
    Verb,
)
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.unit import UnitKind

_FIXTURES = Path(__file__).parent / "fixtures"


def _ctx(
    turn: int,
    task_forces: dict[str, set[int]],
    unassigned: set[int],
    markers: dict[str, int],
    cities: set[tuple[int, int]],
) -> ValidationContext:
    return ValidationContext(
        turn=turn,
        board_width=14,
        board_height=6,
        task_forces={tf: frozenset(UnitId(u) for u in ms) for tf, ms in task_forces.items()},
        unassigned=frozenset(UnitId(u) for u in unassigned),
        markers={m: UnitId(u) for m, u in markers.items()},
        owned_cities=frozenset(Coord(x, y) for x, y in cities),
    )


def _markers(spec: str) -> dict[str, int]:
    """'a1 b2 c3' -> {'a': 1, 'b': 2, 'c': 3}."""
    return {tok[0]: int(tok[1:]) for tok in spec.split()}


# Board contexts, rebuilt from the lab prompt files the transcripts ran against.
CTX_A1 = _ctx(  # lab/prompts/stability/board_A1.txt
    50,
    {"1": {3, 4, 5, 6, 7, 8}, "2": {1, 2}, "3": {9, 10}},
    {11, 12, 13, 14, 15},
    _markers("a1 b2 c3 d4 e5 f6 g7 h8 i11 j12 k13 l14 m15 n9 o10"),
    {(2, 0), (1, 2), (4, 3)},
)
CTX_A2 = _ctx(  # lab/prompts/stability/board_A2.txt
    54,
    {"1": {3, 4, 5, 6, 7, 8}, "2": {1, 2}, "3": {9, 10}},
    {11, 12, 13, 15, 16, 17},
    _markers("a1 b2 c3 d4 e5 f6 g7 h8 i11 j12 k13 l15 m17 n9 o16 p10"),
    {(2, 0), (1, 2), (4, 3), (4, 1)},
)
CTX_A3 = _ctx(  # lab/prompts/stability/board_A3.txt
    58,
    {"2": {1, 2}, "5": {3, 4, 5, 6, 7, 8, 9, 16, 10}},
    {11, 12, 13, 15, 17, 18, 19},
    _markers("a1 b2 c3 d4 e5 f6 g7 h8 i11 j12 k13 l15 m17 n18 o19 p9 q16 r10"),
    {(2, 0), (1, 2), (4, 3), (4, 1)},
)
CTX_B1 = _ctx(  # lab/prompts/stability/board_B1.txt
    52,
    {"1": {3, 4, 5, 6, 7, 8}, "2": {1, 2}, "3": {9, 10}},
    {11, 12, 13, 15, 17},
    _markers("a1 b2 c3 d4 e5 f6 g7 h8 i11 j12 k13 l15 m17 n9 o10"),
    {(2, 0), (1, 2), (4, 3), (4, 1)},
)
CTX_B2 = _ctx(  # lab/prompts/stability/board_B2.txt
    55,
    {"1": {3, 4, 5, 6, 7, 8}, "2": {1, 2}, "3": {9, 10}, "4": {15, 17}},
    {11, 12, 16, 18},
    _markers("a1 b2 c3 d4 e5 f6 g7 h8 i11 j12 k15 l17 m18 n9 o16 p10"),
    {(2, 0), (1, 2), (4, 3), (4, 1)},
)
CTX_C2 = _ctx(  # lab/prompts/stability/board_C2.txt
    58,
    {"2": {1, 2}, "5": {4, 5, 6, 7, 8, 9, 16, 10}},
    {11, 12, 13, 15, 17, 18, 19},
    _markers("a1 b2 c4 d5 e6 f7 g8 h11 i12 j13 k15 l17 m18 n19 o9 p16 q10"),
    {(2, 0), (1, 2), (4, 3), (4, 1)},
)
CTX_START = _ctx(  # lab/prompts/stability/board_START.txt
    6, {}, {1}, _markers("a1"), {(2, 0)}
)
CTX_V2 = _ctx(  # lab/prompts/doctrine/board_contract_v2.txt (epoch-1, no registry yet)
    40, {}, set(range(1, 11)), {}, {(2, 0), (1, 2), (4, 3)}
)
CTX_LIFT = _ctx(  # lab/prompts/epoch2/board_lift_uni.txt (and _clar; same roster)
    50,
    {"1": {3, 4, 5, 6, 7, 8}, "2": {1, 2}, "3": {9, 10}},
    {11, 12, 13, 14, 15, 16},
    _markers("a1 b2 c3 d4 e5 f6 g7 h8 i11 j12 k13 l14 m15 n9 o16 p10"),
    {(2, 0), (1, 2), (4, 3)},
)


def _validate(fixture: str, ctx: ValidationContext) -> ValidationResult:
    text = (_FIXTURES / f"{fixture}.txt").read_text()
    return DoctrineValidator().validate(text, ctx)


def _ids(*ns: int) -> tuple[UnitId, ...]:
    return tuple(UnitId(n) for n in ns)


def _by_type(result: ValidationResult, kind: type) -> list:
    return [a for a in result.doctrine.amendments if isinstance(a, kind)]


# --- the named must-pass fixtures -------------------------------------------


class TestTriggerV6:
    def test_b2_s3_legal_launch_pair_folds(self) -> None:
        """The v6 pair used exactly as designed: REINFORCE + RETASK fold into
        one RETASK ... ADDING for TF-1; the other three TFs stay covered."""
        result = _validate("trigger-v6_B2-s3", CTX_B2)
        assert result.clean
        assert not result.warnings
        assert len(result.doctrine.amendments) == 4
        launch = result.doctrine.amendments[0]
        assert isinstance(launch, RetaskOrder)
        assert launch.tf_id == "1"
        assert launch.objective.verb is Verb.CAPTURE
        assert launch.objective.target == Coord(11, 1)
        assert launch.adding == _ids(16, 11, 12)
        assert result.doctrine.amendments[1] == ReinforceOrder(tf_id="2", unit_ids=_ids(18))
        assert _by_type(result, ContinueOrder) == [
            ContinueOrder(tf_id="3"),
            ContinueOrder(tf_id="4"),
        ]
        assert any("folded" in n for n in result.notes)
        assert result.doctrine.turn == 55

    def test_b2_s2_silent_drift_detected(self) -> None:
        """The RETASK rode inside the REINFORCE line's why — invisible to the
        lab parser, worse than a loud error. The drift detector must fire."""
        result = _validate("trigger-v6_B2-s2", CTX_B2)
        assert result.clean  # the REINFORCE itself is a legal order
        assert len(result.doctrine.amendments) == 4
        assert result.doctrine.amendments[0] == ReinforceOrder(
            tf_id="1", unit_ids=_ids(16), why="RETASK STAGE (7,2)"
        )
        assert len(result.warnings) == 1
        assert "reasons are not orders" in result.warnings[0].reason
        assert "STAGE (7,2)" in result.warnings[0].reason
        assert "REINFORCE UNITS #16" in result.warnings[0].order_text

    def test_b1_s1_shock_response(self) -> None:
        result = _validate("trigger-v6_B1-s1", CTX_B1)
        assert result.clean
        assert not result.warnings
        retasks = _by_type(result, RetaskOrder)
        assert [(r.objective.verb, r.objective.target) for r in retasks] == [
            (Verb.CAPTURE, Coord(5, 4)),
            (Verb.PATROL, Coord(6, 5)),
        ]
        assert _by_type(result, ReinforceOrder) == [
            ReinforceOrder(
                tf_id="2", unit_ids=_ids(11, 12), why="Consolidate garrison at capital"
            )
        ]
        assert any("marker 'i' -> #11" in n for n in result.notes)


class TestNothinkMeltdown:
    def test_phantom_units_and_rewrite_churn_all_refused(self) -> None:
        """The NOTHINK meltdown: phantom ids #11-#13, five self-corrected
        rewrites of the same TF numbers, prose leakage, duplicate BUILDs.
        Only the first coherent block and the first BUILD set survive."""
        result = _validate("doctrine-v2_NOTHINK-s3", CTX_V2)
        forms = _by_type(result, FormOrder)
        builds = _by_type(result, BuildDirective)
        assert len(result.doctrine.amendments) == 9
        assert [f.tf_id for f in forms] == ["1", "2", "3", "4", "5", "6"]
        assert forms[0].unit_ids == _ids(3, 4)
        assert forms[2].objective.verb is Verb.SCOUT
        assert forms[2].objective.target is Compass.NW
        assert {b.city for b in builds} == {Coord(2, 0), Coord(1, 2), Coord(4, 3)}
        # The phantoms are refused by name (epoch-1 roster is #1-#10).
        assert any("unknown unit #11" in r.reason for r in result.refusals)
        assert any("unknown unit #12" in r.reason for r in result.refusals)
        assert any("unknown unit #13" in r.reason for r in result.refusals)
        # "TF 9: UNITS #13 | BUILD (4,3): TRANSPORT" — BUILD is not a verb.
        assert any("unknown verb 'BUILD'" in r.reason for r in result.refusals)
        # Every re-formed TF number is a collision, not a silent overwrite.
        assert sum("already formed in this order set" in r.reason for r in result.refusals) >= 20
        assert sum("duplicate BUILD" in r.reason for r in result.refusals) == 3
        # The visible self-argument lines are strays, refused one by one.
        assert sum(r.reason == "not an order line" for r in result.refusals) == 11
        assert len(result.refusals) == 40


class TestEarlygame:
    def test_start_s1_single_line_restraint(self) -> None:
        """The degenerate case: one city, one army, and exactly one order —
        `FORM TF 1: UNITS 1 | SCOUT <EAST>` (no '#', brackets, compass word)."""
        result = _validate("earlygame_START-s1", CTX_START)
        assert result.clean
        assert not result.warnings
        assert result.doctrine.amendments == (
            FormOrder(
                tf_id="1",
                unit_ids=_ids(1),
                objective=result.doctrine.amendments[0].objective,
            ),
        )
        objective = result.doctrine.amendments[0].objective
        assert objective.verb is Verb.SCOUT
        assert objective.target is Compass.E
        assert any("angle brackets stripped" in n for n in result.notes)
        assert any("compass word 'EAST' read as E" in n for n in result.notes)


# --- stability2 spread -------------------------------------------------------


class TestStability2:
    def test_a1_s2_markers_and_bare_verb(self) -> None:
        result = _validate("stability2_A1-s2", CTX_A1)
        assert result.clean
        assert len(result.doctrine.amendments) == 3
        rein, cont, retask = result.doctrine.amendments
        assert rein == ReinforceOrder(
            tf_id="1",
            unit_ids=_ids(11, 12),
            why="Consolidate eastern forces for cross-channel push",
        )
        assert isinstance(cont, ContinueOrder) and cont.tf_id == "2"
        assert isinstance(retask, RetaskOrder)
        assert retask.objective.verb is Verb.STAGE
        assert retask.objective.target == Coord(9, 2)
        assert any("bare verb read as RETASK" in n for n in result.notes)

    def test_a1_s3_disband_release_and_marker_range(self) -> None:
        """DISBAND x2 releases TF-1 + TF-3 into the pool; the FORM then draws
        `<c-h, n, o>` — a marker range plus letters — under a freeform label."""
        result = _validate("stability2_A1-s3", CTX_A1)
        assert result.clean
        assert len(result.doctrine.amendments) == 4
        assert [type(a) for a in result.doctrine.amendments] == [
            DisbandOrder,
            DisbandOrder,
            ContinueOrder,
            FormOrder,
        ]
        form = result.doctrine.amendments[3]
        assert form.tf_id == "ATK-01"
        assert form.unit_ids == _ids(3, 4, 5, 6, 7, 8, 9, 10)
        assert form.objective.verb is Verb.CAPTURE
        assert form.objective.target == Coord(11, 2)
        assert any("marker range 'c-h' expanded" in n for n in result.notes)

    def test_a2_s3_fold_plus_drift_in_continue_why(self) -> None:
        """Two findings in one answer: TF-3's REINFORCE+RETASK pair folds, and
        TF-2's CONTINUE carries `DEFEND (2,0)` in its why — drift, flagged."""
        result = _validate("stability2_A2-s3", CTX_A2)
        assert result.clean
        assert len(result.doctrine.amendments) == 3
        folded = next(a for a in _by_type(result, RetaskOrder) if a.tf_id == "3")
        assert folded.objective.verb is Verb.CAPTURE
        assert folded.objective.target == Coord(11, 1)
        assert folded.adding == _ids(11, 12)
        assert len(result.warnings) == 1
        assert "DEFEND (2,0)" in result.warnings[0].reason

    def test_a3_s1_plain_reinforce(self) -> None:
        result = _validate("stability2_A3-s1", CTX_A3)
        assert result.clean
        assert result.doctrine.amendments == (
            ContinueOrder(tf_id="2", why="Capital garrisoned"),
            ReinforceOrder(
                tf_id="5", unit_ids=_ids(11, 12, 13), why="Reinforce crossing fleet"
            ),
        )

    def test_b2_s1_four_amendments_no_refusals(self) -> None:
        result = _validate("stability2_B2-s1", CTX_B2)
        assert result.clean
        assert not result.warnings
        assert len(result.doctrine.amendments) == 4
        assert [a.tf_id for a in result.doctrine.amendments] == ["1", "2", "3", "4"]
        retask = _by_type(result, RetaskOrder)[0]
        assert retask.objective.verb is Verb.PATROL
        assert retask.objective.target == Coord(6, 5)

    def test_c2_s3_seven_marker_reinforce(self) -> None:
        result = _validate("stability2_C2-s3", CTX_C2)
        assert result.clean
        rein = _by_type(result, ReinforceOrder)[0]
        assert rein.tf_id == "5"
        assert rein.unit_ids == _ids(11, 12, 13, 15, 17, 18, 19)


# --- doctrine-arm spread -----------------------------------------------------


class TestDoctrineArm:
    def test_a2_s2_quoted_whys_and_bare_verb(self) -> None:
        result = _validate("doctrine-arm_A2-s2", CTX_A2)
        assert result.clean
        assert len(result.doctrine.amendments) == 3
        assert result.doctrine.amendments[0] == ReinforceOrder(
            tf_id="1", unit_ids=_ids(16), why="load convoy for eastern strike"
        )
        retask = result.doctrine.amendments[2]
        assert isinstance(retask, RetaskOrder)
        assert retask.objective.verb is Verb.PATROL
        assert retask.objective.target == Coord(8, 3)
        assert retask.why == "engage destroyer threat"

    def test_a2_s3_missing_colons(self) -> None:
        result = _validate("doctrine-arm_A2-s3", CTX_A2)
        assert result.clean
        assert len(result.doctrine.amendments) == 3
        assert result.doctrine.amendments[0] == ReinforceOrder(tf_id="1", unit_ids=_ids(16))
        assert any("missing ':'" in n for n in result.notes)

    def test_a3_s3_marker_and_id_for_same_unit_collapse(self) -> None:
        """`REINFORCE UNITS n #18` lists unit #18 twice (marker + id); the
        duplicate collapses with a note instead of refusing the line."""
        result = _validate("doctrine-arm_A3-s3", CTX_A3)
        assert result.clean
        rein = _by_type(result, ReinforceOrder)[0]
        assert rein.unit_ids == _ids(18)
        assert any("duplicate unit tokens collapsed: #18" in n for n in result.notes)

    def test_b2_s2_retask_then_reinforce_folds_too(self) -> None:
        """The two-line pair in the OTHER order (RETASK first) — the very
        answer that broke the one-line rule to do the right thing."""
        result = _validate("doctrine-arm_B2-s2", CTX_B2)
        assert result.clean
        assert len(result.doctrine.amendments) == 4
        folded = result.doctrine.amendments[0]
        assert isinstance(folded, RetaskOrder)
        assert folded.objective.verb is Verb.CAPTURE
        assert folded.objective.target == Coord(11, 2)
        assert folded.adding == _ids(16)
        assert len(_by_type(result, ContinueOrder)) == 3

    def test_c2_s2_empty_whys(self) -> None:
        result = _validate("doctrine-arm_C2-s2", CTX_C2)
        assert result.clean
        assert result.doctrine.amendments == (
            ContinueOrder(tf_id="2"),
            ContinueOrder(tf_id="5"),
        )


# --- refusal-rich real answers ----------------------------------------------


class TestRegistryRefusals:
    def test_stability_a3_s1_form_grabs_a_tasked_transport(self) -> None:
        """FORM TF 6 lists marker p — transport #9, mid-mission in TF-5.
        The whole FORM refuses; the two CONTINUEs still stand."""
        result = _validate("stability_A3-s1", CTX_A3)
        assert len(result.doctrine.amendments) == 2
        assert all(isinstance(a, ContinueOrder) for a in result.doctrine.amendments)
        assert len(result.refusals) == 1
        assert "#9 is in TF-5, not UNASSIGNED" in result.refusals[0].reason
        assert not any(isinstance(a, FormOrder) for a in result.doctrine.amendments)

    def test_stability_b2_s1_unknown_marker_and_untasked_pool(self) -> None:
        """FORM TF-5 wants i,j,o,q,p,n: q does not exist on this board and
        p/n (#10/#9) belong to standing TF-3 — three problems, one loud
        refusal, and no partial acceptance of the line."""
        result = _validate("stability_B2-s1", CTX_B2)
        assert len(result.refusals) == 1
        reason = result.refusals[0].reason
        assert "unrecognized unit token 'q'" in reason
        assert "#10 is in TF-3, not UNASSIGNED" in reason
        assert "#9 is in TF-3, not UNASSIGNED" in reason
        # The four standing-TF amendments and the BUILD are unaffected.
        assert len(result.doctrine.amendments) == 5
        build = _by_type(result, BuildDirective)[0]
        assert build.city == Coord(4, 3)
        assert build.kind is UnitKind.ARMY
        assert any("read '·' as '|'" in n for n in result.notes)


# --- legacy separators and flipped forms --------------------------------------


class TestNormalizationCatalog:
    def test_stability_a2_s3_continue_with_restated_objective(self) -> None:
        """Round-1 drift: `TF 2: CONTINUE DEFEND (2,0) | ...` — the trailing
        restatement is ignored with a note, not read as a second order."""
        result = _validate("stability_A2-s3", CTX_A2)
        assert result.clean
        assert len(result.doctrine.amendments) == 3
        assert len(_by_type(result, ContinueOrder)) == 2
        assert any("restated objective" in n and "DEFEND (2,0)" in n for n in result.notes)

    def test_lift3_sealift_s1_flipped_disband(self) -> None:
        """`DISBAND TF 1 | ...` (order flipped) plus a FORM drawing every
        released unit — the textbook launch, one normalization note."""
        result = _validate("lift3_SEALIFT-s1", CTX_LIFT)
        assert result.clean
        assert len(result.doctrine.amendments) == 4
        assert [type(a) for a in result.doctrine.amendments] == [
            RetaskOrder,
            DisbandOrder,
            DisbandOrder,
            FormOrder,
        ]
        form = result.doctrine.amendments[3]
        assert form.unit_ids == _ids(3, 4, 5, 6, 7, 8, 9, 10)
        assert any("order-flipped 'DISBAND TF 1' accepted" in n for n in result.notes)

    def test_prodprofile_s1_middot_separator(self) -> None:
        result = _validate("prodprofile_PROD-s1", CTX_LIFT)
        assert result.clean
        assert len(result.doctrine.amendments) == 4
        form = _by_type(result, FormOrder)[0]
        assert form.tf_id == "5"
        assert form.unit_ids == _ids(3, 4, 5, 6, 7, 8, 9, 10)
        assert any("read '·' as '|'" in n for n in result.notes)

    def test_prodprofile_s3_bracketed_target_and_quoted_why(self) -> None:
        result = _validate("prodprofile_PROD-s3", CTX_LIFT)
        assert result.clean
        form = _by_type(result, FormOrder)[0]
        assert form.objective.target == Coord(11, 1)
        assert form.why == "Advance to capture enemy cities (11,1) and (11,2)"
        assert form.unit_ids == _ids(3, 4, 5, 6, 7, 8, 9, 16, 10)
        assert any("angle brackets stripped" in n for n in result.notes)


# --- grammar rules the corpus never exercised ----------------------------------


class TestDirectRules:
    def _one(self, text: str, ctx: ValidationContext = CTX_B2) -> ValidationResult:
        return DoctrineValidator().validate(text, ctx)

    def _b2_rest(self, *lines: str) -> str:
        """Cover B2's other standing TFs so coverage noise stays out of tests."""
        base = ["TF 1: CONTINUE", "TF 2: CONTINUE", "TF 3: CONTINUE", "TF 4: CONTINUE"]
        return "\n".join(list(lines) + base[len(lines):])

    def test_unknown_task_force_refused(self) -> None:
        result = self._one(self._b2_rest("TF 9: CONTINUE"))
        assert any("TF 9 is not a standing task force" in r.reason for r in result.refusals)

    def test_missing_coverage_refused(self) -> None:
        result = self._one("TF 1: CONTINUE\nTF 2: CONTINUE\nTF 3: CONTINUE")
        assert any(
            r.order_text == "TF 4" and "no amendment" in r.reason for r in result.refusals
        )

    def test_conflicting_double_amendment_refused(self) -> None:
        text = self._b2_rest("TF 1: CONTINUE", "TF 1: DISBAND", "TF 2: CONTINUE",
                             "TF 3: CONTINUE", "TF 4: CONTINUE")
        result = self._one(text)
        assert any("need exactly one" in r.reason for r in result.refusals)
        assert not any(a.tf_id == "1" for a in result.doctrine.amendments)

    def test_build_at_unowned_city_refused(self) -> None:
        result = self._one(self._b2_rest() + "\nBUILD (11,1): ARMY | forward base")
        assert any("(11,1) is not an owned city" in r.reason for r in result.refusals)

    def test_duplicate_build_refused(self) -> None:
        text = self._b2_rest() + "\nBUILD (2,0): ARMY\nBUILD (2,0): TRANSPORT"
        result = self._one(text)
        assert any("duplicate BUILD for city (2,0)" in r.reason for r in result.refusals)
        assert len([a for a in result.doctrine.amendments if isinstance(a, BuildDirective)]) == 1

    def test_unknown_build_kind_refused(self) -> None:
        result = self._one(self._b2_rest() + "\nBUILD (2,0): ZEPPELIN")
        assert any("unknown unit kind 'ZEPPELIN'" in r.reason for r in result.refusals)

    def test_unknown_verb_refused(self) -> None:
        result = self._one(self._b2_rest("TF 1: RETASK BESIEGE (11,1)"))
        assert any("unknown verb 'BESIEGE'" in r.reason for r in result.refusals)

    def test_capture_needs_a_coordinate(self) -> None:
        result = self._one(self._b2_rest("TF 1: RETASK CAPTURE E"))
        assert any("CAPTURE target must be a coordinate" in r.reason for r in result.refusals)

    def test_scout_accepts_compass_and_patrol_accepts_coord(self) -> None:
        result = self._one(self._b2_rest("TF 1: RETASK SCOUT NE", "TF 2: RETASK PATROL (7,2)"))
        assert result.clean
        retasks = [a for a in result.doctrine.amendments if isinstance(a, RetaskOrder)]
        assert retasks[0].objective.target is Compass.NE
        assert retasks[1].objective.target == Coord(7, 2)

    def test_off_board_coordinate_refused(self) -> None:
        result = self._one(self._b2_rest("TF 1: RETASK STAGE (14,2)"))
        assert any("(14,2) is off the board" in r.reason for r in result.refusals)

    def test_unit_committed_twice_refused(self) -> None:
        text = self._b2_rest(
            "TF 1: REINFORCE UNITS #16", "TF 2: REINFORCE UNITS #16",
            "TF 3: CONTINUE", "TF 4: CONTINUE",
        )
        result = self._one(text)
        assert any("#16 already committed in this order set" in r.reason for r in result.refusals)

    def test_retask_adding_draws_from_pool(self) -> None:
        result = self._one(self._b2_rest("TF 1: RETASK CAPTURE (11,1) ADDING #16 #11"))
        assert result.clean
        launch = result.doctrine.amendments[0]
        assert isinstance(launch, RetaskOrder)
        assert launch.adding == _ids(16, 11)

    def test_form_of_standing_tf_refused(self) -> None:
        result = self._one(self._b2_rest() + "\nFORM TF 1: UNITS #16 | STAGE (5,2)")
        assert any("TF 1 already stands" in r.reason for r in result.refusals)

    def test_warnings_do_not_reject(self) -> None:
        result = self._one(self._b2_rest("TF 1: CONTINUE | hold, then CAPTURE (11,1)"))
        assert result.clean
        assert len(result.warnings) == 1
        assert len(result.doctrine.amendments) == 4

    def test_empty_answer_refuses_coverage_only(self) -> None:
        result = self._one("")
        assert result.doctrine.amendments == ()
        assert len(result.refusals) == 4  # one per standing TF
        assert all("no amendment" in r.reason for r in result.refusals)


def test_bracketed_adding_from_live_handshake_parses() -> None:
    """Regression: handshake (b) seed 2 copied the contract's optionality
    brackets literally — `[ADDING #16]` must parse as a clean launch pair."""
    ctx = _ctx(
        54,
        {"1": {3, 4, 5, 6, 7, 8}, "2": {1, 2}, "3": {9, 10}},
        {11, 12, 13, 15, 16, 17},
        _markers("a1 b2 c3 d4 e5 f6 g7 h8 i11 j12 k13 l15 m17 n9 o16 p10"),
        {(2, 0), (1, 2), (4, 3), (4, 1)},
    )
    result = DoctrineValidator().validate(
        "TF-1: RETASK CAPTURE (11,1) [ADDING #16] | launch eastern offensive\n"
        "TF-2: CONTINUE | maintain capital defense\n"
        "TF-3: CONTINUE | maintain screen\n",
        ctx,
    )
    assert result.refusals == ()
    assert result.doctrine is not None
    retask = result.doctrine.amendments[0]
    assert isinstance(retask, RetaskOrder)
    assert retask.objective == Objective(Verb.CAPTURE, Coord(11, 1))
    assert retask.adding == (UnitId(16),)

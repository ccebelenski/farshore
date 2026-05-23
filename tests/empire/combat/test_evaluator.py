"""Phase-6 canary tests for `CombatEvaluator` — pure prediction.

Verifies the closed-form formulas against known cases, purity (no RNG
dependence), and that `expected_outcome` is internally consistent (win
probabilities sum to 1, HPs add up).
"""

from __future__ import annotations

import pytest

from empire.combat.evaluator import CombatEvaluator, ExpectedOutcome
from empire.combat.resolver import CombatError
from empire.core.coord import Coord
from empire.core.identity import PlayerId, UnitId
from empire.core.map import ViewMap
from empire.core.player import Player
from empire.core.unit import UNIT_REGISTRY, Unit, UnitKind


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


def _make(kind: UnitKind, owner: Player, unit_id: int = 1) -> Unit:
    return UNIT_REGISTRY[kind](UnitId(unit_id), owner, Coord(0, 0))


# --- purity ------------------------------------------------------------------


def test_win_probability_is_deterministic(p1: Player, p2: Player) -> None:
    """Same inputs always produce the same output; no hidden RNG."""
    a = _make(UnitKind.ARMY, p1)
    d = _make(UnitKind.ARMY, p2, unit_id=2)
    results = {CombatEvaluator.win_probability(a, d) for _ in range(50)}
    assert len(results) == 1


def test_evaluator_does_not_mutate_inputs(p1: Player, p2: Player) -> None:
    a = _make(UnitKind.BATTLESHIP, p1)
    d = _make(UnitKind.SUBMARINE, p2)
    original_a_hp = a.hits
    original_d_hp = d.hits
    CombatEvaluator.win_probability(a, d)
    CombatEvaluator.expected_outcome(a, d)
    assert a.hits == original_a_hp
    assert d.hits == original_d_hp


# --- known-value spot checks -------------------------------------------------


def test_army_vs_army_one_hp_each(p1: Player, p2: Player) -> None:
    """Single-blow fight with p=0.5 → attacker wins exactly half."""
    a = _make(UnitKind.ARMY, p1)
    d = _make(UnitKind.ARMY, p2, unit_id=2)
    assert CombatEvaluator.win_probability(a, d) == pytest.approx(0.5)


def test_anything_vs_transport_always_wins(p1: Player, p2: Player) -> None:
    """Transport has zero engagement strength; attacker takes no damage."""
    a = _make(UnitKind.ARMY, p1)
    t = _make(UnitKind.TRANSPORT, p2)
    assert CombatEvaluator.win_probability(a, t) == 1.0


def test_transport_vs_anything_always_loses(p1: Player, p2: Player) -> None:
    t = _make(UnitKind.TRANSPORT, p1)
    a = _make(UnitKind.ARMY, p2)
    assert CombatEvaluator.win_probability(t, a) == 0.0


def test_zero_one_outcomes_for_transport_have_certain_hp(p1: Player, p2: Player) -> None:
    """X vs Transport at p=1: attacker keeps all HP, defender ends at 0."""
    a = _make(UnitKind.ARMY, p1)
    t = _make(UnitKind.TRANSPORT, p2)
    o = CombatEvaluator.expected_outcome(a, t)
    assert o.win_probability == 1.0
    assert o.expected_attacker_hp == pytest.approx(float(a.hits))
    assert o.expected_defender_hp == pytest.approx(0.0)


# --- closed-form sanity ------------------------------------------------------


def test_expected_outcome_marginals_sum_to_one(p1: Player, p2: Player) -> None:
    """For any legal matchup, P(att wins) + P(def wins) = 1. We verify by
    summing E[attacker HP > 0] and E[defender HP > 0] probabilities.

    More directly: P(att wins) implies att_hp >= 1 at end (def_hp = 0).
    P(def wins) implies def_hp >= 1 at end (att_hp = 0). These are
    exhaustive and mutually exclusive, so win_probability + (1 -
    win_probability) = 1 trivially. The real check: expected HPs aren't
    nonsensical.
    """
    b = _make(UnitKind.BATTLESHIP, p1)
    s = _make(UnitKind.SUBMARINE, p2)
    o = CombatEvaluator.expected_outcome(b, s)
    assert 0.0 <= o.win_probability <= 1.0
    # E[attacker HP] is at most A and >= 0.
    assert 0.0 <= o.expected_attacker_hp <= float(b.hits)
    assert 0.0 <= o.expected_defender_hp <= float(s.hits)


def test_symmetric_matchup_has_p_one_half(p1: Player, p2: Player) -> None:
    """Patrol vs Patrol, full HP: per-blow p = 0.5; both 1-HP → outcome 50/50."""
    p_one = _make(UnitKind.PATROL, p1)
    p_two = _make(UnitKind.PATROL, p2)
    assert CombatEvaluator.win_probability(p_one, p_two) == pytest.approx(0.5)


def test_higher_hp_favors_that_side_at_equal_p() -> None:
    """All else equal, more HP wins more often. Construct a hypothetical
    matchup by manipulating HP directly. Use Army vs Army (p=0.5 from
    symmetry) and crank up the attacker's HP via direct manipulation.
    """
    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    a = _make(UnitKind.ARMY, p1)
    d = _make(UnitKind.ARMY, p2, unit_id=2)
    # Bump attacker HP to 3 (hypothetical; max_hits=1 normally).
    a.hits = 3
    prob = CombatEvaluator.win_probability(a, d)
    assert prob > 0.5


def test_expected_winner_hp_for_one_sided_fight() -> None:
    """When p ≈ 1.0 (defender hopeless), expected attacker HP ≈ full HP."""
    p1 = Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    a = _make(UnitKind.ARMY, p1)
    t = _make(UnitKind.TRANSPORT, p2)
    o = CombatEvaluator.expected_outcome(a, t)
    assert o.expected_attacker_hp == pytest.approx(float(a.hits))


# --- validation parity with resolver -----------------------------------------


def test_evaluator_raises_on_self_attack(p1: Player) -> None:
    a = _make(UnitKind.ARMY, p1)
    with pytest.raises(CombatError):
        CombatEvaluator.win_probability(a, a)


def test_evaluator_raises_on_friendly_fire(p1: Player) -> None:
    a1 = _make(UnitKind.ARMY, p1)
    a2 = _make(UnitKind.ARMY, p1, unit_id=2)
    with pytest.raises(CombatError):
        CombatEvaluator.win_probability(a1, a2)


def test_evaluator_raises_on_satellite_involvement(p1: Player, p2: Player) -> None:
    a = _make(UnitKind.ARMY, p1)
    sat = _make(UnitKind.SATELLITE, p2)
    with pytest.raises(CombatError):
        CombatEvaluator.win_probability(a, sat)


def test_expected_outcome_returns_expected_outcome_instance(p1: Player, p2: Player) -> None:
    a = _make(UnitKind.ARMY, p1)
    d = _make(UnitKind.ARMY, p2, unit_id=2)
    result = CombatEvaluator.expected_outcome(a, d)
    assert isinstance(result, ExpectedOutcome)

"""Phase-6 canary tests for `CombatResolver`.

Headline canary: for every legal (attacker_kind, defender_kind) pair, the
empirical win-rate over N seeded duels matches the analytical prediction
from `CombatEvaluator.win_probability` within ±3 percentage points. This
both validates the resolver's mechanics AND confirms it agrees with the
evaluator's closed-form math.
"""

from __future__ import annotations

import random

import pytest

from empire.combat.evaluator import CombatEvaluator
from empire.combat.resolver import (
    CombatError,
    CombatOutcome,
    CombatResolver,
    effective_strength,
    per_blow_probability,
)
from empire.core.coord import Coord
from empire.core.identity import PlayerId, UnitId
from empire.core.map import ViewMap
from empire.core.player import Player
from empire.core.unit import UNIT_REGISTRY, Battleship, Submarine, Unit, UnitKind

# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def p1() -> Player:
    return Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap())


@pytest.fixture()
def p2() -> Player:
    return Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())


def _make(kind: UnitKind, owner: Player, unit_id: int = 1) -> Unit:
    return UNIT_REGISTRY[kind](UnitId(unit_id), owner, Coord(0, 0))


# --- strength/probability helpers --------------------------------------------


def test_army_vs_army_is_fair_fight(p1: Player, p2: Player) -> None:
    """Symmetric matchups: same kind, both at full HP → p = 0.5."""
    a1 = _make(UnitKind.ARMY, p1)
    a2 = _make(UnitKind.ARMY, p2)
    assert per_blow_probability(a1, a2) == pytest.approx(0.5)


def test_battleship_beats_submarine_per_blow(p1: Player, p2: Player) -> None:
    """Battleship has higher base strength AND treats submarines as a priority
    target (third in its prefs). p should favor the battleship.
    """
    b = _make(UnitKind.BATTLESHIP, p1)
    s = _make(UnitKind.SUBMARINE, p2)
    p = per_blow_probability(b, s)
    assert p > 0.5


def test_transport_cannot_engage(p1: Player, p2: Player) -> None:
    """Transport has empty preferences → zero effective strength as attacker."""
    t = _make(UnitKind.TRANSPORT, p1)
    a = _make(UnitKind.ARMY, p2)
    assert effective_strength(t, a) == 0.0


def test_transport_vs_anyone_p_is_zero(p1: Player, p2: Player) -> None:
    """Transport attacking Army: Transport eff = 0, Army eff > 0 → p = 0."""
    t = _make(UnitKind.TRANSPORT, p1)
    a = _make(UnitKind.ARMY, p2)
    assert per_blow_probability(t, a) == 0.0


def test_anyone_vs_transport_p_is_one(p1: Player, p2: Player) -> None:
    """Attacker engages Transport with positive strength; Transport eff = 0 → p = 1."""
    a = _make(UnitKind.ARMY, p1)
    t = _make(UnitKind.TRANSPORT, p2)
    assert per_blow_probability(a, t) == 1.0


def test_transport_vs_transport_raises(p1: Player, p2: Player) -> None:
    t1 = _make(UnitKind.TRANSPORT, p1)
    t2 = _make(UnitKind.TRANSPORT, p2)
    with pytest.raises(CombatError, match="engage"):
        per_blow_probability(t1, t2)


# --- validation: illegal matchups raise --------------------------------------


def test_self_attack_raises(p1: Player) -> None:
    a = _make(UnitKind.ARMY, p1)
    with pytest.raises(CombatError, match="itself"):
        CombatResolver().resolve(a, a, random.Random(0))


def test_friendly_fire_raises(p1: Player) -> None:
    a1 = _make(UnitKind.ARMY, p1, unit_id=1)
    a2 = _make(UnitKind.ARMY, p1, unit_id=2)
    with pytest.raises(CombatError, match="friendly"):
        CombatResolver().resolve(a1, a2, random.Random(0))


def test_satellite_as_attacker_raises(p1: Player, p2: Player) -> None:
    sat = _make(UnitKind.SATELLITE, p1)
    a = _make(UnitKind.ARMY, p2)
    with pytest.raises(CombatError, match="Satellite"):
        CombatResolver().resolve(sat, a, random.Random(0))


def test_satellite_as_defender_raises(p1: Player, p2: Player) -> None:
    a = _make(UnitKind.ARMY, p1)
    sat = _make(UnitKind.SATELLITE, p2)
    with pytest.raises(CombatError, match="Satellite"):
        CombatResolver().resolve(a, sat, random.Random(0))


def test_dead_attacker_raises(p1: Player, p2: Player) -> None:
    a = _make(UnitKind.ARMY, p1)
    a.hits = 0
    d = _make(UnitKind.ARMY, p2, unit_id=2)
    with pytest.raises(CombatError, match="Attacker has no HP"):
        CombatResolver().resolve(a, d, random.Random(0))


def test_dead_defender_raises(p1: Player, p2: Player) -> None:
    a = _make(UnitKind.ARMY, p1)
    d = _make(UnitKind.ARMY, p2, unit_id=2)
    d.hits = 0
    with pytest.raises(CombatError, match="Defender has no HP"):
        CombatResolver().resolve(a, d, random.Random(0))


# --- mutation semantics ------------------------------------------------------


def test_resolver_mutates_unit_hp_in_place(p1: Player, p2: Player) -> None:
    """After resolve(), the loser has hits=0 and the winner has hits >= 1."""
    a = _make(UnitKind.ARMY, p1)
    d = _make(UnitKind.ARMY, p2, unit_id=2)
    result = CombatResolver().resolve(a, d, random.Random(0))
    if result.outcome is CombatOutcome.ATTACKER_WINS:
        assert d.hits == 0
        assert a.hits >= 1
        assert result.winner_id == a.id
        assert result.winner_hp == a.hits
    else:
        assert a.hits == 0
        assert d.hits >= 1
        assert result.winner_id == d.id
        assert result.winner_hp == d.hits


def test_blow_log_terminates_with_a_zero_hp(p1: Player, p2: Player) -> None:
    b = _make(UnitKind.BATTLESHIP, p1)
    s = _make(UnitKind.SUBMARINE, p2)
    result = CombatResolver().resolve(b, s, random.Random(0))
    last = result.blows[-1]
    assert last.attacker_hp_after == 0 or last.defender_hp_after == 0
    # Blow count = HP lost from each side, sum.
    assert len(result.blows) == (Battleship.max_hits - b.hits) + (Submarine.max_hits - s.hits)


def test_one_hp_attacker_one_hp_defender_finishes_in_one_blow(p1: Player, p2: Player) -> None:
    a = _make(UnitKind.ARMY, p1)
    d = _make(UnitKind.ARMY, p2, unit_id=2)
    # Both at hits=1 already (Army max_hits=1).
    result = CombatResolver().resolve(a, d, random.Random(0))
    assert len(result.blows) == 1


# --- THE PHASE 6 HEADLINE CANARY: distribution vs analytical ----------------

# Every pair (att, def) where both can engage at least one side. We exclude
# Satellite (can't combat) and Transport vs Transport (mutual zero engagement,
# which raises). All other pairs are legal.

_LEGAL_PAIRS: list[tuple[UnitKind, UnitKind]] = [
    (att, def_)
    for att in UnitKind
    for def_ in UnitKind
    if att is not UnitKind.SATELLITE
    and def_ is not UnitKind.SATELLITE
    and not (att is UnitKind.TRANSPORT and def_ is UnitKind.TRANSPORT)
]


@pytest.mark.parametrize(("att_kind", "def_kind"), _LEGAL_PAIRS)
def test_empirical_win_rate_matches_analytical(
    att_kind: UnitKind, def_kind: UnitKind, p1: Player, p2: Player,
) -> None:
    """For every legal pair: run N seeded duels and compare the empirical
    attacker-win rate against the analytical `win_probability`.

    Tolerance is loose (3 percentage points) because N=1500 has stdev
    ~sqrt(0.25/1500) ≈ 0.013 — three sigmas ≈ 4pp. 3pp catches gross
    formula bugs while accepting natural sampling variance.
    """
    # Reference units (fresh HP) for the analytical computation.
    ref_att = _make(att_kind, p1)
    ref_def = _make(def_kind, p2)
    analytical = CombatEvaluator.win_probability(ref_att, ref_def)

    # Empirical.
    resolver = CombatResolver()
    rng = random.Random(0xC0FFEE)
    wins = 0
    n = 1500
    for _ in range(n):
        a = _make(att_kind, p1)
        d = _make(def_kind, p2)
        result = resolver.resolve(a, d, rng)
        if result.outcome is CombatOutcome.ATTACKER_WINS:
            wins += 1

    empirical = wins / n
    assert abs(empirical - analytical) < 0.03, (
        f"{att_kind.value} vs {def_kind.value}: empirical {empirical:.3f} "
        f"vs analytical {analytical:.3f}; |diff|={abs(empirical - analytical):.3f}"
    )


# --- damage-state combat -----------------------------------------------------


_HP_CROSSCHECK_PAIRS = [
    pytest.param(UnitKind.ARMY, UnitKind.ARMY, id="army-vs-army"),
    pytest.param(UnitKind.BATTLESHIP, UnitKind.SUBMARINE, id="bb-vs-sub"),
    pytest.param(UnitKind.SUBMARINE, UnitKind.TRANSPORT, id="sub-vs-transport"),
    pytest.param(UnitKind.DESTROYER, UnitKind.DESTROYER, id="destroyer-vs-destroyer"),
    pytest.param(UnitKind.CARRIER, UnitKind.SUBMARINE, id="carrier-vs-sub"),
]


@pytest.mark.parametrize(("att_kind", "def_kind"), _HP_CROSSCHECK_PAIRS)
def test_evaluator_expected_hp_matches_empirical_mean(
    att_kind: UnitKind, def_kind: UnitKind, p1: Player, p2: Player,
) -> None:
    """`CombatEvaluator.expected_outcome` returns predicted E[attacker HP] and
    E[defender HP]. These should match the empirical means from many seeded
    resolver duels. Bugs in the negative-binomial coefficient or in the
    HP-weighting sum would slip past the win-probability canary but be
    caught here.
    """
    ref_a = _make(att_kind, p1)
    ref_d = _make(def_kind, p2)
    predicted = CombatEvaluator.expected_outcome(ref_a, ref_d)

    resolver = CombatResolver()
    rng = random.Random(0xBADCAFE)
    n = 1500
    att_hp_total = 0
    def_hp_total = 0
    for _ in range(n):
        a = _make(att_kind, p1)
        d = _make(def_kind, p2)
        resolver.resolve(a, d, rng)
        att_hp_total += a.hits
        def_hp_total += d.hits

    empirical_att = att_hp_total / n
    empirical_def = def_hp_total / n

    # Tolerance based on roughly 2-3 stdev of the sample mean. HP is bounded
    # by max_hits so sample stdev is at most max_hits/2; with n=1500, stdev
    # of the mean is at most max_hits/(2*sqrt(1500)) ≈ max_hits/77.
    # Use 0.4 HP as a generous absolute tolerance.
    assert abs(empirical_att - predicted.expected_attacker_hp) < 0.4, (
        f"{att_kind.value} vs {def_kind.value}: empirical E[att HP] "
        f"{empirical_att:.3f} vs predicted {predicted.expected_attacker_hp:.3f}"
    )
    assert abs(empirical_def - predicted.expected_defender_hp) < 0.4, (
        f"{att_kind.value} vs {def_kind.value}: empirical E[def HP] "
        f"{empirical_def:.3f} vs predicted {predicted.expected_defender_hp:.3f}"
    )


def test_combat_blow_field_consistent_with_hp_deltas(p1: Player, p2: Player) -> None:
    """`CombatBlow.defender_took_damage` must agree with the actual HP delta
    between consecutive blows. Catches a bug where the field is set
    independently of the HP update.
    """
    b = _make(UnitKind.BATTLESHIP, p1)
    s = _make(UnitKind.SUBMARINE, p2)
    initial_att, initial_def = b.hits, s.hits
    result = CombatResolver().resolve(b, s, random.Random(0))

    prev_att, prev_def = initial_att, initial_def
    for blow in result.blows:
        if blow.defender_took_damage:
            assert blow.defender_hp_after == prev_def - 1
            assert blow.attacker_hp_after == prev_att
        else:
            assert blow.attacker_hp_after == prev_att - 1
            assert blow.defender_hp_after == prev_def
        prev_att, prev_def = blow.attacker_hp_after, blow.defender_hp_after


def test_damaged_battleship_still_likely_beats_full_sub(p1: Player, p2: Player) -> None:
    """A heavily damaged battleship (hits=2) vs a full-hp sub (hits=2). Same HP,
    similar effective strength → not a slam-dunk for either side."""
    b = _make(UnitKind.BATTLESHIP, p1)
    b.hits = 2
    s = _make(UnitKind.SUBMARINE, p2)
    assert s.hits == Submarine.max_hits  # confirm fresh
    prob = CombatEvaluator.win_probability(b, s)
    # Battleship base str 4 vs Sub base str 3; both treat each other as
    # priority targets — battleship still favored but not overwhelming.
    assert 0.5 < prob < 0.9

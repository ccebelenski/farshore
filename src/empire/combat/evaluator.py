"""`CombatEvaluator`: pure (non-mutating) prediction of combat outcomes.

Used by AI behaviors to decide whether to engage. The evaluator returns
analytical results computed from the per-blow probability formula; no RNG
is consulted, so the same inputs always yield the same outputs.

The math: combat is a race-to-zero between two HP pools where each blow
has fixed probability `p` of damaging the defender (else the attacker).
This is the classic "two-walk gambler's ruin" — closed-form probabilities
come from the negative-binomial distribution.

If the attacker wins, the final state is `(att_hp = k, def_hp = 0)` for
some k in 1..A. The number of blows along the way is `(A - k) + D`, the
last of which damaged the defender (a "success"). The earlier blows
contain `D - 1` successes and `A - k` failures in any order:

    P(att ends with k HP) = C(A + D - k - 1, D - 1) * p^D * (1-p)^(A - k)

Similarly for defender wins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from empire.combat.resolver import (
    CombatResolver,
    effective_strength,
)
from empire.core.unit import Unit


@dataclass(frozen=True, slots=True)
class ExpectedOutcome:
    """Analytical prediction of a hypothetical combat."""

    win_probability: float
    expected_attacker_hp: float
    expected_defender_hp: float


class CombatEvaluator:
    """Pure predictor of combat outcomes.

    Public methods accept two units and produce analytical results. The
    same validation rules as `CombatResolver` apply — illegal matchups
    raise (caller should not be asking about them).
    """

    @staticmethod
    def win_probability(attacker: Unit, defender: Unit) -> float:
        """Probability the attacker wins (defender reaches 0 HP first).

        Returns 0.0 if the attacker cannot engage the defender at all;
        returns 1.0 if the defender cannot engage the attacker at all.
        Raises `CombatError` for illegal matchups (satellite, self,
        friendly, dead unit, or mutual no-engagement).
        """
        # Resolver and evaluator share the same validation rules.
        CombatResolver._validate(attacker, defender)  # pyright: ignore[reportPrivateUsage]
        p = _per_blow(attacker, defender)
        return _attacker_win_prob(attacker.hits, defender.hits, p)

    @staticmethod
    def expected_outcome(attacker: Unit, defender: Unit) -> ExpectedOutcome:
        """Full marginal: win probability plus expected HP for each side."""
        # Resolver and evaluator share the same validation rules.
        CombatResolver._validate(attacker, defender)  # pyright: ignore[reportPrivateUsage]
        p = _per_blow(attacker, defender)
        return _expected_outcome(attacker.hits, defender.hits, p)


# -----------------------------------------------------------------------------
# Internal: per-blow probability + closed-form outcome math.
# -----------------------------------------------------------------------------


def _per_blow(attacker: Unit, defender: Unit) -> float:
    """Compute `p` directly (without going through resolver's raise path)
    so the evaluator can handle one-sided zero-strength matchups cleanly.
    """
    att_eff = effective_strength(attacker, defender)
    def_eff = effective_strength(defender, attacker)
    total = att_eff + def_eff
    if total == 0.0:
        # Both validation passed (above) but neither can engage. This
        # shouldn't happen given _validate's checks, but be defensive.
        from empire.combat.resolver import CombatError
        raise CombatError("Neither unit can engage the other")
    return att_eff / total


def _attacker_win_prob(att_hp: int, def_hp: int, p: float) -> float:
    """P(attacker wins) = sum over k=1..A of P(end with att=k, def=0)."""
    if att_hp <= 0 or def_hp <= 0:
        raise ValueError("HP must be positive")
    if p == 1.0:
        return 1.0
    if p == 0.0:
        return 0.0
    q = 1.0 - p
    total = 0.0
    for k in range(1, att_hp + 1):
        # blows so far: D successes + (A - k) failures, with the very last
        # blow being a success (closing the combat). Order the earlier
        # ones however.
        total += math.comb(att_hp + def_hp - k - 1, def_hp - 1) * (p ** def_hp) * (q ** (att_hp - k))
    return total


def _expected_outcome(att_hp: int, def_hp: int, p: float) -> ExpectedOutcome:
    """Walk every absorbing state, accumulate marginals."""
    if att_hp <= 0 or def_hp <= 0:
        raise ValueError("HP must be positive")
    q = 1.0 - p

    win_prob = 0.0
    e_att = 0.0
    e_def = 0.0

    # Attacker-wins terms: end at (k, 0) for k in 1..A.
    for k in range(1, att_hp + 1):
        pk = math.comb(att_hp + def_hp - k - 1, def_hp - 1) * (p ** def_hp) * (q ** (att_hp - k))
        win_prob += pk
        e_att += k * pk
        # defender HP = 0 in this branch; contributes 0 to e_def.

    # Defender-wins terms: end at (0, k) for k in 1..D.
    for k in range(1, def_hp + 1):
        pk = math.comb(att_hp + def_hp - k - 1, att_hp - 1) * (q ** att_hp) * (p ** (def_hp - k))
        e_def += k * pk
        # attacker HP = 0 in this branch; contributes 0 to e_att.

    return ExpectedOutcome(
        win_probability=win_prob,
        expected_attacker_hp=e_att,
        expected_defender_hp=e_def,
    )

"""`CombatResolver`: per-blow attrition combat between two units.

Implements the combat rules from `planning/01-game-rules-spec.md` §4. Per
blow: with probability `p` the defender loses 1 HP; otherwise the attacker
loses 1 HP. Repeat until one side reaches 0 HP. `p` is derived from the
units' `strength` and the attacker's `attack_preferences`.

The resolver mutates `Unit.hits` on both combatants. The caller is
responsible for removing the loser from the map.

See `combat.evaluator` for the pure (non-mutating) outcome prediction used
by AI engagement decisions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from empire.core.identity import UnitId
from empire.core.unit import Unit, UnitKind

# -----------------------------------------------------------------------------
# Strength derivation (shared with the evaluator)
# -----------------------------------------------------------------------------

# A single character per unit kind, used to look up positions in each unit's
# `attack_preferences()` string. Satellite has no char because it never
# participates in combat.
_KIND_CHARS: dict[UnitKind, str] = {
    UnitKind.ARMY: "A",
    UnitKind.FIGHTER: "F",
    UnitKind.PATROL: "P",
    UnitKind.DESTROYER: "D",
    UnitKind.SUBMARINE: "S",
    UnitKind.TRANSPORT: "T",
    UnitKind.CARRIER: "C",
    UnitKind.BATTLESHIP: "B",
    UnitKind.SATELLITE: "",
}


def effective_strength(attacker: Unit, defender: Unit) -> float:
    """Attacker's effective combat strength against this specific defender.

    Looks up the defender's kind in the attacker's preference string and
    applies a linear decay multiplier based on position (position 0 →
    full strength; later positions → less). Returns 0 if the attacker
    cannot engage this defender kind at all.
    """
    if defender.kind is UnitKind.SATELLITE:
        return 0.0
    target_char = _KIND_CHARS[defender.kind]
    prefs = attacker.attack_preferences()
    if not prefs or target_char not in prefs:
        return 0.0
    position = prefs.index(target_char)
    multiplier = 1.0 - position / len(prefs)
    return float(attacker.strength) * multiplier


def per_blow_probability(attacker: Unit, defender: Unit) -> float:
    """Probability that the defender loses 1 HP on a blow.

    Symmetric formula: `p = eff(att, def) / (eff(att, def) + eff(def, att))`.
    A higher value means combat tilts toward the attacker.

    Raises `CombatError` if neither unit can engage the other (both
    effective strengths are zero). Callers should validate first via
    `CombatResolver._validate` if they want consistent error semantics.
    """
    att_eff = effective_strength(attacker, defender)
    def_eff = effective_strength(defender, attacker)
    total = att_eff + def_eff
    if total == 0.0:
        raise CombatError(
            f"Neither {attacker.kind.value} nor {defender.kind.value} can engage the other"
        )
    return att_eff / total


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


class CombatOutcome(Enum):
    ATTACKER_WINS = "attacker_wins"
    DEFENDER_WINS = "defender_wins"


@dataclass(frozen=True, slots=True)
class CombatBlow:
    """A single round of attrition.

    `defender_took_damage` is True if the defender lost an HP this blow,
    False if the attacker did. The two `*_hp_after` fields capture HP
    after this blow's resolution.
    """

    defender_took_damage: bool
    attacker_hp_after: int
    defender_hp_after: int


@dataclass(frozen=True, slots=True)
class CombatResult:
    outcome: CombatOutcome
    winner_id: UnitId
    winner_hp: int
    blows: tuple[CombatBlow, ...]


class CombatError(RuntimeError):
    """Raised on illegal combat: self-attack, friendly fire, satellite
    involvement, an already-dead combatant, or a matchup where neither
    unit can engage the other.
    """


# -----------------------------------------------------------------------------
# Resolver
# -----------------------------------------------------------------------------


class CombatResolver:
    """Drives a fight to its conclusion via per-blow attrition.

    Mutates `Unit.hits` on both combatants. After `resolve()`, the loser's
    `hits` is 0 and the winner's `hits` is whatever they have left.
    """

    def resolve(
        self,
        attacker: Unit,
        defender: Unit,
        rng: random.Random,
    ) -> CombatResult:
        self._validate(attacker, defender)
        p = per_blow_probability(attacker, defender)

        att_hp = attacker.hits
        def_hp = defender.hits
        blows: list[CombatBlow] = []

        while att_hp > 0 and def_hp > 0:
            if rng.random() < p:
                def_hp -= 1
                blows.append(CombatBlow(True, att_hp, def_hp))
            else:
                att_hp -= 1
                blows.append(CombatBlow(False, att_hp, def_hp))

        attacker.hits = att_hp
        defender.hits = def_hp

        if def_hp <= 0:
            return CombatResult(
                outcome=CombatOutcome.ATTACKER_WINS,
                winner_id=attacker.id,
                winner_hp=att_hp,
                blows=tuple(blows),
            )
        return CombatResult(
            outcome=CombatOutcome.DEFENDER_WINS,
            winner_id=defender.id,
            winner_hp=def_hp,
            blows=tuple(blows),
        )

    @staticmethod
    def _validate(attacker: Unit, defender: Unit) -> None:
        if attacker is defender:
            raise CombatError("Unit cannot attack itself")
        if attacker.owner is defender.owner:
            raise CombatError("Cannot attack friendly unit")
        if attacker.kind is UnitKind.SATELLITE or defender.kind is UnitKind.SATELLITE:
            raise CombatError("Satellites cannot attack or be attacked (spec §2.4)")
        if attacker.hits <= 0:
            raise CombatError("Attacker has no HP")
        if defender.hits <= 0:
            raise CombatError("Defender has no HP")

"""Campaign odds estimator (Phase 15.7, step 1 — no air yet).

The combined-arms attack doctrine (see memory `project_campaign_doctrine`)
rests on one primitive: an estimate of *P(success)* against a target by the
time a force could actually arrive. Every commitment decision branches off this
number — commit at >= threshold, else rebudget / retarget / scrap, and when
every target is gray, gamble (Hail Mary).

This module is deliberately **pure**: `estimate_success` and its helpers take
plain values and return a probability in [0, 1]. No `WorldView`, no mutation —
so it is trivially unit-testable and the strategist can call it per candidate.
The factors are crude linear knobs by design ("war is messy"); they are tuned
against the land-brawl arena, not derived.

The four factors (multiplied):

* **combat_odds** — can the force beat the target's defenders AND run the city's
  artillery gauntlet? The field-defender term comes from intel
  (`Opportunity.success_probability`); the gauntlet term is a crude model of
  the one-shot-per-round artillery (`gauntlet_breakthrough`).
* **arrival_discount** — a force that takes many turns to assemble + march
  arrives late, by which time the enemy has reinforced. Longer → lower.
* **trend_factor** — if the enemy holds more cities, they out-produce us, so
  our odds decay while we wait. Out-numbered → lower.
* **surprise_bonus** — mostly binary (the user's refinement): if *no* campaign
  unit has been provably spotted, surprise is high (a real bonus); if any has,
  it collapses to negligible. We can only infer spotting from enemy sensors we
  know about, so this over-estimates our own stealth — accepted as conservative
  (the probe loop self-corrects next turn).
"""

from __future__ import annotations

from empire.core.ruleset import RuleSet

# Tuning knobs (v1 — crude, arena-tuned). Grouped so a sweep can find them.
_ARRIVAL_PENALTY_PER_TURN = 0.05  # each formation turn shaves this much off P
_ARRIVAL_FLOOR = 0.30  # never discount arrival below this multiplier
_TREND_PENALTY_PER_CITY = 0.08  # per enemy city held beyond ours
_TREND_FLOOR = 0.50  # never let the trend term sink below this
_SURPRISE_BONUS = 0.20  # multiplier bump when wholly unspotted
_GAUNTLET_FLOOR = 0.05  # a lone army vs a city is grim, not impossible
# A target is "contested" — and so subject to the time-pressure discounts
# (arrival lag, production trend) — only when taking it is actually a race
# against resistance: real mobile defenders, or city artillery to run. An
# undefended neutral is not a race; you just walk in, so those discounts do
# NOT apply (applying them flat over-analysed early grabs and collapsed the
# front count — see Phase 15.7 step-1 failure).
_CONTESTED_FIELD_ODDS = 0.95  # field_odds below this ⇒ defenders worth racing


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def gauntlet_breakthrough(
    assault_size: int, rules: RuleSet
) -> float:
    """Crude P(at least one of `assault_size` armies runs the artillery
    gauntlet onto the city tile), under the one-shot-per-round rule.

    A city fires once per round, so over an approach of roughly
    `city_artillery_range` rounds it can neutralise at most that many armies —
    each shot *stops* an army (kills it, or misses-but-pins it) with
    probability `hit_prob + (1 - hit_prob) * pin_prob`. The more armies arrive
    together, the smaller the fraction the gun can stop — concentration beats
    trickle. Returns 1.0 when the ruleset has no artillery (nothing to run).
    """
    if rules.city_artillery_range <= 0:
        return 1.0
    n = max(1, assault_size)
    stop_per_shot = rules.city_artillery_hit_prob + (
        1.0 - rules.city_artillery_hit_prob
    ) * rules.city_artillery_pin_prob
    shots = rules.city_artillery_range  # ~rounds the army spends in range
    expected_stopped = min(n, shots) * stop_per_shot
    breakthrough = 1.0 - expected_stopped / n
    return _clamp(breakthrough, _GAUNTLET_FLOOR, 1.0)


def arrival_discount(formation_turns: int) -> float:
    """Multiplier for how long the force takes to assemble + reach the target.
    Longer formation → the enemy reinforces → lower odds."""
    raw = 1.0 - _ARRIVAL_PENALTY_PER_TURN * max(0, formation_turns)
    return _clamp(raw, _ARRIVAL_FLOOR, 1.0)


def trend_factor(my_city_count: int, enemy_city_count: int) -> float:
    """Multiplier for the production race. If the enemy holds more cities they
    out-produce us and our odds decay over time; parity or ahead → no penalty."""
    deficit = max(0, enemy_city_count - my_city_count)
    raw = 1.0 - _TREND_PENALTY_PER_CITY * deficit
    return _clamp(raw, _TREND_FLOOR, 1.0)


def surprise_bonus(any_unit_spotted: bool) -> float:
    """Binary-ish surprise (the user's refinement): a wholly-unspotted campaign
    gets a real bonus; once any unit is provably spotted it collapses to 1.0."""
    return 1.0 if any_unit_spotted else 1.0 + _SURPRISE_BONUS


def estimate_success(
    *,
    field_odds: float,
    assault_size: int,
    formation_turns: int,
    my_city_count: int,
    enemy_city_count: int,
    any_unit_spotted: bool,
    rules: RuleSet,
) -> float:
    """P(success) against a target by the time the force could arrive.

    `field_odds` is the intel-derived chance of beating the target's mobile
    defenders (`Opportunity.success_probability`); the artillery gauntlet,
    arrival lag, production trend, and surprise are layered on. Result is
    clamped to [0, 1]. Keyword-only so call sites read as a checklist of the
    four doctrine factors.

    The time-pressure discounts (arrival lag, production trend) apply ONLY to
    a *contested* target — one with real defenders or city artillery — because
    only then is taking it a race the enemy can spoil. Against an undefended
    neutral, P is just the (near-certain) field odds: grab it now, don't
    over-analyse. This is the fix for the step-1 regression, where flat
    discounting suppressed soft grabs and collapsed the AI to one front.
    """
    field = _clamp(field_odds, 0.0, 1.0)
    gauntlet = gauntlet_breakthrough(assault_size, rules)
    combat = field * gauntlet

    contested = field < _CONTESTED_FIELD_ODDS or rules.city_artillery_range > 0
    if not contested:
        # No race: surprise can still help, but no time-pressure penalty.
        return _clamp(combat * surprise_bonus(any_unit_spotted), 0.0, 1.0)

    raw = (
        combat
        * arrival_discount(formation_turns)
        * trend_factor(my_city_count, enemy_city_count)
        * surprise_bonus(any_unit_spotted)
    )
    return _clamp(raw, 0.0, 1.0)


# The commitment threshold: a forming force whose CURRENT odds already clear
# this launches early (strike a soft target now — surprise beats waiting for a
# bigger fist it doesn't need).
COMMIT_THRESHOLD = 0.60

# --- commitment-loop knobs (operational launch gate; arena-tuned) ------------
# How many turns a force may stay FORMING before it commits with whatever it
# has ("shit happens, go with less"). The anti-stalemate backstop: forces always
# eventually strike rather than muster forever.
PREP_DEADLINE = 8
# Abandon an objective when even a full fist that reached it could not clear
# this best-case probability — a doomed siege, scrapped so the force redirects.
# Separate from the swap margin and absolute, so noise can't trigger it and a
# sticky incumbent can't suppress it. ~1/3 of COMMIT_THRESHOLD: a merely-hard
# target is still prosecuted; a hopeless one is dropped.
ABANDON_FLOOR = 0.20
# After abandoning a target, ignore it for this many turns so the greedy
# strategist re-proposing it does not cause immediate re-assembly (thrash).
ABANDON_COOLDOWN = 6
# Anti-oscillation: a challenger only displaces the committed incumbent target
# if its odds beat the incumbent's by this margin, which GROWS with sunk
# progress (don't abandon a half-marched assault for a marginally better one).
# Floor sits just above the per-turn P jitter (~0.05) so noise alone can't swap.
_SWAP_MARGIN_FORMING_BASE = 0.05  # empty force: cheap to redirect
_SWAP_MARGIN_FORMING_FILL = 0.15  # + this x fraction-of-fist-assembled
_SWAP_MARGIN_EN_ROUTE = 0.30  # marching: the approach is sunk
_SWAP_MARGIN_ENGAGED = 0.50  # in contact: a near-lock


def swap_margin(state: str, fill_fraction: float) -> float:
    """Extra odds a challenger must beat the incumbent by to force a target
    swap, scaled by sunk progress (see `project_campaign_doctrine`). `state` is
    the incumbent `TaskForceState.value`; `fill_fraction` is armies-assembled /
    fist-needed (only meaningful while FORMING)."""
    if state == "forming":
        return _SWAP_MARGIN_FORMING_BASE + _SWAP_MARGIN_FORMING_FILL * _clamp(
            fill_fraction, 0.0, 1.0
        )
    if state == "en_route":
        return _SWAP_MARGIN_EN_ROUTE
    if state == "engaged":
        return _SWAP_MARGIN_ENGAGED
    return _SWAP_MARGIN_FORMING_BASE

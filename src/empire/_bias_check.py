"""Analytical check for the aggression-bias-with-reversion idea — NO game, no
playout. The mechanism is just a decision rule over a few numbers, so we can
ask the only question that matters before writing any AI code:

    Is there a single aggression scalar `a` that makes the AI choose the RIGHT
    plan in every canonical situation from the playtest diagnosis at once?

Decision rule (per candidate plan p):

    U(p; a) = flat[p] + a * bold[p] * (1 - caution[p])
    choice(a) = argmax_p U(p; a)

`flat` is the honest search value (evaluator units: army~10, city~100, losing an
army ~ -10). `bold` flags an aggressive plan. `caution` flags a fired danger
signal — and the `(1 - caution)` factor is the reversion: when caution fires the
bias vanishes and the plan is judged on `flat` alone ("the smarter move wins").

What the sweep tells us:
  * lower bounds  — scenarios whose right answer is bold+uncautioned (bootstrap,
    crack-the-neutral): `a` must be big enough to overcome the flat deficit.
  * upper bounds  — scenarios where a bold plan is WRONG yet uncautioned: `a`
    must stay small. An upper bound that appears = a MISSING caution signal.
  * no bound      — suicidal plans are caution-gated, so bias never touches them;
    they lose for every `a`. (horde-proofness, straight from the math.)
Feasible iff max(lower) < min(upper). Width = robustness of the single knob.

Run:  uv run python -m empire._bias_check
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    name: str
    flat: float       # honest in-horizon value (evaluator units)
    bold: bool        # is this the aggressive option?
    caution: bool     # does a danger signal fire for it? (=> revert to flat)


@dataclass(frozen=True)
class Scenario:
    name: str
    note: str
    plans: tuple[Plan, ...]
    desired: str      # name of the plan a good general would pick


def utility(p: Plan, a: float) -> float:
    return p.flat + (a if (p.bold and not p.caution) else 0.0)


def choice(s: Scenario, a: float) -> str:
    # Tie-break conservatively: on equal utility prefer the NON-bold plan, so the
    # bias must strictly win, never coast in on a tie.
    return max(s.plans, key=lambda p: (utility(p, a), not p.bold)).name


# --- the diagnosis, as numbers -------------------------------------------------
# flat values are deliberate and commented; they encode "what the 12-turn search
# honestly sees", which is the whole point — payoffs past the horizon are absent.

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "bootstrap-navy",
        "home secured, enemy overseas UNSEEN. Build the Nth (stacking, ~useless) "
        "army vs build a transport whose payoff is past the horizon.",
        (
            Plan("build_army_home", flat=3.0, bold=False, caution=False),
            # transport: no visible payoff in-horizon, nothing dangerous about
            # building a boat in your own harbor => bold, NOT cautioned.
            Plan("build_transport", flat=0.0, bold=True, caution=False),
        ),
        desired="build_transport",
    ),
    Scenario(
        "crack-neutral",
        "stuck at 1 city under FORTIFIED with ADEQUATE mass; assault eats a few "
        "turns of attrition (seen) before the +100 city (past horizon, unseen).",
        (
            Plan("sit_and_build", flat=3.0, bold=False, caution=False),
            Plan("assault_with_mass", flat=-5.0, bold=True, caution=False),
        ),
        desired="assault_with_mass",
    ),
    Scenario(
        "suicide-assault",
        "lone army vs 3 defenders + artillery. Odds are KNOWN-bad in-horizon, so "
        "the odds caution signal fires and reverts the bold bonus.",
        (
            Plan("wait", flat=0.0, bold=False, caution=False),
            Plan("lone_assault", flat=-12.0, bold=True, caution=True),
        ),
        desired="wait",
    ),
    Scenario(
        "unescorted-transport",
        "loaded transport vs a VISIBLE destroyer in the strait. Sinking is known "
        "in-horizon; the escort caution signal fires and reverts the bias.",
        (
            Plan("hold_for_escort", flat=0.0, bold=False, caution=False),
            Plan("sail_unescorted", flat=-40.0, bold=True, caution=True),
        ),
        desired="hold_for_escort",
    ),
    Scenario(
        "favorable-attack",
        "adjacent weaker enemy, good odds — a clean in-horizon win. Should be "
        "taken even at a=0; bias only reinforces it.",
        (
            Plan("pass", flat=0.0, bold=False, caution=False),
            Plan("attack_weak", flat=9.0, bold=True, caution=False),
        ),
        desired="attack_weak",
    ),
    Scenario(
        "dont-over-extend",
        "home city threatened with recapture. Expanding elsewhere leaves it open; "
        "the home/recapture caution signal fires and reverts the bias.",
        (
            Plan("defend_home", flat=12.0, bold=False, caution=False),
            Plan("expand_leave_home_open", flat=4.0, bold=True, caution=True),
        ),
        desired="defend_home",
    ),
)

# A sentinel scenario demonstrating the completeness audit: a bold plan that is
# actually BAD but which we FORGOT to tag with a caution signal. If included it
# sets an upper bound and can empty the feasible window — the tool catching a
# missing signal. Kept separate so the core result stays clean.
SENTINEL = Scenario(
    "MISSING-SIGNAL-probe",
    "a bold plan that is bad (flat<0) but left UNCAUTIONED on purpose — should "
    "force the tool to report an upper bound (i.e. 'you forgot a caution signal').",
    (
        Plan("safe_option", flat=0.0, bold=False, caution=False),
        Plan("untagged_bad_bold", flat=-6.0, bold=True, caution=False),
    ),
    desired="safe_option",
)


def correct_interval(s: Scenario, a_max: float, step: float) -> tuple[float | None, float | None]:
    """Return (flip_up, break_at): the smallest `a` at which the scenario becomes
    correct (None if correct at a=0), and the smallest `a` at which it turns
    incorrect again (None if it never breaks)."""
    flip_up: float | None = None
    break_at: float | None = None
    a = 0.0
    correct0 = choice(s, 0.0) == s.desired
    while a <= a_max + 1e-9:
        ok = choice(s, a) == s.desired
        if not correct0 and flip_up is None and ok:
            flip_up = a
        if (correct0 or flip_up is not None) and break_at is None and not ok:
            break_at = a
        a += step
    return flip_up, break_at


def run(scenarios: tuple[Scenario, ...], a_max: float = 40.0, step: float = 0.25) -> None:
    print(f"sweeping aggression a in [0, {a_max}] (step {step})\n")
    lows: list[float] = []
    highs: list[float] = []
    n0 = sum(1 for s in scenarios if choice(s, 0.0) == s.desired)
    print(f"at a=0 (no bias = current AI): {n0}/{len(scenarios)} scenarios correct")
    print("  (the misses are exactly the passive failures we diagnosed)\n")
    for s in scenarios:
        flip, brk = correct_interval(s, a_max, step)
        if choice(s, 0.0) == s.desired and brk is None:
            verdict = "correct for ALL a (caution-gated or already right)"
        elif flip is not None and brk is None:
            verdict = f"needs a > {flip:.2f} (lower bound), never breaks"
            lows.append(flip)
        elif brk is not None:
            verdict = f"BREAKS at a >= {brk:.2f}  <-- upper bound / MISSING CAUTION SIGNAL"
            highs.append(brk)
            if flip is not None:
                lows.append(flip)
        else:
            verdict = "never correct in range"
        print(f"  {s.name:24s} desired={s.desired:22s} {verdict}")
    lo = max(lows) if lows else 0.0
    hi = min(highs) if highs else float("inf")
    print()
    if lo < hi:
        hi_s = "infinity" if hi == float("inf") else f"{hi:.2f}"
        print(f"FEASIBLE: a in ({lo:.2f}, {hi_s}) makes EVERY scenario correct.")
        if hi == float("inf"):
            print("  open-ended window => one scalar is robust; pick comfortably above the floor.")
        else:
            print(f"  finite window width {hi - lo:.2f} => tunable but watch the ceiling.")
    else:
        print(f"INFEASIBLE: need a > {lo:.2f} but also a < {hi:.2f}. "
              "A caution signal is missing (see the BREAKS line).")


def main() -> None:
    print("=" * 72)
    print("CORE scenarios (the diagnosis):")
    print("=" * 72)
    run(SCENARIOS)
    print()
    print("=" * 72)
    print("CORE + sentinel (demonstrates the missing-signal audit):")
    print("=" * 72)
    run((*SCENARIOS, SENTINEL))


if __name__ == "__main__":
    main()

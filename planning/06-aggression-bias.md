# Aggression bias with caution-reversion (SearchAI temperament)

## Why

Post-playtest diagnosis (see memory `project_ai_weakness_diagnosis`): the
SearchAI loses not because it fights badly but because it **won't commit to
anything whose payoff lands past its 12-turn horizon**. Two concrete failures,
one root cause:

- **Naval bootstrap** — once it owns what it can *see*, building a transport and
  sailing to an unseen enemy pays off far past the horizon, so the search never
  builds the navy (~6% projection; games stalemate).
- **Home consolidation under FORTIFIED** — cracking an artillery-defended neutral
  costs armies *now* and yields the city *later* (past horizon), so it sits on
  its capital (stuck at 1 city to t130 in a self-play trace).

Tactics are a rounding error next to this — you can't fail to take a fight you
never reach.

## The idea (one scalar)

Horizon-limited search is exactly what *intuition* is for: a prior that biases
the choice toward good actions the search can't afford to verify. We add a
single **aggression** scalar that leans plan selection toward bold plans —
*unless* a caution signal fires, in which case the bias **reverts to flat
(honest) evaluation and the smarter plan wins on merit.**

Decision rule per candidate plan `p`, with honest playout score `flat[p]`:

```
effective[p] = flat[p] + AGGRESSION   if  bold(p) and not caution(p)
             = flat[p]                otherwise
choice = argmax effective[p]      (then the existing SWITCH_MARGIN hysteresis)
```

Reversion (not veto, not dampening): caution simply *withdraws the thumb*. Bias
and honest search never fight — the bias only ever operates where the search is
blind.

**The principle:** be bold where you can't see (past horizon), disciplined where
you can (in horizon). Aggression fills uncertainty; evidence dissolves it.

Validated analytically before coding (`empire._bias_check`): with the caution
list complete, the feasible aggression window is open-ended, and every suicidal
plan is caution-gated so the horde cannot occur — straight from the arithmetic.

## What counts as bold

Structural, cheap (`_is_bold` in `ai.py`): a plan is bold iff it
- has an ASSAULT or INVADE objective (advance / amphibious), **or**
- builds a non-army unit (transport / patrol / fighter = projection & recon
  investment whose payoff is slow).

Hold, reserve, pure-defense, and scout-on-foot-building-armies are *not* bold —
they're the passive baseline the bias is meant to beat.

## What counts as caution — derived from the playout, not a hand list

The key simplification: we do **not** maintain a hand-written list of danger
signals (odds, escort, garrison, …). Instead caution is read off the honest
playout score itself:

```
floor   = max flat score among the NON-bold plans   (best "stand pat" option)
caution(p) = flat[p] < floor - CAUTION_TOL
```

A bold plan is cautioned exactly when the honest 12-turn simulation shows it
**losing ground versus doing nothing** — i.e. it incurs real, in-horizon costs
(armies die, a transport is sunk, home is recaptured). That is precisely the
*known-bad* region where the bias must stand down.

Why this is better than a signal list: **any in-horizon danger the playout can
simulate is caught automatically** — caution-completeness (the one thing the
analytical check said feasibility depends on) is guaranteed by construction for
all in-horizon dangers. The only dangers the playout "misses" are *past-horizon*
ones — which are exactly the ones we deliberately want to be bold about.

The two diagnosed failures pass cleanly:
- *build transport*: no in-horizon loss, scores ≈ floor → not cautioned → +bias
  → chosen. Bootstrap fixed.
- *gather-to-assault a neutral*: still massing, no loss yet, scores ≈ floor →
  +bias → chosen; once massed near the city the capture lands in-horizon and it
  scores far above floor → chosen on merit anyway. Consolidation fixed.
- *under-crewed / suicidal assault*: armies die in-horizon, scores ≪ floor →
  cautioned → bias stripped → loses to standing pat. Horde-proof.
- *unescorted transport into a known destroyer*: playout sinks it, scores ≪
  floor → cautioned → won't sail. *over-extend leaving home open*: playout shows
  recapture → cautioned → defends.

`CAUTION_TOL` is the real safety knob: the most in-horizon loss we'll back on
faith (≈ 2 armies at the default). `AGGRESSION` only needs to clear the
hysteresis margin to actually change a choice; the feasible window above that is
open-ended.

## Implementation & knobs

In `SearchAI._choose_plan`: after scoring all candidates, compute `floor`,
classify each plan bold/cautioned, add `AGGRESSION` to the eligible ones, and run
the existing argmax + hysteresis on the *effective* scores. Two constructor
params expose the temperament and make a clean A/B:

- `aggression: float` (default tuned below) — `0.0` recovers today's behavior
  exactly, so arena A/B is aggression-on vs aggression-off.
- `caution_tol: float` — the in-horizon loss tolerance.

Defaults are a starting guess (`AGGRESSION≈40`, `CAUTION_TOL≈20` in evaluator
units: city=100, army=10); the self-play arena calibrates them.

## Test plan

1. `empire._bias_check` — analytical feasibility (done; FEASIBLE, open-ended).
2. **Self-play A/B** — aggression-on SearchAI vs aggression-off SearchAI on the
   two-continent FORTIFIED map. Success = converts stalemates into wins and
   lifts projection above the ~6% floor (via `_naval_arena` / `_econ_trace`).
3. **Land-arena regression** (STANDARD) — the standing lesson
   (`project_naval_regressed_land`): any shared selection change must not
   regress the land game. Aggression-on must not drop the STANDARD win-rate.
4. Full unit-test suite.

## Future (not now)

This is step one of the two-step path. Step two: a thin **objective-director**
that supplies a richer set of in-horizon objectives than the current generator;
the *same* lean-with-reversion rule sits on top of it unchanged. The single
aggression scalar is the seed of a temperament vector and the natural hook for
the local-LLM personality vision (LLM sets the gut; search supplies competence).
Keep it one scalar until the data demands more.

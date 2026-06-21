# Portfolio director (concurrent plans for SearchAI)

## Progress log (newest first)

- **Set-piece decision tests + strict gate confirmed** (`fbd8fb7`). Methodology
  shift (playtester's call): assert the AI picks the predicted plan in hand-built
  positions, deterministically, in one decision — `tests/.../test_decision_
  scenarios.py`, 0.03s vs 70-min games. Resolved the land "regression" as RESOURCE
  DIVERSION (chasing the sea pre-contact on a land map), correctly prevented by the
  STRICT gate: credit naval goals only when `no land frontier left AND no enemy
  reachable by land`. Land A/B 50/50 under it.
- **Discovery via concurrent recon** (`8ae0d19`). SCOUT_SEA-tagged press-home +
  build-a-patrol plan → must-scout path off zero (built 12→50%, captured 0→12% in
  the probe). Naval base value moved onto generator-set goal tags.
- **Split-score** (`4f98526`). Horizon-free base value for the INVADE goal →
  revealed land-and-hold doubled (12→25%). The playout is demoted to priority.
- **Aggression bias FAILED** (`fef13f5`, reverted to off). Uniform scalar can't
  reorder bold-vs-bold; see `project_aggression_bias_failed`.

KNOWN REMAINING LIMITATION: the strict gate is correct but naval only *fires* when
home is fully explored, and home exploration / consolidation doesn't always
complete within a game — so projection is enabled in the right situations but not
yet reliably reached. Next lever: home-exploration completeness (use the set-piece
harness + a focused probe), then the stateful portfolio below.

## Why

Every prior attempt to make the AI project across water failed because the
SearchAI runs **one monolithic plan at a time** and judges everything by a
**12-turn playout**. Two consequences, both fatal to naval:

- **Mutual exclusivity.** "Press home" (production=ARMY) and "build a fleet"
  (production=TRANSPORT) are *separate* candidates; the search picks one. Naval
  can only happen by *abandoning* the land game, which always loses the in-horizon
  comparison. (Amphibious probe: even with the target revealed, it builds a fleet
  in only 25% of games — the break is at the very first link, commit-to-build.)
- **Horizon latency.** An overseas fist funds over 30+ turns (transport build +
  ferry) — past the horizon — so the playout can't see its cost *or* payoff. The
  uniform aggression bias failed because a flat bonus can't reorder bold-vs-bold;
  see `project_aggression_bias_failed`.

The fix is concurrency + a score that isn't hostage to the horizon.

## The model

A **portfolio of single-purpose plans**, actively maintained across turns.

### A plan member
`objective · phase · base_value · cost · priority · progress`

- **objective** — one purpose: capture city X / invade Y / defend Z / explore.
  Single-purpose (not today's multi-objective monolith), so each is
  kept/deferred/stopped and resourced independently.
- **phase** — `FUNDING` (assembling the force; draws *production*, gathers units
  toward a staging point) → `EXECUTING` (force assembled; spends *movement*,
  marches/sails/assaults). Phase tells the allocator which resource the plan
  draws and how sticky it is.
- **base_value** — the intrinsic, **horizon-free** worth of *achieving the goal*:
  capture → city worth (~100, ×strategic weight); explore → information worth;
  defend → threatened-city worth × threat probability. THIS is what lets a naval
  invasion get *started* despite zero in-horizon payoff.
- **cost** — situational **ETA to execute** (turns), the "simple but tactical"
  economy term, from distance fields we already build:
  ```
  cost ≈ funding_turns + delivery_turns
    funding_turns  = armies_needed × build_time / producing_cities
                     (+ 30/transport if hulls must be built for a sea-lift)
    delivery_turns = land: march_dist / army_speed
                     sea:  load(1) + ferry_dist / ship_speed + unload(1)
  ```
  Two continents falls out: local fist cheap; overseas fist costly (the 30/hull
  term). If a fleet ALREADY exists the sea term collapses — so a standing fleet
  is naturally rewarded (amortized logistics).
- **priority** — from the 12-turn playout: how promising/urgent progress is *now*.
  **Advisor, not judge** — it orders funding and flags stuck-vs-progressing; it
  does NOT set absolute worth (that's `base_value`). This is the demotion that
  breaks horizon latency.
- **progress** — funding fraction; drives sunk-cost stickiness (`_commitment_bonus`).

### Lifecycle each turn
1. **Re-validate / relevance** — "does the thing I started this for still matter?"
   (target exists, still unowned, reachable, still worth it). Horizon-independent.
   Fails → STOP.
2. **Propose edits** — START a new single-purpose plan (generator) when spare
   resources + good `base_value`/`cost`; STOP via **reallocate-then-rescore**
   (remove plan, return its resources, re-allocate, re-playout — did the whole
   portfolio improve?); promote/demote between **active** and **deferred**.
3. **Apply** only edits that improve the **whole-portfolio playout** by more than a
   hysteresis + sunk-cost margin. Deliberate abandonment only — a score dip never
   drops a plan; an irrelevant goal or a genuine reallocation win does.
4. **Allocate** units/production across the active set by `base_value`/`cost`/
   priority. Contention is priced *for free* by the shared playout (a unit given
   to A is unavailable to B in the same sim, so B's delay shows up).
5. **Execute** — followers run each active plan; unit moves are plan-driven.

### States
- **active** — resourced this turn.
- **deferred** — kept and valid, intentionally starved (contention → sequence,
  don't drop). The home of "an expensive plan waits its turn."
- **stopped** — deliberately killed (irrelevant goal, or reallocation win).

## Scoring: principled, split

- **Whole-portfolio playout** (one sim of all active objectives) — never
  independent per-plan scores (those thrash on contention). Edits judged by
  *marginal delta* with reallocation.
- **base_value (horizon-free) for worth; playout for priority; ETA for cost;
  stickiness against churn.** The playout grounds the in-horizon EXECUTING phase;
  the analytic value/cost handles the past-horizon FUNDING phase. Best of both —
  the old StrategicAI force-economy had to estimate *everything* analytically and
  got it wrong; here the playout carries execution.

## Build plan & guardrails

- **Step 1 — concurrency gate (cheap).** Before building the portfolio, prove the
  hypothesis: emit a *stateless* combined "press-home + build/stage-fleet"
  candidate and run the amphibious probe. If `built/held` jumps, concurrency is
  the fix. (Production splits per-city already: coastal→transport, inland→army.)
- **Step 2 — the stateful portfolio** above, once the gate confirms.
- **Regression guard** — land-brawl A/B (STANDARD); concurrency additions must not
  hurt the land game (standing lesson, `project_naval_regressed_land`).
- **Metric** — capability (`built/held` from `_amphib_probe`), NOT self-play
  win-rate; two equal AIs *should* be ~50/50 (per playtester).
- **Disposition** — this is the chosen architecture; a weak first result means
  TUNE, not revert (per playtester). Iterate the knobs.

# Implementation Plan

Phased build with explicit exit gates per phase and continuous quality gates throughout. Sequenced so that **core data structures and infrastructure land first**, game mechanics second, AI third, UI/LLM last. Each phase has a clear "done" definition; we don't move on until the gate is green.

Testing follows a **canary philosophy**: per-phase integration tests that exercise the phase's main entry points end-to-end, plus a small number of unit tests for things with non-obvious algorithms (combat distributions, pathfinding correctness, mapgen statistics). Goal: a failing test points at the right *phase*, and the stack trace points at the right *function*. We don't aim for line coverage; we aim for regression detectability.

---

## Continuous quality gates

Enforced on every commit (`make check` locally, same in CI when we add it). A phase is not "done" until these all pass *and* the phase's exit gate passes.

| Gate | Tool | Threshold |
|---|---|---|
| Lint + format | `ruff` (with `ruff format`) | Zero warnings |
| Type check | `pyright` (strict mode, or `mypy --strict`) | Zero errors on `src/empire/{core,contracts,combat,mapgen,pathfinding,ai}`. `tui` may relax. |
| Import discipline | `import-linter` | Dependency matrix from `04-class-hierarchy.md` §10. Any violation fails the build. |
| Test suite | `pytest` | All green. Suite runs in <30s for fast feedback. AI self-play tests carved out to `pytest -m slow`, run on gate-of-the-phase. |
| Coverage (advisory, not blocking) | `pytest-cov` | Track per-package; expect ≥70% on `core`/`combat`/`pathfinding`, ≥50% on `ai`, ≥30% on `tui`. Failure to *meet* is not a build break; sudden *drop* is reviewed. |

**Discipline rules** (enforced by lint config + import-linter, not just convention):
- No module-level mutable state (`ruff` rule).
- No top-level `random` calls — must use `Game.rng` (custom AST check or grep gate).
- `core` must not import `contracts`, `ai`, `combat`, `mapgen`, `pathfinding`, `events`, `persistence`, `tui` (import-linter).
- `Unit.coord` has no public setter (enforced at code-review; type system catches direct assignment via `@property`).

---

## Parallelization principles

Where work splits cleanly along file/interface boundaries, run agents in parallel (single message, multiple `Agent` tool calls). Where it doesn't, sequential is faster than untangling collisions.

**Safe to parallelize when:**
- Each agent owns a **disjoint set of files** (no two agents edit the same file).
- The **interface they implement is already defined** (the ABC/Protocol exists, locked).
- They share **no in-flight mutable state** (each agent writes its files + its tests; nothing in between).
- Their tests are runnable independently (no cross-agent fixture coupling).

**Not safe to parallelize:**
- Editing the same module / `__init__.py` / planning doc / `MEMORY.md`.
- Anything where agent B's design decisions depend on what agent A actually wrote (rather than what the spec says).
- Phases where the interface is itself the deliverable — design those single-threaded, *then* parallelize the implementers.

**Conventions for parallelized phases:**
- **One file per implementer.** E.g., each `Behavior` lives in its own file (`behaviors/army_assault.py`), not as a class in a shared `behaviors.py`. Same for `Goal` subclasses and TUI widgets. Avoids `__init__.py`/import collisions and lets each agent's diff be reviewed independently.
- **Stitching is a separate step.** After parallel agents return, a single sequential pass updates the package's `__init__.py` exports, registers new types in any registries, and runs the full gate suite. Agents do not edit shared exports.
- **Brief each agent like a colleague.** Hand them: the exact file paths they own, the ABC/Protocol signature, the spec section in `03`/`04`, the test fixtures available (`NullController`, golden maps, etc.), and the canary tests they must add. Include "do not edit X, Y, Z" lines for shared files.
- **Each agent runs its own gates before reporting.** `make check` on its files, its tests green. The stitching step re-runs gates on the whole repo.
- **Worktrees only when needed.** For agents writing distinct files in distinct modules, regular parallel execution is fine. Reach for `isolation: "worktree"` only when there's genuine risk of interference (e.g., two agents both touching the same package's `__init__.py`).

**Parallelism callouts appear per-phase below.** Phases without a callout are sequential by nature.

---

## Phase 0 — Skeleton & gates (1-2 sessions)

**Deliverable:** Empty package tree matching `04-class-hierarchy.md` §1, with `pyproject.toml`, dev deps, `make check` working, all gates green on empty code.

**Includes:**
- `pyproject.toml` (Python 3.11+, deps: textual, anthropic [opt-extra], llama-cpp-python [opt-extra]; dev: pytest, pytest-cov, ruff, pyright, import-linter).
- `Makefile` targets: `check`, `test`, `lint`, `typecheck`, `imports`, `format`.
- `.importlinter` config encoding the dependency matrix.
- `src/empire/__main__.py` shim that does nothing yet.
- `tests/` mirror of `src/` with one trivial test per package so pytest discovers everything.

**Parallelism:** Modest. Three independent files can land in parallel: `pyproject.toml`, `Makefile`, `.importlinter`. One stitching agent then creates the empty package tree and trivial tests. Net: ~30% wall-clock savings; not transformative since the phase is small.

**Exit gate:** `make check` is green. CI hookable if/when we add one.

---

## Phase 1 — Core value types (1 session)

**Deliverable:** `empire.core` value types — no behavior, no game logic.

**Modules:** `coord.py`, `identity.py`, and `ruleset.py`.

- `Coord`, `Direction` (with offsets), `TerrainKind`, distance helpers.
- `UnitId`, `CityId`, `PlayerId`, `TaskForceId`, `GoalId` as `NewType[int]`.
- `RuleSet` dataclass + `STANDARD` preset populated with values from `01-game-rules-spec.md`.

**Canary tests:**
- `Coord` arithmetic round-trips (`step` then reverse-step returns original).
- `Direction.offsets()` produces all 8 unique vectors.
- `STANDARD` preset loads and has the expected name/values for a handful of spot-checked fields.

**Exit gate:** Gates green. ~20-30 LOC of tests.

---

## Phase 2 — Core entities, no mechanics (2-3 sessions)

**Deliverable:** `Tile`, `Map`, `Unit` hierarchy + registry, `City` + `ProductionState`, `Player`, `ViewMap` — *positional and structural only*. No movement rules, no combat, no production tick yet.

- `Map.place_unit / move_unit / remove_unit` maintain the spatial index but do NOT validate against `RuleSet`. Rule validation lands in Phase 8.
- `Unit` subclasses with class-level attrs chosen per `01-game-rules-spec.md` §2. `Unit.coord` is read-only via `@property`; `_set_coord` is called only by `Map`.
- `ProductionState` has the methods stubbed but no tick logic yet.
- `ViewMap` exposes `update_from_scan`, `seen`, `render_char` — implementation can be naive (full visibility) for now; fog logic lands in Phase 8.

**Canary tests:**
- Spatial-index consistency: after `N` random placements/moves/removes, `Map.units_at(c)` matches a brute-force scan of all units.
- Every `UnitKind` resolves to a concrete `Unit` subclass via the registry, and every subclass declares every required class attr (introspection test).
- Unit class-attr values match a small golden table committed to `tests/fixtures/unit_attributes.json` (catches typos against our spec).

**Parallelism:** **Significant.** Once the `Unit` ABC is locked (1 sequential session — must be agreed first), the rest splits cleanly:
- Agent A: `Tile` + `City` + `ProductionState` (all in `core`, no AI/combat deps).
- Agent B: `Unit` subclasses, one file per kind under `core/units/` (`army.py`, `fighter.py`, ..., `satellite.py`) + `core/units/__init__.py` registry. *Inside this slot,* sub-agents can do one unit per agent in parallel, since each unit file is independent.
- Agent C: `Player` + `ViewMap` (naive full-visibility impl for now).
- Agent D: `Map` (depends on Tile + Unit ABC; can start in parallel with A/B/C as long as it imports only the ABC, not concrete units).

Stitching step: wire `core/__init__.py` exports, run the property test for spatial-index consistency across the assembled pieces.

Net: ~3-4× wall-clock vs sequential.

**Exit gate:** Gates green. Spatial-index property test runs ≥1000 random ops without desync.

---

## Phase 3 — Contracts package (1 session)

**Deliverable:** `empire.contracts` — the AI-facing vocabulary.

- `AIController` Protocol with `plan_turn`, `revise_move`, `name`.
- `WorldView` as live-filtered view over `Map` + `ViewMap` (no copying).
- `TurnPlan`, `UnitMove`, `ProductionOrder`, `UnitSentry` as frozen dataclasses.
- `Surprise` tagged union (`EnemySighted`, `PathBlocked`, `TargetLost`, `EscortLost`, `TerrainImpassable`).
- `NullController` — a controller that always returns an empty `TurnPlan`. Useful as a test fixture and as proof the Protocol works.

**Canary tests:**
- `WorldView` only exposes tiles in `ViewMap.visible | ViewMap.remembered` — assert that a hidden tile is not reachable through the view.
- `NullController` satisfies the Protocol at type-check time (catch by `pyright --strict`).

**Exit gate:** Gates green. `NullController` is a registered fixture in `conftest.py`.

---

## Phase 4 — Game aggregate, TurnManager skeleton, persistence v0 (2 sessions)

**Deliverable:** `Game` exists; can be constructed, saved to JSON, loaded back, and ticked through empty turn phases.

- `Game.__init__` takes `RuleSet`, `Map`, players, controllers dict, seeded `Random`.
- `TurnManager` with phase methods present but bodies empty (or just event emissions): `production`, `movement_user`, `movement_ai`, `endgame_check`.
- `SaveManager` + `V1Serializer` implementing **topological load** per `04` §8. Schema version field present.
- `EventBus` (Phase 4a) — minimal pub/sub for `TurnAdvancedEvent`, `UnitMovedEvent`, etc. Subscribers are optional; bus is plumbed in early to avoid retrofitting.

**Canary tests:**
- **Save/load round-trip:** generate a small game (handcrafted, ~5 units, 3 cities), save, load, deep-compare. RNG state survives.
- **Topological-load doesn't half-link:** introspect loaded entities for `None`/`Id`-typed reference fields that should be resolved objects.
- **Load rejects future schema:** a payload with `schema_version=999` raises a clear error.
- Empty turn phases run without crashing for 10 turns.

**Parallelism:** Moderate. Three streams after `Game`'s public surface is sketched (~30 min of single-threaded design):
- Agent A: `Game` + `TurnManager` skeletons.
- Agent B: `SaveManager` + `V1Serializer` (topological load).
- Agent C: `EventBus` + event dataclasses.

Stitching: wire `EventBus` into `TurnManager`'s phase methods (one small edit). Net: ~2-3× wall-clock.

**Exit gate:** Gates green. A `Game` survives a round-trip and 10 empty turns.

---

## Phase 5 — Mapgen (2 sessions)

**Deliverable:** `HeightFieldMapGenerator` per `04-class-hierarchy.md` §5. Three `MapProfile` presets (`SMALL`, `STANDARD`, `LARGE`).

**Canary tests (golden-summary, not golden-map):**
- For each `(profile, seed)` pair in a fixed table (~6 entries), generate the map and assert: cell count by terrain ±2%, city count exactly matches, all cities are on land, no two cities within `min_city_distance`, ≥1 connected landmass.
- `regen_land` fallback fires on a known pathological seed (verify via counter).
- Determinism: same seed → identical map. Different seed → different map (probabilistic but high-confidence).

**Exit gate:** Gates green. Golden table committed under `tests/fixtures/mapgen_summaries/`.

---

## Phase 6 — Combat (2 sessions)

**Deliverable:** `CombatResolver` (classic per-blow attrition) + `CombatEvaluator` (pure EV prediction).

**Canary tests:**
- **Distribution test** (the big one): for each (attacker_kind, defender_kind) pair in `vms-empire.6:218-228`, run 10,000 seeded duels through `CombatResolver` and assert the win-rate matches the manpage's published probability within ±2 percentage points.
- `CombatEvaluator.win_probability` is pure (same inputs → same output, no RNG dependency).
- Edge cases: zero-HP units, satellites (cannot be attacked), self-attack (rejected).

**Parallelism:** Clean 2-way split.
- Agent A: `CombatResolver` + its distribution test (the big one, expensive to develop).
- Agent B: `CombatEvaluator` + its purity test.

Different files, no shared state. Net: ~2× wall-clock.

**Exit gate:** Gates green. Distribution test passes for all pairs.

---

## Phase 7 — Pathfinding (2 sessions)

**Deliverable:** `Pathfinder` ABC, `PathRequest`, `PathCostProfile`, `BFSPathfinder` (classic perimeter), `AStarPathfinder` (with optional danger weighting).

**Canary tests:**
- **Handcrafted-map suite:** ~6 small maps (10x10 or so) with known shortest paths. Each pathfinder must find the expected path for each map.
- **Danger weighting:** on a map with a known "threat zone," the danger-weighted profile produces a longer-but-safer path than the unweighted version.
- **Unreachable:** returns `None`, not a crash.
- **Fog respect:** when `view` has unexplored cells, the pathfinder treats them per `PathCostProfile.unknown_cost`.

**Parallelism:** Clean 2-way split after the `Pathfinder` ABC + `PathRequest` + `PathCostProfile` are defined (small single-threaded session).
- Agent A: `BFSPathfinder` (classic perimeter expansion) + its handcrafted-map tests.
- Agent B: `AStarPathfinder` (with optional danger weighting) + its tests.

Shared handcrafted-map fixtures should be authored as the third deliverable of the ABC step, so both agents reuse them. Net: ~2× wall-clock.

**Exit gate:** Gates green. All handcrafted-map cases pass for both pathfinders.

---

## Phase 8 — Game mechanics: rules wired into turn phases (3-4 sessions)

This is the first phase where the game *plays* anything. Lands movement validation, fog updates, production ticking, combat triggering, and the endgame check — all gated by `RuleSet`.

**Deliverable:**
- `Map.move_unit` now validates against `RuleSet` (terrain legal? destination passable? unit has moves left?).
- `ViewMap.update_from_scan` correctly tracks visibility from the moved unit's `scan_range`.
- `ProductionPhase` ticks every city and emits units when ready.
- Movement triggers combat via `CombatResolver` when entering an enemy-occupied tile.
- City capture rules (army-only, `irand(2)` per `RuleSet`).
- `Game.is_over` + `winner` check.

**Canary tests:**
- **Seeded single-turn scenarios:** ~5 handcrafted scenarios with deterministic expected outcomes (unit moves there, combat happens, attacker wins, city captured). One assertion per scenario covers the whole turn.
- **Production tick:** city with `Army` queued and `build_time-1` work, after `tick`, has the new `Army` on the tile.
- **Fog update:** unit moves; previously-invisible tile within `scan_range` is now in `ViewMap.visible`.
- **Hotseat playable:** a test harness drives a 20-turn game with both sides as `NullController` (no moves; just production + fog updates) and asserts no crashes, no negative state.

**Exit gate:** Gates green. Hotseat-playable smoke test passes.

---

## Phase 9 — BaselineAI (3-4 sessions)

**Deliverable:** `BaselineAI` per `03-ai-design.md` §2 — a greedy per-unit AI. Per-unit decision methods on `BaselineTactical`, weight tables, BFS pathfinding via `BFSPathfinder` from Phase 7.

**Approach:** For each owned unit, evaluate nearby objectives (capturable cities, attackable enemies, unexplored frontier, return-to-base for damaged units) with a weighted scoring function, then move toward the highest-scoring objective. No coordination across units. Weights are our design choices, tuned by self-play.

**Canary tests:**
- **Self-play stability:** 20 seeded `BaselineAI` vs `BaselineAI` games (seeds 0-19) run to completion without crashing. Game length and final-score distribution are recorded as a golden — sudden shifts are reviewed.
- **Decision spot-checks:** canned `WorldView` situations with intuitively-correct decisions. The spec's full list (army adjacent to undefended neutral city → moves into city; damaged unit far from any friendly city → routes toward repair; fighter low on fuel → returns to nearest friendly carrier or city) only partially applies in the Phase-9 *ARMY-only* shipped slice:
  - "Army adjacent to neutral city → captures" ✔ covered.
  - "Damaged unit returns to repair" — Army has `max_hits=1`, so it's binary alive/dead with no damaged state to route from. This scenario re-enters scope when Destroyer/Battleship/Carrier decisions are wired (those units don't currently produce under BaselineAI's ARMY-only default build).
  - "Fighter low on fuel returns to carrier" — Fighter doesn't currently produce or get a decision method. Re-enters scope when Fighter is wired.
- **Interface adapter:** `BaselineAI.revise_move()` returns a sensible step (re-runs per-unit decision); does not crash on any `Surprise` variant.

**Parallelism:** Significant. Each unit kind gets its own decision method (`army_decide`, `fighter_decide`, `transport_decide`, etc.) — these become methods on `BaselineTactical`, one per kind. After the dispatch skeleton + initial weight tables + objective-scoring helpers are written single-threaded:
- One agent per unit-kind decision function (one file each under `ai/baseline/decisions/`), in parallel. Each agent designs the unit's heuristic from the spec, picks initial weights, adds a spot-check test.
- Parallel to all of the above: one agent tunes the shared objective-scoring helpers if needed (or reuses `BFSPathfinder` directly).

Stitching: register decisions in the dispatch map, run self-play, tune weights iteratively. Net: ~4-6× wall-clock on the per-unit work, which is the bulk of the phase.

**Exit gate:** Gates green. 20-game self-play completes; outcome distribution within tolerance of recorded golden (or, on first run, *establishes* the golden).

---

## Phase 10 — Engine validation milestone (1 session)

No new code — a validation gate. Confirms that Phases 1-9 produced a sound engine.

**Run:**
- 50 seeded `BaselineAI` vs `BaselineAI` games to completion. Assertions:
  - No crashes.
  - All games terminate within a turn cap (e.g., 500 turns) — non-termination is a bug.
  - Win-rate roughly balanced if continent quality is balanced (both sides win ~30-70% range).
  - Saves taken at random turns load back identically.

**Exit gate:** All 50 games pass. This is the green light to start building `StrategicAI`.

---

## Phase 10.5 — Minimal playable TUI (2-3 sessions)

Inserted out of plan order because Phase 10's gate run made it clear we'd benefit from *actually playing* well before Phase 17. Goal: human-vs-`BaselineAI` end-to-end in a Textual app, plus an AI-vs-AI viewer for watching self-play (which would have helped diagnose the stalemate question this very phase produced).

**Deliverable:**
- `empire.tui` package: `EmpireApp`, `TitleScreen` → `PlayScreen` → `GameOverScreen`. Three regions on `PlayScreen`: `MapWidget` (character grid, live in-place updates), `StatusBar` (turn + selected unit + production), `LogPanel` (scrolling event stream).
- `HumanController` satisfies `AIController` by replaying a `TurnPlan` the TUI assembles before each `run_turn()`.
- `empire setup` shared helper: capital assignment + `Game` construction, used by both TUI and validation harness.
- CLI: `empire play-tui` (human vs `BaselineAI`) and `empire viewer` (AI vs AI on a tick).

**Input model.** Default = numpad 1-9 for 8-directional movement (5 = stay); vi-keys (`hjkl`+`yubn`) as a toggle. Letter commands from classic Empire (`m`=move, `s`=sentry, `p`=production, `n`/`N`=next/prev unit, `c`=center). `?` opens a help overlay. `F2`=save, `F3`=load (existing `SaveManager`). No hotseat — see [feedback-no-hotseat-network-mp]; MP arrives as networking later.

**Fog of war.** Map renders from the human's `ViewMap`: visible = bright terrain + live units; remembered = dim terrain (no live units); unseen = blank. Status/log never leak enemy state outside the view.

**Cuts vs. full Phase 17.** No snapshot tests yet. No CityList/UnitList side panels (info goes in StatusBar). One trivial `ProductionModal`. No keybinding customization beyond the numpad/vi toggle.

**Canary tests:**
- **Headless smoke:** Boot app via Textual pilot, advance 5 turns with scripted input, exit cleanly. No crashes.
- **Save/load round-trip:** Save mid-game, quit, reload, confirm state matches.
- **Viewer determinism:** Same seed → same final map (compares against `validate` output for one seed).

**Exit gate:** Gates green. A human player can start, take a turn, save, quit, reload, take another turn, and reach a win/loss screen. Viewer mode renders a 50-turn self-play without dropping frames.

---

## Phase 10.6 — Persistent unit orders (DONE)

Follow-on from 10.5 hands-on play. Two of classic Empire's most-used commands need cross-turn state on `Unit` that we haven't modeled yet:

- **Set direction (Army).** Player gives an Army a fixed cardinal/diagonal heading. The Army walks that way one cell per turn automatically, with no further input, until interrupted by: (a) reaching the coast (Army can't enter water), (b) an enemy unit becoming visible nearby, (c) a city or own unit blocking the cell, (d) the player explicitly waking the unit. Lets the player set a long-range march and stop micromanaging it.
- **Set patrol path (naval).** Player draws a sequence of cells (one or more turns of travel). Unit walks the path; on reaching the last cell, either stops (one-shot) or reverses (true patrol — design choice TBD).
- **Persistent sentry (any).** Already conceptually in `OrderKind.SENTRY`; needs to actually be enforced by the engine: a sentried unit doesn't accept auto-cycle and stays put each turn until awakened.
  - **Wake triggers (auto):** an enemy unit enters this unit's scan range; an own unit's combat happens adjacent to it; a previously-unseen city becomes visible nearby. All cause the unit to *un-sentry* and re-enter the next turn's auto-cycle so the player notices.
  - **Wake trigger (manual):** the player selects the unit and gives any non-sentry order.
  - **Anti-rule:** a surprise NEVER causes a unit to enter sentry. Half-finished paths just stop for the rest of the current turn; the unit is still free to move next turn under planner control. Auto-sentry-on-surprise would be exactly backwards from the desired behavior — the player would lose visibility on the very units they most need to react with.

**Engine work:**
- Add `Unit.standing_order: StandingOrder | None`. `StandingOrder` is a value-type union: `Heading(direction)`, `PatrolPath(cells, reverse_on_end)`, `Sentry()`.
- New `TurnManager` step (between production and the controller's plan): for each owned unit with a standing order, apply one step (via the same `execute_unit_path` machinery), then check the interruption rule. Interrupted units have their order cleared and are flagged for the next player turn's auto-cycle.
- `TurnPlan` gets `set_orders: tuple[SetOrder, ...]` so the controller can set/clear standing orders declaratively.

**TUI work:**
- New command (`d` for "direction"): with a unit selected, the next direction key sets a heading and immediately marks the unit handled. Persists across turns.
- New command (`g` for "go to"): cursor designates a target; engine builds a BFS path; unit walks it over multiple turns.
- Standing order shown in the StatusBar when a unit is selected ("heading: NE" / "patrol: 5 cells" / "sentry").

**Exit gate:** Gates green. A human can set an Army's heading, end-turn several times, and watch it march autonomously; the unit stops on coast/enemy/city/own-unit collision and re-enters auto-cycle.

---

> **Deferred Phase-8 mechanics (10.7–10.10).** Phase 8 wired movement,
> combat, capture, production, fog, and the endgame check, but several
> per-unit mechanics from the rules spec were left as explicit
> placeholders (see `core/game.py::_end_of_round`: *"satellite lifetime
> decay, fighter fuel attrition, repair logic — none of these are wired in
> Phase 8"*). Cargo (§2.2/§3.4) was never given a phase at all. These gaps
> surfaced during 10.5/10.6 hands-on play: **BaselineAI self-play games do
> not terminate** — the dominant side captures most cities but cannot cross
> water to take the last enemy cities, because Transports can't actually
> carry Armies. The win condition (§8, zero cities) is therefore
> unreachable on the usual multi-continent map. The phases below close the
> spec gaps, ordered by how much they block winnability. They must land
> before the StrategicAI phases (13–14) that *assume* cargo exists
> (`TaskForce` force-matching on "(3 armies, 1 transport)";
> `TransportFerryBehavior` = "load … sail … unload").

## Phase 10.7 — Transport & Carrier cargo (load/unload) (DONE)

**The winnability-critical gap.** Implements spec §2.2 (Cargo) and §3.4
(Loading and unloading). Today the `capacity` ClassVar (Transport=6,
Carrier=8) and `Unit.effective_capacity()` exist as groundwork, but there
is no cargo *state* and no load/unload logic — sea carriers sail empty and
armies are stranded on their home continent.

**Engine work:**
- Cargo state on `Unit`. A carrier (Transport/Carrier) holds an ordered
  list of aboard `UnitId`s; an aboard unit knows its carrier. Aboard units
  are removed from the `Map` cell index entirely (spec §2.2: "not on the
  map independently while aboard") and travel with the carrier. Keep the
  mutation single-owner per [[feedback-minimize-mutation]]: `Map` owns
  placement; loading/unloading goes through `Map` methods, not direct field
  pokes.
- **Load:** a friendly Army stepping into a friendly Transport's cell (and
  Fighter → Carrier) is treated as a load, not a collision-reject, when the
  carrier has free `effective_capacity()`. Costs the cargo unit one
  movement point; the unit comes off the map and onto the carrier.
- **Unload:** an aboard unit steps from the carrier's cell to an adjacent
  legal cell, spending a movement point. Spec §3.4: a unit cannot load and
  unload in the *same* turn — enforce with a `loaded_this_turn` guard
  (cleared at end-of-round), not just by move-budget accounting (a fast
  unit could otherwise round-trip).
- Capacity respects damage scaling via `effective_capacity()`; a carrier
  taking hits while over capacity sheds nothing automatically (design
  choice — note it), but can't load past the reduced cap.
- Aboard units contribute no independent vision; the carrier's
  `scan_range` covers them. Aboard units don't fight independently — if the
  carrier is destroyed in combat, its cargo is lost with it (spec-consistent
  with §4.5 "units stationed inside are destroyed"; note the choice).
- Save/load: cargo lists round-trip; topological load resolves aboard-unit
  references after their carrier exists.
- Honor the existing `transport_escort_required_for_unload` ruleset flag
  (§10): when set, an unload is illegal unless a friendly combat ship is
  adjacent. (STANDARD leaves it off — wire the check, default-off.)

**TUI work:**
- Stepping a selected Army onto a friendly Transport loads it (with a
  status-bar confirmation: "loaded onto TRANSPORT#7 — 3/6"). Stepping an
  aboard unit off (select it from the carrier, direction key) unloads.
- A carrier's StatusBar line shows cargo count ("cargo: 3/6"); a way to
  cycle/select aboard units to give them unload orders.

**Canary tests:**
- **Load/unload round-trip:** army loads onto adjacent-water transport,
  transport sails 2 cells, army unloads onto a different shore — ends on
  the far landmass. Same for Fighter ↔ Carrier.
- **Capacity:** the 7th army can't board a capacity-6 transport; a
  damaged carrier's reduced `effective_capacity()` is enforced.
- **No same-turn round-trip:** a freshly-loaded unit cannot unload until
  next turn even with movement points left.
- **Carrier sinks with cargo:** destroying a loaded transport in combat
  removes the aboard armies from the game.
- **Save/load:** a mid-voyage game (loaded transport at sea) round-trips
  byte-identically.

**Exit gate:** Gates green. A human can build a transport, load an army,
ferry it to another continent, unload, and capture a city there — i.e.
**actually win a two-continent game**. A BaselineAI self-play game on a
two-continent map terminates (this is the real signal that the win
condition is reachable; BaselineAI's *use* of transports is refined in
Phase 14, but the mechanic must let a game end here).

---

## Phase 10.8 — Deferred unit lifecycle: fuel, orbit, repair (DONE)

The trio explicitly placeholdered in `_end_of_round`. None block winning,
but all three are spec mechanics that currently no-op, so Fighters,
Satellites, and damaged units don't behave per the rules.

**Engine work:**
- **Fighter fuel (§3.5).** Decrement `range` by 1 per cell moved. At
  end-of-turn, a Fighter with `range == 0` not on a friendly City or
  friendly Carrier is lost (publish a `UnitRemovedEvent`). Landing on a
  friendly City/Carrier resets `range` to `base_range`. (Couples with 10.7:
  "on a friendly Carrier" means aboard or co-located per the carrier rules.)
- **Satellite lifecycle (§2.4).** Autonomous orbital movement — one cell
  per turn in its launch direction, bouncing off the map edge (default per
  spec). Runs in a dedicated end-of-round step (satellites aren't
  controller-driven). Decrement lifetime each round; at 0 the satellite
  deorbits and is removed. Vision covers every cell it passes through this
  round, not just the final cell.
- **Repair (§2.3).** A unit that begins and ends a turn stationary in a
  friendly city regenerates 1 HP (capped at `max_hits`).

**Canary tests:**
- Fighter flown past its range with no friendly field in reach is removed;
  one that reaches a friendly city the same turn survives and refuels.
- A launched satellite moves deterministically, bounces at the edge,
  reveals its trajectory, and deorbits exactly at its lifetime.
- A 1-HP unit parked in a friendly city is at 2 HP next turn; a unit that
  moved that turn does not repair.

**Exit gate:** Gates green.

---

## Phase 10.9 — City default-order enforcement (DONE)

The data model is already complete and round-trips through save/load:
`OrderKind` (`SENTRY` / `MOVE_TO` / `ATTACK_NEAREST_ENEMY`),
`City.default_orders`, and `City.default_order_for()` (§5.3). **Nothing
consumes it** — a newly produced unit is never handed its city's default
order. SENTRY happens to be the implicit no-op, so the gap is invisible
until a player sets `MOVE_TO`/`ATTACK_NEAREST_ENEMY` and nothing happens.

**Engine work:**
- On unit production, look up the city's `default_order_for(kind)` and
  translate it into the unit's initial standing order (reusing Phase 10.6
  machinery): `MOVE_TO(coord)` → a `PatrolPath` (BFS at production time);
  `SENTRY` → `Sentry()`; `ATTACK_NEAREST_ENEMY` → resolved by the
  controller/behavior layer (record intent; deterministic targeting lands
  with the AI phases).

**TUI work:**
- The production modal lets the player set a city's per-kind default order.

**Canary test:** a city with `MOVE_TO(target)` default produces an army
that, left untouched, walks toward the target over subsequent turns.

**Exit gate:** Gates green.

---

## Phase 10.10 — Capital-selection eligibility (DONE)

`setup.build_game` currently just assigns the two largest continents as
capitals — a documented stand-in. Spec §9.2 and
[[feedback-capital-eligibility]] require enforcing eligibility *at the
selection layer*: a capital-eligible continent has **≥3 cities** (capital
+ ≥2 capture targets) and **≥1 ocean-coastal city** (so transports can be
built/hosted). If fewer than `num_players` continents qualify, reject the
map and regenerate. This is partly a winnability concern too — a capital
stranded on a one-city island can neither expand nor (pre-10.7) leave.

**Canary tests:**
- A handcrafted map whose only large continent has 2 cities is rejected;
  one with a qualifying continent is accepted.
- Selected capitals always sit on continents meeting both criteria.
- Regeneration terminates (bounded retry count, surfaced if exceeded).

**Exit gate:** Gates green. `build_game` deterministically yields capitals
on distinct eligible continents for every profile/seed it's used with, and
regeneration is bounded.

> **Correction (implementation note).** An earlier draft claimed the
> validation harness would show a non-zero BaselineAI self-play termination
> rate once 10.7 + 10.10 landed. That's wrong: 10.10 *guarantees* capitals
> sit on separate landmasses, and BaselineAI does not yet build or use
> transports (that's Phase 14's `TransportFerryBehavior`), so neither side
> can reach the other across water. Self-play termination therefore still
> waits on Phase 14. The cargo *mechanic* and the human ferry-to-win path
> are proven in 10.7; 10.10's own gate is capital placement + bounded
> regeneration, verified by its canary tests.

---

## Phase 10.11 — City support facilities + ground bombardment (DONE)

Inserted out of plan order (done after Phase 11 chronologically), driven by
playtesting the TUI: produced units were silently auto-sentried and never
offered. Fixing that opened up a coherent batch of core-mechanic work, all
spec'd in `01-game-rules-spec.md` §5.1/§5.3/§5.4 and §4.6.

> **Shipped.**
> - **Produced units await orders** (engine `apply_default_order`): an unset
>   city default leaves no standing order, so the unit enters the order cycle
>   instead of being auto-sentried (§5.3).
> - **City as a unit-support facility** (§5.4): units are produced *on* the
>   city cell; a turn-end disband phase (`disband_overcrowded_city_units`,
>   wired as `TurnManager._disband_phase` before production) enforces per-kind
>   limits — army 0 (armies can't garrison; a conquering army is consumed as
>   the city's defence), fighter 8 (airbase), sea 1 (dry-dock, repairs via the
>   existing §2.3 rule, can't load/unload). Armies can't move into a friendly
>   city. The disband scraps empty ships before loaded ones so it never routes
>   cargo through the combat-sink path.
> - **Ground bombardment** (§4.6): `execute_bombardment` — BB/DD/Patrol,
>   ≥2 HP, one adjacent salvo/turn; army/fighter destroyed outright (ship −1
>   HP), a docked ship resolved as ordinary combat. TUI `f` key to fire.
>   Patrol `max_hits` bumped 1 → 2 (matching the Submarine) so it can fire
>   once before needing repair.

**Tests:** `test_city_support.py`, `test_bombardment.py`, plus updated
`test_default_orders.py` / `test_engine.py`. Gates green.

---

## Phase 11 — IntelService (DONE)

**Deliverable:** `IntelService` produces `IntelReport` with `Threats`, `Opportunities`, `ChokePoints`, `Theaters` from a `WorldView`.

> **Shipped.** `ai/strategic/intel/` package: `report.py` (frozen artifact
> types), `threats.py`, `opportunities.py`, `chokepoints.py`, `theaters.py`,
> and `service.py` assembling the report. Each computation is a pure function
> over `WorldView`; `IntelService` is stateless, so the same view yields a
> deeply-equal (and hashable) `IntelReport` — the purity gate. Notes:
> - **Threats:** projection horizon `PROJECTION_TURNS = 3` (a speed-1 army
>   three cells out registers as a threat, matching the design scenario);
>   reach is a bounded BFS over legal terrain, treating unobserved cells as
>   traversable (conservative for the defender). Combat power = strength ×
>   remaining hits.
> - **Opportunities:** neutral/enemy cities + currently-visible enemy units,
>   scored `value × probability / (1 + distance)`. No friendly assets → no
>   opportunities (nothing can act). Stale (remembered-only) enemies are not
>   attack targets.
> - **ChokePoints:** purely local one-cell strait/isthmus pinch test over
>   *known* terrain only — never fires through fog.
> - **Theaters:** flood-fill of *known* land/city cells into 8-connected
>   components (one-ring fringe added for adjacent water/unexplored, but
>   inference is not propagated, so two known islands always give exactly two
>   theaters). State from city ownership: both → CONTESTED, one side → that
>   side's core, neutral-only → CONTESTED, empty → UNEXPLORED.
>
> Tests under `tests/empire/ai/strategic/intel/` (19 canaries) share scenario
> builders via `_world.py`; this introduced the first `from tests...` import,
> so `pyright`'s tests execution-environment gained `extraPaths = ["."]` to
> mirror the runtime path pytest already provides.

**Canary tests:**
- **Purity:** same `WorldView` → same `IntelReport` (deep equality).
- **Canned scenarios:** ~4 handcrafted views with known expected outputs ("enemy army 3 cells from my city" → `Threats` non-empty and includes that city as at-risk; "neutral city visible and reachable" → `Opportunities` non-empty).
- **Theater detection:** on a two-island map, exactly 2 `Theaters` are produced.

**Parallelism:** Clean 4-way split. The four components are independent computations over `WorldView`:
- Agent A: `Threats` (per-enemy projected reach + at-risk friendly assets).
- Agent B: `Opportunities` (capturable cities, exposed enemy fleets).
- Agent C: `ChokePoints` (terrain bottleneck detection).
- Agent D: `Theaters` (flood-fill regional classification).

Each in its own file under `ai/strategic/intel/` + its own canary test. `IntelService` assembles the report from all four. Net: ~3-4× wall-clock.

**Exit gate:** Gates green.

---

## Phase 12 — FeasibilityOracle + Goals + DeterministicStrategist (DONE)

**Deliverable:** `FeasibilityOracle`, full `Goal` hierarchy, `DeterministicStrategist`.

> **Shipped.**
> - `ai/strategic/goals/` — `base.py` (`Goal` ABC + `ForceComposition` +
>   `ResourceBudget` + `GoalKind`) and `concrete.py` (the six goals:
>   `CaptureCityGoal`, `DefendCityGoal`, `ExploreAreaGoal`, `ProjectPowerGoal`,
>   `DenyContinentGoal`, `BuildForcesGoal`). Each goal is a frozen value type
>   with a `progress_signal(view)` and `to_dict`/`goal_from_dict` round-trip.
> - `ai/strategic/feasibility.py` — `FeasibilityOracle`: stateless,
>   view-per-call `can_assemble` (production-window capacity), `defensible`
>   (local force vs threat power), `reachable` (bounded terrain-aware BFS).
> - `ai/strategic/strategist.py` — `Strategist` ABC + `DeterministicStrategist`
>   (defend → consolidate → expand → project → explore → build, then
>   rank-and-cap). Every force-requiring goal is gated through the oracle, so
>   infeasible goals are never emitted. Pure given (intel, view).
> - `ai/strategic/memory.py` — minimal `AIMemory` (grows in Phases 13+/16).
>
> **Notes:** reconciled the two design docs — the oracle is stateless and
> takes `view` per call (§7), so `Strategist.plan(intel, memory, view)` carries
> the view and the strategist holds the oracle. The `strategic/__init__` is
> kept import-free to avoid a package-init cycle; import pieces from their
> submodules. Built inline rather than via the planned parallel goal-agents.

**Exit gate:** Gates green — `make check`, 535 passed (+20 canaries:
goal serialization/progress, oracle purity + correctness, strategist
defend/capture/no-infeasible scenarios).

**Canary tests:**
- **Oracle purity:** `can_assemble`, `defensible`, `reachable` all deterministic on the same `WorldView`.
- **Strategist canned scenarios:** ~4 handcrafted intel reports → expected goal types emitted (under-threat city → `DefendCityGoal` for it; surplus production + visible neutral → `CaptureCityGoal`).
- **No infeasible goals:** in canned scenarios where assembly is impossible, the strategist does not emit the goal (oracle filter works).

**Parallelism:** Moderate. After the `Goal` ABC and `FeasibilityOracle` interface are pinned single-threaded:
- One agent per concrete `Goal` subclass, in parallel: `CaptureCityGoal`, `DefendCityGoal`, `ExploreAreaGoal`, `ProjectPowerGoal`, `DenyContinentGoal`, `BuildForcesGoal`. Each agent writes one file under `ai/strategic/goals/` + its `progress_signal()` method + a trivial construction/serialization test.
- Parallel to the goal work: agent for `FeasibilityOracle` implementation.
- Sequential after: `DeterministicStrategist` (consumes goals + oracle, needs both ready).

Net: ~3× wall-clock on the goals; strategist is unavoidably sequential at the end.

**Exit gate:** Gates green.

---

## Phase 13 — OperationalPlanner + TaskForce + reaper (DONE)

**Deliverable:** `OperationalPlanner` consumes goals, assembles `TaskForce`s, emits production orders, reaps terminal-state TFs at end-of-turn.

> **Shipped** in `ai/strategic/operational.py`: `Role`, `TaskForceState`,
> mutable `TaskForce` (with `to_dict`/`from_dict`), and `OperationalPlanner`.
> `plan(goals, view, memory) -> OperationalPlan` reaps terminal forces past a
> one-turn grace, prunes dead units + promotes survivors to COMPLETE/DISBANDED,
> then assembles new forces for unserved goals from the idle pool (lowest-id
> first, consumed so two forces never claim a unit), filling roles by goal
> type. Forming forces emit `ProductionOrder`s for the shortfall. Active TFs
> live in `AIMemory.task_forces` across turns.
>
> **Deviations (resolve-and-note):** a `TaskForce` stores unit *ids*, not live
> `Unit` refs (§3.3's `units: list[Unit]`) — cleaner to serialize, no stale
> refs; the tactical executor resolves ids against the map. The "game with
> active TFs round-trips" canary is covered at the `TaskForce` level here
> (JSON round-trip test); the full game-with-AIMemory round-trip lands with
> AIMemory serialization in Phase 16.

**Exit gate:** Gates green — `make check`, 542 passed (+7: force-matching,
short-force production request, continuity, COMPLETE/DISBANDED reaping,
goal-completion, TF JSON round-trip).

**Canary tests:**
- **Force matching:** given a goal requiring (3 armies, 1 transport) and an idle pool that contains them, the resulting `TaskForce` includes exactly those units.
- **Reaper:** a `COMPLETE` TF is dropped after one turn of grace; a `DISBANDED` TF likewise.
- **Save/load:** a game with active TFs round-trips through JSON without loss.

**Exit gate:** Gates green.

---

## Phase 14 — TacticalExecutor + Behaviors (DONE)

**Deliverable:** `Behavior` ABC, `Unit.behavior_for(role)` inversion, concrete behaviors (`ArmyAssault`, `ArmyGarrison`, `TransportFerry`, `FighterStrike`, `FighterScout`, `ShipEscort`, `ShipPatrol`, `SubAmbush`), `DefaultBehavior` (sentry no-op).

> **Shipped** in `ai/strategic/behaviors/`: `Behavior` ABC + `DefaultBehavior`
> + shared movement helpers (`base.py`); army / air / naval behavior modules;
> a `behavior_for(kind, role)` registry; and `TacticalExecutor`
> (`tactical.py`) — `plan_moves(forces, view)` (one `UnitMove` per board unit,
> idle units sentry) + `revise_move` for mid-turn surprises. Movement reuses
> the Phase-7 `BFSPathfinder` with the ARMY/SEA/AIR cost profiles; fighters are
> fuel-aware (peel home to refuel), scouts seek the frontier, escorts close on
> their transport.
>
> **Deviation (resolve-and-note):** the design's `Unit.behavior_for(role)`
> can't live on `Unit` — `core` may not depend on `ai` (import-linter). The
> registry instead lives in the AI layer keyed by `UnitKind`; the per-kind
> modularity the design wanted is preserved (each kind's behaviors live in
> their own module). Built inline rather than via the planned parallel
> behavior-agents. `revise` defaults to re-planning a fresh step, which is
> total over every `Surprise` variant.

**Exit gate:** Gates green — `make check`, 598 passed (+56: full (kind, role)
coverage, revise-no-crash over every surprise × every behavior, and a
scenario per behavior).

**Canary tests:**
- **Coverage:** for every `(UnitKind, Role)` pair the strategist could emit, `unit.behavior_for(role)` returns *some* `Behavior` (possibly `DefaultBehavior`).
- **No crashes on `revise`:** every behavior handles every `Surprise` variant without crashing — `DefaultBehavior` is the fallback.
- **One scenario per major behavior:** handcrafted situation where the behavior's `next_move` produces a clearly-correct step (e.g., `ArmyAssaultBehavior` with an undefended target city 2 cells away steps toward the city).

**Parallelism:** **Largest win in the whole project.** Behaviors are wholly independent — each consumes `(unit, view, force)`, emits a `UnitMove`, knows nothing about other behaviors. After the `Behavior` ABC + `Role` enum + `DefaultBehavior` (no-op sentry) are pinned single-threaded:
- One agent per behavior, in parallel: `ArmyAssaultBehavior`, `ArmyGarrisonBehavior`, `TransportFerryBehavior`, `FighterStrikeBehavior`, `FighterScoutBehavior`, `ShipEscortBehavior`, `ShipPatrolBehavior`, `SubAmbushBehavior`. Each in its own file under `ai/strategic/behaviors/` + its canary scenario test + its `revise()` implementation against every `Surprise` variant.
- Parallel to all of the above: agent for `TacticalExecutor` (dispatch + iteration; consumes the `Behavior` Protocol, doesn't care about concrete classes).
- Parallel to all of the above: agent extending each `Unit` subclass's `behavior_for(role)` mapping (one PR per unit kind, touching only that unit's file — no conflicts).

Stitching: ensure every `(UnitKind, Role)` pair the strategist can emit resolves to a behavior (DefaultBehavior catches the rest). Net: ~8× wall-clock on the behavior work, which dominates the phase.

**Exit gate:** Gates green.

---

## Phase 15 — StrategicAI integration & validation milestone (INTEGRATION DONE; VALIDATION BLOCKED)

**Deliverable:** `StrategicAI` wired together; all four layers active.

> **Integration shipped** in `ai/strategic/ai.py`: `StrategicAI` (an
> `AIController`) runs intel → strategist → operational → tactical each turn,
> persisting goals + task forces in `AIMemory` (one instance per player).
> `AIMemory.to_dict`/`from_dict` added; mid-game AI state round-trips (Phase-15
> save/load criterion met at the AIMemory level). Operational continuity fixed
> to match forces to goals by *content signature*, not the strategist's
> per-turn goal id. Gate green (604 passed).
>
> **Expansion bugs fixed; one feature gap blocks the win-rate gate.**
>
> Two bugs found in the first validation run are fixed:
> - **No exploration → no expansion (was: 1 city forever).** Idle units now
>   default to **hunt mode** (`HuntBehavior`: push to the nearest frontier)
>   instead of sentrying — so freshly-built armies leave the capital,
>   exploration reveals neutral cities, and `CaptureCityGoal`s follow.
> - **`BuildForcesGoal` parked armies on the capital** (its op-target was the
>   AI's own city) where the §5.4 garrison rule disbanded them. The strategist
>   no longer emits it; hunt mode occupies idle units instead.
> - **Goal realism:** expand now emits a `CaptureCityGoal` only when the target
>   is land-reachable; an across-water target gets a transport-borne
>   `ProjectPowerGoal` instead.
>
> Re-measured (SMALL, 8 seeds, 300-turn cap): StrategicAI now expands to **4.4
> cities avg, at parity with BaselineAI (4.4)** — up from 1. But **still 0/8
> terminated**: capitals are on separate continents (10.10) and there is still
> no way for the AI to *unload* across water (`TurnPlan` can't express an
> amphibious unload — only the automatic load-on-step). So the strategic edge
> (coordinated task forces, defence, power projection) never engages, and the
> ≥60% win-rate gate cannot be assessed.
>
> **Remaining decision for the user:** build AI amphibious unload now
> (TurnPlan + engine + ferry behavior — required for any cross-continent game
> to terminate), and/or add a single-continent validation profile to measure
> land-combat quality where the AIs can actually fight.

**Validation:**
- 50 seeded `StrategicAI` vs `BaselineAI` games at NORMAL difficulty.
- **Quality gate: `StrategicAI` win-rate ≥ 60%.** If not, the "smarter" design isn't smarter — investigate before proceeding. Per-difficulty: EASY ~50%, NORMAL ≥60%, HARD ≥70%.
- Save/load of mid-game `StrategicAI` state (including `AIMemory`, active TFs, goals) round-trips.

**Exit gate:** Win-rate threshold met; saves round-trip.

---

## Phase 15.5 — Force-economy redesign (StrategicAI must beat BaselineAI)

**Why:** the land-brawl cross-check (both capitals on one continent, swapped
sides) showed `StrategicAI` losing **0/10** to `BaselineAI`. Root cause =
**fragmentation**: ~7 goals, ~3 armies, 1 unit crewed per force → smeared
across the map while BaselineAI's "every army → nearest high-value target"
emergently concentrated and won the §5.4 attrition war. The layered AI loses
*because* of its breadth. Fix the force economy before any further AI work
(this gates the LLM path too — the LLM only swaps the strategist).

**Design (decided with the user; full rationale in `03-ai-design.md` §3.2 and
memory `project_force_economy_redesign`):**
1. **Top-K value discriminator, bounded by the force budget** (not a fixed
   cap — multi-front when crewable). `value ≈ base(enemy_city > neutral_city >
   explore; + defense urgency) × proximity_to_existing_force × likelihood_of_
   success`. An enemy army hittable within strike range overrides passive
   defence. Drop unwinnable objectives.
2. **Goals fund goals:** an unfunded top objective → a build/stage goal that
   concentrates production and **rallies idle units to its rendezvous** instead
   of hunting; the force strikes as a fist at critical mass.
3. **Concentration:** operational crews a force with more than the minimum and
   pulls idle units into a forming force.
4. **Simple reactive triage:** winnable defence → mass defenders sized to the
   threat; unwinnable → let the city go, concentrate elsewhere.
5. **Progress-scaled commitment (anti-oscillation):** incumbent goals get a
   bonus growing with progress + type (attack-build sticky, hunting fluid);
   switch only when a new goal clearly beats the bonus-adjusted incumbent. Uses
   `AIMemory`.
6. **Empirical tuning:** force-size buffer, budget/K bound, commitment margin,
   success threshold tuned against the committed land-brawl **arena**
   (`empire._arena`: swapped StrategicAI vs BaselineAI + binomial test), not
   guessed.

**Reuses:** `combat.evaluator.CombatEvaluator` (win probability), task-force
FORMING/EN_ROUTE + rendezvous, `FeasibilityOracle`, `AIMemory`.

**Exit gate:** StrategicAI beats BaselineAI in the arena with statistical
significance (one-sided binomial p < 0.05), no regression on the SMALL
separate-continent profile, all `make check` gates green.

**Results so far (arena: 24 land-brawl games/variant, swapped sides):**

| variant | StrategicAI win-rate (decided) | note |
|---|---|---|
| v1 original layered AI | **0%** (0/10) | fragmented; armies smeared 1-per-goal |
| **v2 concentration** (committed) | **39%** (9/23) | fists not tokens; the decisive fix |
| v3 + threat-sized defence + success filter | 0% (0/6) | turtled; filter stopped all enemy-city captures |
| v4 + per-target assault size + cost budget | 29% (2/7) | fast 1-army neutral grabs just re-fragmented |
| v5 + aggressive hunt (seek neutrals/enemies) | 35% (8/23) | more decisive games, no win-rate gain |
| v8 1-army shields + aggressive army-cost offense | 23% (5/22) | cheap neutral fronts re-fragment (same failure as v4); §5.4 tax punishes distribution hardest. Reverted. |

| v6 Shield/Sustain/Dagger doctrine | 17% (1/6) | discipline → too passive; can't take cities |

**Rule × AI matrix** (the decisive finding) — same arena, 24 games each:

| | v2 (no posture) | v7 (posture: EXPAND/PRESS/CONTEST/CONSOLIDATE) |
|---|---|---|
| **§5.4 current rule** (capture disbands the army) | 39% | **45.5%** ↑ |
| **softened rule** (conqueror garrisons; no disband tax) | **52%** | **13.6%** ↓↓ |

**Conclusion.** Two coupled findings:
1. **The §5.4 capture-disband tax handicaps strategy by ~13 points** — the
   *same* v2 AI goes 39% → 52% when a conqueror garrisons instead of being
   disbanded. The rule turns the game into a body-throwing attrition race that
   rewards the baseline's blunt greed. (Tested by monkeypatching the engine's
   land garrison limit 0→1 in the arena; not a committed change.)
2. **The optimal strategy is coupled to the rule.** Posture (`AIMemory`
   trajectory + EXPAND/PRESS/CONTEST/CONSOLIDATE) *helps* under §5.4 (39→45.5,
   best variant on the real rule) because CONSOLIDATE-turtling survives an
   attrition grind — but is *catastrophic* under the fair rule (52→13.6),
   because turtling when temporarily behind cedes the economy in a game that
   rewards continued expansion.

So: **decide the rule first, then tune the AI to it.** v2 (committed) is the
robust baseline across both rules; v6/v7 are recorded but not committed (v6 too
passive; v7 rule-brittle). The exit gate (>60% vs baseline) is unmet and won't
be cleanly reachable until §5.4 is settled — a game-design call for the user.

---

## Phase 15.6 — City artillery (FortifiedCities preset)

**Why:** the user's call after the §5.4 fork — rather than soften the capture
tax (which makes the horde *stronger*: a surviving conqueror is fresh fodder),
make **cities fight back** so capture is a gauntlet, not a coin flip. Full
rationale in spec §4.7 and memory `project_city_artillery`.

**Design (decided with the user, locked):** cities have **no HP**; the only
defense is **ranged single-target artillery** — Chebyshev range 2, one shot per
city per round, flat ~50% hit chance, a hit = 1 HP (instant kill vs army/
fighter), and a ~50% chance to **pin** the target (land/air lose their move
this round, naval halved). All cities — owned and neutral — fire once in an
**opening barrage before any unit moves** (symmetric, so no first-mover edge),
plus reactive overwatch on units that *enter* range mid-round. Capture is
**deterministic on arrival** under this preset (the gauntlet was the cost).
Ships as the `FORTIFIED_CITIES` preset; `Classic`/`STANDARD` stay inert. The
one-shot/round cadence is the anti-horde invariant: trickle dies on the
approach, a concentrated assault punches through.

**Engine (committed b087e68):** `RuleSet.city_artillery_range/_hit_prob/
_pin_prob` + `FORTIFIED_CITIES` preset; `City.artillery_ready` + `Unit.pinned`
transient flags (`Unit.moves_this_turn` honors the pin); engine
`execute_city_artillery` / `reactive_city_fire` / `_fire_artillery` /
`reset_city_artillery` / `clear_movement_pins` + `ArtilleryOutcome`/
`ArtilleryResult`; `CityFiredEvent`; `TurnManager._opening_barrage_phase` +
reactive hook in `_apply_turn_plan`; per-round re-arm + pin-clear in
`run_round`. Arena gains a `--fortified` flag. 16 unit tests.

**Final design (after measurement) — two balance refinements on the raw rule:**
- **Opening barrage, not per-player proactive fire.** All cities (every player's
  + neutral) fire once *before any unit moves*. Per-player proactive fire gave a
  large first-mover edge (P1 win-rate 71%); the symmetric pre-move barrage
  removes it. Reactive fire still covers units that *enter* range mid-round.
- **Pinning, as a chance (`city_artillery_pin_prob`, default 0.5).** A fired-upon
  unit is pinned with that probability (hit or miss): land/air lose their move
  this round, naval halved. Absolute pinning made cities uncapturable (0
  captures, every game stalled); 0.5 keeps them capturable while stalling an
  unsupported attacker.

**Result (160-game arena A/B, same v2 AI, committed b087e68):**

| metric | STANDARD (control) | FORTIFIED_CITIES |
|---|---|---|
| StrategicAI win-rate (decided) | 31.6% (24/76) | 20.0% (12/60) |
| first-mover win-rate (fairness) | 38% | 52% |
| unfinished (hit turn cap) | 5% | 25% |

**Verdict ("it works"):** the horde no longer wins lopsided and the first-mover
bias is solved (52% ≈ fair). But the StrategicAI still *loses* under FORTIFIED
(20%) and 25% of games stall, because v2 has **no combined-arms capability** — it
bounces lone armies off defended cities exactly like the horde does. The rule is
feature-complete on *defense*; the *attack* side is Phase 15.7.

---

## Phase 15.7 — Combined-arms campaign AI (exploit FortifiedCities)

**Why:** 15.6 made cities defensible; now the StrategicAI must learn to crack
them, and the 25% unfinished games must collapse. Full doctrine in memory
`project_campaign_doctrine`.

**Design (locked with user):** one odds estimator —
`P(success) = combat_odds × arrival_discount × trend_factor × surprise_bonus`,
evaluated at force *arrival* time (formation time included). The decision is
**not IF to attack, but WHERE and WHEN** — so the estimator governs the
operational *launch* decision, NOT strategist goal-emission. The strategist
stays greedy (emit every reachable enemy/neutral city, = v2); operational sizes
the force and times the strike. Single continent only in v1.

**False start (recorded for the lesson).** The first build put the estimator at
strategist goal-emission — it gated each `CaptureCityGoal` behind `P ≥ 0.60`.
Controlled diagnosis (same arena, only strategist code differs): v2 emits 1.33
capture goals/plan (704 enemy-city assaults / 4 seeds); the gated version 0.77
(268) — a **62% cut to attacking enemy cities, the move that wins**. Intel gives
enemy cities a fixed 0.6 prior (`opportunities.py`); `arrival_discount` dragged
that under 0.60, so the strategist stopped proposing enemy-city captures. In a
war game that's fatal (measured: STANDARD 31.6%→21.8%, FORTIFIED 20%→16.4%). The
estimator module + tests are sound; only the *call site* was wrong. Reverted the
strategist to v2.

**Key realisation:** v2 *already* concentrates — a `CaptureCityGoal` requisitions
a fist (`ATTACK_FORCE_SIZE=3`) and the task force stays FORMING until full, only
then promotes to EN_ROUTE and marches. "Mass before commit" is already v2. What
the estimator genuinely *adds* at the launch gate: (1) a **commit deadline** (v2
forms forever if it can't reach the fist — a stalemate source); (2) **abandon a
hopeless objective** (free the force from a doomed siege); (3) **air-superiority
gating** (step 2). All three live at FORMING→EN_ROUTE, none at goal-emission.

**Step 1 — operational commitment loop (DONE, except sticky-swap deferred).**
`_required_composition` made ruleset-aware via `_fortified_fist(rules)` =
`range+1` armies under FortifiedCities (the city fires ≤`range` times on the
approach, so `range+1` guarantees one lands), default `ATTACK_FORCE_SIZE`
otherwise — a no-op at current values (range 2 → 3 = ATTACK_FORCE_SIZE) but
makes the concentration target explicit and rule-driven. The commitment loop in
operational (`_campaign_p` calls the estimator; capture goals only, everything
else returns P=1.0):
- **Launch gate (wired).** One unified gate in `_reinforce`: FORMING→EN_ROUTE
  when *turns_forming ≥ PREP_DEADLINE (8)* OR *(full fist AND P ≥ 0.60)*. To put
  every force through the same gate, `_assemble` now always births a force
  FORMING — even a fresh full fist must clear the threshold (or the deadline)
  the same turn before it marches. A full fist below threshold *holds* (the
  board may improve) until the deadline forces it out. `field_odds` comes from
  the canonical capture priors (neutral 1.0 / enemy 0.6) read off the view —
  same number `Opportunity.success_probability` carries, no new intel coupling.
- **Abandon + cooldown (wired).** `_abandon_hopeless` scraps any capture force
  whose *best-case* P (full fist, `formation_turns=0`) `< ABANDON_FLOOR (0.20)`
  — a property of target+board, independent of how this muster is going, so a
  doomed siege is dropped no matter how it formed. The freed units redirect
  (next turn, after the reap) and the target city is recorded in
  `AIMemory.abandoned_targets`; while it sits in `ABANDON_COOLDOWN (6)` the
  planner refuses to re-assemble a force for it, so the greedy strategist can't
  thrash assemble→abandon. Absolute and challenger-independent.
- **Sticky swap — DEFERRED (fast-follow).** The doctrine's anti-oscillation
  margin (`swap_margin` in `campaign.py`, written + unit-tested) redirects an
  assembled force between rival targets. But today the force↔goal binding is
  *static* (signature-matched, one force per goal), so a force never switches
  targets — there is nothing to oscillate yet. Swap only earns its keep once we
  add dynamic retargeting, and the metric Step 1 must move (unfinished-rate) is
  driven by the deadline + abandon, not swap. Held until the arena shows actual
  target thrash. `swap_margin` stays in place, unused, for that follow-up.

Constants live in `campaign.py`, arena-tuned. Surprise inference still deferred
(`any_unit_spotted=True`, no bonus — conservative).

**Step 1 measured (FORTIFIED, 20 games, cap 250).** S=4 B=12, **unfinished
25%→20%** — the anti-stalemate metric moved as intended. Win-rate among decided
still 25% (4/16): under FortifiedCities the smart AI is *expected* to lose until
combined-arms air (step 2) clears the field — Step 1 is the launch-discipline
prerequisite, not the win. Sample is small/noisy; treat as directional.

**Step 2 (building) — surplus employment by home-theater confidence.** Step 1
stopped armies dying on cities (§5.4) but converted the waste: measured **13.4
idle armies/turn, peak 35** in a losing FORTIFIED game (diagnostic, since
deleted) — surplus that survives but only ad-hoc *Hunts*. Root cause is a
demand/supply gap: the strategist emits only Defend + Capture goals (consolidate
& explore are deliberately *off* — they scattered force, see `strategist.py`),
front count is capped at `own_cities + 2`, and each capture force wants a fixed
fist — so an army-rich / target-poor side has nowhere to put the surplus. The
fix is a **situational surplus router** keyed off the *home theater's*
confidence (the `Theater` containing our cities; intel already tags state +
per-landmass city ids):

- **CONTESTED** (known targets include real fights — not all high-P): soak
  surplus into **over-strength fists** (extra armies = gauntlet redundancy: lose
  some to artillery on the approach, still land the fist) and a **mobile reserve**
  parked *adjacent* to held cities (not on them — §5.4). Raises per-front demand.
- **THIN BUT FOGGY** (known target list short / all high-P, BUT the home landmass
  still has a fog frontier): a **concentrated home-exploration campaign** toward
  the frontier — NOT the old scatter-to-every-cell explore goal. Self-reinforcing:
  exploring either turns up new cities (new campaigns → more demand) or earns the
  confidence to look elsewhere. This is the key "at least something" floor.
- **SECURED** (short/high-P list AND home landmass fully revealed — no frontier):
  project overseas. **DEFERRED to a later phase** — needs working naval
  projection (transports/escorts/amphibious) AND a *multi-continent* arena; the
  land-brawl harness is single-continent by construction and cannot validate it.

Confidence = (few known non-friendly targets, all high-P) **discounted by**
unexplored area — a short *known* list is only provisional ("done with what we
see"); the discount is whether the home landmass still has a frontier
(`nearest_frontier`). Build + tune the CONTESTED and THIN-BUT-FOGGY branches in
the existing arena (measure idle armies/turn ↓ alongside win/unfinished); stub
SECURED as the explicit hand-off to the naval phase.

**Concentration (prerequisite to Step 3, in progress).** Telemetry (the new
`_telemetry` instrument) showed the real bottleneck is not air but that *the fist
never forms*: ~75% of assaults launch as a single army, 74–90% deadline-forced,
mean forming-time pinned at the 8-turn deadline. Front heuristic landed (the
user's design): **soft grabs** (undefended neutrals — none under FortifiedCities)
take 1 army and launch on sight, liberal count; **defended assaults** (enemy +
all FortifiedCities cities) take the full fist, capped at `own_armies // fist`
with NO `+1` (the spare front just starved and deadline-dribbled), nearest-first
+ coastal tiebreak; dropped the blunt `own_cities + 2` global trim; reverted the
3a-1 fighter production (it stole ⅓ of output from already-thin fists). Result:
front count fell (STANDARD 7.4→4.4, FORTIFIED 12.8→10.1 launches/game) but the
trickle PERSISTS (FORTIFIED fists still ~1.8 armies, 82% deadline-forced) and
win-rate is flat. New root cause: **production spreads** across every forming
force (incl. soft grabs) + loss-replacement, so no one fist fills in 8 turns.
Next: **focus production** — fund the top-priority fist to full before the next,
and stop spending production on soft grabs (walk-ins use spare armies, not the
factory); split telemetry by soft/defended to see fist sizes directly.

**Step 3 (building) — combined arms (the win lever).** 100-game baseline after
2a: STANDARD 28.7% win / 6% unfinished (clean losses), FORTIFIED 34.2% win /
**27% unfinished** (fortified-city grind). Over-strength can't finish a defended
city; the approach attrition is too high. Air is the breakthrough. Grounded in
the engine (verified, see memory `project_combined_arms`):

- **Artillery shoots ARMIES first** (`_artillery_danger`). Fighters can't bait or
  soak the gauntlet — the gun always targets the army about to capture. So the
  gauntlet stays solved by over-strength *concentration* (2a), not by air.
- **BaselineAI is a pure army horde** (no fighters) — there is no enemy air to
  "win superiority" over. Fighters' real jobs vs this opponent: **(1) kill the
  enemy's mobile defenders** so the over-strength fist that survives the gauntlet
  wins the melee at the city; **(2) scout** (speed 8, scan 5, fuel 20) the
  approach and the wider map. Only armies capture.
- **Limited production → slower combined-arms build** (one unit/city/turn). So
  produced fighters must **scout immediately** (the "unsupported exploration
  during the build window" the design hinges on), never idle waiting for the fist.

Increments: **3a-1** coordinated fighter production (a fighter quota, not all
armies) + route idle/forming fighters to fuel-safe scouting (`FighterScout`, not
generic Hunt which ignores fuel) — gets air into the economy + recon during the
build window. **3a-2** fighters join the capture force and strike the mobile
defenders near the objective ahead of the army landing (the breakthrough; the
core behavior rewrite — `FighterStrike` today just flies at the city and
suicides into artillery). Measure win/unfinished on 100-game parallel samples
after each. Enemy-air-superiority gating, surprise inference, and the
SECURED/overseas naval branch remain later (no enemy air today; naval needs a
multi-continent arena). Target: FORTIFIED win-rate decisively > 50%.

**SUPERSEDED (2026-06-11).** Steps 2 (surplus router) and 3 (combined-arms air,
3a-1/3a-2) and the deferred sticky swap are superseded by **Phase 15.8** — the
user chose plan-space search over further hand-doctrine iteration. Committed
work stands (launch gate, over-strength, front heuristic, telemetry);
`StrategicAI` freezes as a difficulty tier and arena opponent.

---

## Phase 15.8 — `SearchAI`: plan-space lookahead (supersedes 15.7 Steps 2–3)

**Why:** the 15.5–15.7 pattern — hand-design a doctrine, measure, discover the
failure, revert — plateaued around 30% vs the horde. The decisive move (design
rationale in `03-ai-design.md` §9, decided with the user): search over candidate
*plans* at runtime, scoring each by cloning the game and simulating H turns
forward with the **literal `BaselineAI` as the opponent model**. Turn-based
budget makes it affordable; owning the opponent script makes it accurate.
Horizon kept short (H ≈ 10–20) — deeper simulation diverges into noise; a
static evaluator carries long-term judgment.

**Measured feasibility (2026-06-11):** clone via schema-v1 round-trip ≈ 1 ms;
93% of playout cost is `BaselineAI`'s per-(army×objective) A*; BFS distance
fields make playouts ~10× cheaper, behavior-preserving. K=32 candidates × 15
turns ≈ 3 s/turn single-core after the fix. Found: `from_dict` doesn't wire a
`CombatResolver` — the playout clone helper must.

**Step 0 — forward model + speed (DONE, commit 2ab6bd8).** The bit-for-bit
replay test flushed out three latent bugs: schema v1 never serialized the
city-artillery rule fields (a FORTIFIED save silently loaded as STANDARD!),
`Game._next_unit_id` was re-derived on load (reissuing dead units' ids,
which can collide with remembered `UnitSnapshot` intel), and
`ViewMap.update_from_scan` inserted remembered tiles in set-iteration order
(memory-history-dependent — unreproducible by save/clone; now canonical
(y, x)). BaselineAI planning rebuilt around one `DistanceField` flood per
unit (distances provably equal `find_path` steps; equal-length route
tie-breaks may differ). Measured: mid-game 15-turn playout 1075ms → 132ms
(~8×); clone ~1ms. Arena re-baseline (100 games each): STANDARD 35.2%
(recorded pre-change: 28.7%), FORTIFIED 31.4% (34.2%) — within noise, and
the arena itself now runs a 100-game sample in ~60s.
- `PlayoutModel`: clone (serializer round-trip), wire `CombatResolver`, attach
  controllers. Unit test: cloned game plays N turns bit-identically to the
  original under the same controllers/RNG.
- BFS distance-field objective scoring in `BaselineAI` replacing per-pair A*
  (same distances; path tie-breaks may differ). Re-run the 100-game arena
  baseline to confirm win-rates are statistically unchanged — this also speeds
  the arena itself.
- Commit the forward-model benchmark as an instrument (alongside `_telemetry`).
- Exit: mid-game 15-turn playout ≤ ~150 ms single-core.

**Step 1 — evaluator + plan follower.**
- `Evaluator`: city differential, production in flight, Lanchester-weighted
  material (concentration counts), frontier/intel terms. Unit-test ordering on
  hand-built positions (winning > drawn > losing; massed > scattered at equal
  count).
- `Plan` (typed, small) + `PlanFollower` executing a candidate inside a playout
  by composing the existing tactical `Behaviors`. The follower is also the
  real-turn executor (search commits the argmax plan's first turn).

**Step 2 — `SearchAI` v1 (greedy plan search).**
- `CandidateGenerator`: K ≈ 8–32 plausible plans over choice points — per-city
  {assault-now-with-N, mass, defend, ignore}, surplus {scout, reserve},
  production {armies, fighters, split}. Anchored heuristically, not a
  cross-product.
- Rolling horizon: re-plan each turn; evaluate candidates by playout from the
  **searcher's view** (fog-honest: unknown = empty, enemy at last-known +
  opponent-model projection); commit argmax.
- Tune H and K against the arena. `fog_cheat` run as a diagnostic upper bound
  only (never committed as the player-facing default).
- **Exit gate:** beats `BaselineAI` with one-sided binomial p < 0.05 on BOTH
  rulesets; target ≥ 60% decided win-rate on FORTIFIED (the original Phase 15
  bar). Unfinished-rate at or below StrategicAI's.

**Step 2 results (DONE — commits 59cf21e, fdf5ea5; validated 2026-06-11).**
First full validation of untuned v1 (H=12, 3 samples, K≈4-12): STANDARD
51.0% (p=0.46, 0 unfinished), FORTIFIED 45.1% (p=0.84, 18% unfinished) —
both the best ever recorded (StrategicAI's best: 35.2% / 31.4%) but short
of the gate. Event telemetry found the leak: **71% of armies killed by city
artillery died with no friendly within 3 cells** — the v2 trickle, reborn
inside `PlanFollower`; after staging alone was added, deaths moved to
*transit* through other cities' rings (neutral cities fire at everyone).
Three follower disciplines fixed it (fdf5ea5): hostile-ring masking in the
movement grids (per-target grids keep the target's own ring open; raw-grid
fallback), mass-at-the-ring, and storm-on-near-quorum with a latch so late
joiners can't freeze a fist. Certified at 100 games per ruleset:

| ruleset | SearchAI v2 | best StrategicAI ever | gate |
|---|---|---|---|
| FORTIFIED | **63.3%** (57-33), p=0.0074, 10% unfinished | 31.4% | **MET** (≥60%, p<0.05) |
| STANDARD | 51.0% (51-49), p=0.46, 0% unfinished | 35.2% | parity; not significant |

Reading: STANDARD's §5.4 capture-disband attrition race structurally favors
the horde's tempo (the 15.5 rule matrix put ~52% as the apparent ceiling
even with rule softening), so parity there plus decisive superiority under
FORTIFIED — the design-forward ruleset — is the intended shape of the win.

Open follow-ups out of Step 2: (1) **stall autopsy** — are the 10% FORTIFIED
cap-outs genuinely dead mutual-fortress positions or unpressed winnable
endgames? (2) **no-progress rule** as a future preset design question (a
chess-style no-capture clock vs. material adjudication at the cap) — raised
by the user as a musing, undecided, pairs with the autopsy's answer;
(3) whether STANDARD stays a tuning target beyond parity.

**Close-out pass (FINAL — commits 625698f, 7d9ef16; certified 2026-06-12).**
The stall autopsy answered (1): 3 of 4 cap-outs were *crushing endgames that
never finished*, not dead positions. A plan trace found three compounding
causes, each fixed: surplus armies froze as statues once the board was
explored (no frontier → idle; now they rally to the active assault's ring —
artillery rulesets only, a loitering reserve is wasted tempo without a
gauntlet, measured -5.5pp on STANDARD); near-tied playout scores flipped
assault strength 3↔5 every turn, reshuffling fist membership (SearchAI now
keeps the incumbent plan unless a challenger wins by SWITCH_MARGIN); and
the generator lacked an overwhelming-force close-out (new all-out candidate:
a fist per known target). Same-seed autopsy: 18 wins / 3 unfinished (was
16/5), with a 165-turn losing turtle converting to a win at t113.

**Final certified results (100 games per sample):**

| ruleset | SearchAI (final) | best StrategicAI ever | gate |
|---|---|---|---|
| FORTIFIED | **64.1%** (59-33), p=0.0044, 8% unfinished | 31.4% | **MET** |
| STANDARD | 49.5% (49-50), ≤1% unfinished | 35.2% | parity |

STANDARD across three certified samples (51.0 / 45.5 / 49.5, n≈100 each)
pools to 48.7% — statistical parity with the horde; per the noise protocol,
±3pp differences between samples are not chased.

*(Post-certification rules change, 2026-06-12: capture now consumes the
conquering army at capture time — spec §4.5 rewrite during playtesting,
commits 4942a33 + a56eee0 — removing the corpse-shield a captured city
briefly enjoyed. 40-game FORTIFIED check: 65.7%, p=0.045, 12.5% unfinished —
indistinguishable from the certified 64.1%; balance-neutral, stands.)* **Phase 15.8's decisive
question is answered: plan-space search beats the horde where the rules
reward thought (FORTIFIED, the design-forward ruleset) and matches it in
the pure attrition race.** Next per the user: self-play (SearchAI vs
SearchAI — needs nothing new mechanically; the arena generalizes), then
human play (TUI integration as an opponent option + difficulty tiers from
search knobs: K, H, samples, SWITCH_MARGIN).

**Self-play feel check (12 games, FORTIFIED, 2026-06-12).** Pacing healthy:
lengths 89-250, 1/12 stalled, long probing standoffs then a decisive break;
comebacks rare (2/11) — snowbally but genre-consistent. Found: (1)
**capital position — not turn order — decided 3/5 tested maps** (same
capital won every replay across seat swaps and re-seeded dice); an
access-balancing capital-pair criterion FAILED to fix it (far pairs are
extremity cities; and one dominant capital was the access-poorer one —
the cause is deeper map structure) and was reverted. The arena's
side-swap protocol already cancels this for measurement; it is a *game
design* question: measured balance at setup (mirror playouts on candidate
pairs) vs accepting start asymmetry as variance. (2) **Hot-potato cities**:
§5.4 + artillery lets a frontier city trade hands ~30 times in one game
(conqueror disbands → city empty behind its guns → counter-fist walks in).
Both AIs value the flips correctly; the *feel* is the question. Both forks
+ the no-progress rule are recorded for the user's judgment before the
human-play phase.

**Step 3 — widen + harden (only after the gate).**
- More choice points (counterattack timing after a broken wave, fighter
  employment), parallel candidate evaluation if budget demands, spatial
  playout boundary if profiling ever shows unit-scaling pain (recorded
  optimization, not v1).
- Opponent pool (horde + frozen `StrategicAI` + mirror) to shed
  single-opponent overfit; determinization sampling if the fog_cheat gap is
  large.
- Difficulty tiers from search knobs (K, H, eval noise).

---

## Phase 16 — Persistence hardening + schema v1 freeze (1-2 sessions)

**Deliverable:** Full serializers for every entity including `AIMemory`. Schema v1 frozen — golden saves committed for round-trip regression detection.

**Canary tests:**
- **Round-trip every entity type** via property tests (generate random instances, save, load, compare).
- **Golden saves:** ~5 representative game-state snapshots in `tests/fixtures/saves/v1/`. Round-trip each on every test run.
- **Migration framework smoke:** a no-op `Migration(from=0, to=1)` registered and exercised by a v0 fixture, proving the chain works (insurance for when v2 lands).

**Parallelism:** Moderate. Per-entity serializer functions are independent once the dispatcher pattern is set:
- One agent per entity-class serializer group: `(Map, Tile)`, `(Player, ViewMap)`, `(Unit subclasses)`, `(City, ProductionState)`, `(Goal, TaskForce, AIMemory)`. Each agent adds its `to_dict`/`from_dict` + round-trip property test.
- Sequential after: `SaveManager` integration + golden-save creation.

Net: ~3-4× wall-clock on the serializer functions.

**Exit gate:** Gates green. Golden-save round-trips pass.

---

## Phase 17 — TUI (3-5 sessions)

**Deliverable:** Textual app — `TitleScreen`, `PlayScreen` (with `MapWidget`, sidebars, status, log), `GameOverScreen`, modals. Command pattern for input.

**Canary tests:**
- **Snapshot tests** (via Textual pilot): a small set of canned game states render to expected snapshots. Snapshots committed; intentional UI changes update them via review.
- **Command round-trip:** every command type, given a known game state, produces the expected `Game` mutation.
- **Smoke:** boot the app headless, play 5 turns via scripted input, assert no crashes.

**Parallelism:** Significant on widgets. After the `EmpireApp` skeleton + CSS file + `Command` ABC are pinned single-threaded:
- One agent per widget, in parallel: `MapWidget`, `CityListWidget`, `UnitListWidget`, `StatusBarWidget`, `LogPanel`, `CommandLineWidget`. Each in its own file with its own snapshot test.
- Parallel to widgets: one agent per modal (`CityProductionModal`, `HelpModal`, `ConfirmModal`).
- Parallel to widgets: one agent for each concrete `Command` subclass (small files, independent tests).
- Sequential after: `PlayScreen` composes the widgets; `TitleScreen` and `GameOverScreen`; `commands.py` binding map.

Net: ~5-6× wall-clock on the widget+command work.

**Exit gate:** Gates green. Manual playthrough completes a game start-to-finish.

---

## Phase 18 — LLMAI (3-4 sessions)

Lands last per `03-ai-design.md` §8.

**Deliverable:**
- `LLMClient` interface + two backends: `LocalLlamaClient` (Ollama / llama.cpp), `AnthropicClient` (opt-in).
- `LLMStrategist` with `PromptBuilder`, `ResponseParser`, progress-based drift detection.
- "AI thinking…" indicator in TUI during inference (per the synchronous-blocking decision).
- `RecordedTranscriptClient` for tests.

**Canary tests:**
- **Prompt golden:** for a canned `IntelReport` + `AIMemory`, the built prompt matches a committed golden string. Catches prompt drift.
- **Parser robustness:** ~10 hand-crafted bad responses (malformed JSON, missing fields, hallucinated unit kinds, out-of-bounds priorities) all yield clean fallback to `DeterministicStrategist`.
- **Replay test:** `RecordedTranscriptClient` replays a saved transcript and produces a deterministic game outcome.
- **Drift detection:** a scripted scenario where the LLM "emits" a goal with no progress for many turns → priority gets demoted as expected.

**Quality gate:**
- `LLMAI` (local small-model backend) does not crash across 10 seeded games.
- It produces valid `TurnPlan`s — fallback to deterministic strategist fires <50% of turns on average.
- *Aspirational, not blocking:* it should be comparable to or better than `DeterministicStrategist`. If it's worse, that's a useful negative result and may justify fine-tuning.

**Parallelism:** Significant. After the `LLMClient` Protocol is pinned single-threaded:
- Agent A: `LocalLlamaClient` (Ollama / llama.cpp backend).
- Agent B: `AnthropicClient` (hosted backend, prompt caching enabled).
- Agent C: `RecordedTranscriptClient` (test fixture; replays canned transcripts).
- Agent D: `PromptBuilder` (with prompt-golden test).
- Agent E: `ResponseParser` + validation (with the bad-response test suite).
- Sequential after: `LLMStrategist` (consumes Client + PromptBuilder + ResponseParser); drift detection wiring; TUI "thinking" indicator.

Net: ~4-5× wall-clock on the backend + prompt/parse work, which is most of the phase.

**Exit gate:** Gates green. LLMAI completes games; doesn't crash; fallback rate within tolerance.

---

## Testing philosophy recap

**Per phase: one good integration test that exercises the phase's main surface.** When it breaks, you know which phase regressed. The stack trace then points at the failing function.

**Targeted unit tests where algorithms are non-obvious:** combat distributions, pathfinding correctness on tricky terrain, mapgen statistics. These are the places where "looks right" is not enough.

**Golden tests for stable expected outputs:** mapgen summary stats, save-file round-trips, prompts, decision spot-checks. Cheap to maintain, high signal when they fail.

**Self-play tests as final AI gates:** these catch the integration bugs that unit tests can't. Marked `slow`; run on phase exit, not every commit.

**What we deliberately don't do:**
- Test every getter/setter.
- Test every helper function in isolation.
- Aim for 100% coverage. We aim for *useful* coverage.
- Mock heavily. The domain is small enough that real objects in tests are fine, and mocking hides integration bugs.

---

## Sequencing notes

- Phases 1-7 are mostly infrastructure and can move fast — small modules with clear interfaces.
- Phase 8 is the first "feels like a game" milestone but doesn't have an opponent yet.
- Phase 9-10 land the baseline opponent and validate the engine.
- Phases 11-15 are the meat of the project: the new AI.
- Phases 16-18 are polish, packaging, and the optional LLM experiment.

**Estimated effort:** 30-45 focused sessions sequentially. With the parallelization callouts applied, realistic wall-clock is closer to **18-25 sessions** — the biggest wins are in Phase 2 (entities), Phase 9 (per-unit BaselineAI), Phase 14 (behaviors), Phase 17 (widgets), Phase 18 (LLM backends). Infrastructure phases (0-7) parallelize modestly; the AI and UI phases parallelize *heavily* because they're collections of independent implementations of a common interface.

**Phases that resist parallelism** (interface design is itself the deliverable, or one cohesive algorithm):
- Phase 1 (too small to split usefully).
- Phase 3 (small, single coherent contracts file set).
- Phase 5 (mapgen is one cohesive algorithm).
- Phase 8 (game mechanics are tightly coupled to `Game`/`TurnManager`).
- Phase 10 (validation run only).
- Phase 13 (`OperationalPlanner` is one cohesive matcher).
- Phase 15 (integration step).

**General rhythm per parallelizable phase:**
1. Single-threaded "interface pinning" session: write the ABC/Protocol, locking the contract.
2. Single message dispatching N agents in parallel, each owning a disjoint file set.
3. Single-threaded stitching session: wire exports, run full gates, fix integration bugs.

Step 1 is non-negotiable. Skipping it and asking agents to "agree on an interface as you go" produces N incompatible implementations.

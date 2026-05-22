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
- `RuleSet` dataclass + `CLASSIC` preset populated with canonical values from `01-classic-rules-reference.md`.

**Canary tests:**
- `Coord` arithmetic round-trips (`step` then reverse-step returns original).
- `Direction.offsets()` produces all 8 unique vectors.
- `CLASSIC` preset loads and has the expected name/values for a handful of spot-checked fields.

**Exit gate:** Gates green. ~20-30 LOC of tests.

---

## Phase 2 — Core entities, no mechanics (2-3 sessions)

**Deliverable:** `Tile`, `Map`, `Unit` hierarchy + registry, `City` + `ProductionState`, `Player`, `ViewMap` — *positional and structural only*. No movement rules, no combat, no production tick yet.

- `Map.place_unit / move_unit / remove_unit` maintain the spatial index but do NOT validate against `RuleSet`. Rule validation lands in Phase 8.
- `Unit` subclasses with class-level attrs filled from `data.c:108-185`. `Unit.coord` is read-only via `@property`; `_set_coord` is called only by `Map`.
- `ProductionState` has the methods stubbed but no tick logic yet.
- `ViewMap` exposes `update_from_scan`, `seen`, `render_char` — implementation can be naive (full visibility) for now; fog logic lands in Phase 8.

**Canary tests:**
- Spatial-index consistency: after `N` random placements/moves/removes, `Map.units_at(c)` matches a brute-force scan of all units.
- Every `UnitKind` resolves to a concrete `Unit` subclass via the registry, and every subclass declares every required class attr (introspection test).
- Unit class-attr values match a small golden table extracted from `data.c` (catches typos in the canonical attribute transcription).

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

**Deliverable:** `ClassicMapGenerator` ports `make_map` + `place_cities`. Three `MapProfile` presets (`SMALL`, `CLASSIC`, `LARGE`).

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

## Phase 9 — ClassicAI (3-4 sessions)

**Deliverable:** Direct OO port of `compmove.c`. Per-unit decision methods on `ClassicTactical`, weight tables loaded from data, `ClassicPathfinder` (classic perimeter BFS lives here too — or reuse from Phase 7).

**Canary tests:**
- **Self-play stability:** 20 seeded `ClassicAI` vs `ClassicAI` games (seeds 0-19) run to completion without crashing. Game length and final-score distribution are recorded as a golden — sudden shifts are reviewed.
- **Decision spot-checks:** ~5 canned `WorldView` situations where the original C source has a known correct decision (e.g., "army adjacent to undefended neutral city → move into city"). Our `ClassicAI` produces the same decision.
- **Interface adapter:** `ClassicAI.revise_move()` returns a sensible step (re-runs per-unit decision); does not crash on any `Surprise` variant.

**Parallelism:** Significant. The `compmove.c` source has a per-unit decision function per piece type (`army_move`, `fighter_move`, `transport_move`, `ship_move`, etc.) — these become methods on `ClassicTactical`, one per unit kind. After the dispatch skeleton + weight-table loader are written single-threaded:
- One agent per unit-kind decision function (one file each under `ai/classic/decisions/`), in parallel. Each agent reads the corresponding `compmove.c` section, ports to Python, adds a spot-check test.
- Parallel to all of the above: one agent ports `ClassicPathfinder` (or reuses BFS from Phase 7).

Stitching: register decisions in the dispatch map, run self-play. Net: ~4-6× wall-clock on the per-unit work, which is the bulk of the phase.

**Exit gate:** Gates green. 20-game self-play completes; outcome distribution within tolerance of recorded golden (or, on first run, *establishes* the golden).

---

## Phase 10 — Engine validation milestone (1 session)

No new code — a validation gate. Confirms that Phases 1-9 produced a sound engine.

**Run:**
- 50 seeded `ClassicAI` vs `ClassicAI` games to completion. Assertions:
  - No crashes.
  - All games terminate within a turn cap (e.g., 500 turns) — non-termination is a bug.
  - Win-rate roughly balanced if continent quality is balanced (both sides win ~30-70% range).
  - Saves taken at random turns load back identically.

**Exit gate:** All 50 games pass. This is the green light to start building `StrategicAI`.

---

## Phase 11 — IntelService (2 sessions)

**Deliverable:** `IntelService` produces `IntelReport` with `Threats`, `Opportunities`, `ChokePoints`, `Theaters` from a `WorldView`.

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

## Phase 12 — FeasibilityOracle + Goals + DeterministicStrategist (2-3 sessions)

**Deliverable:** `FeasibilityOracle`, full `Goal` hierarchy, `DeterministicStrategist`.

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

## Phase 13 — OperationalPlanner + TaskForce + reaper (2 sessions)

**Deliverable:** `OperationalPlanner` consumes goals, assembles `TaskForce`s, emits production orders, reaps terminal-state TFs at end-of-turn.

**Canary tests:**
- **Force matching:** given a goal requiring (3 armies, 1 transport) and an idle pool that contains them, the resulting `TaskForce` includes exactly those units.
- **Reaper:** a `COMPLETE` TF is dropped after one turn of grace; a `DISBANDED` TF likewise.
- **Save/load:** a game with active TFs round-trips through JSON without loss.

**Exit gate:** Gates green.

---

## Phase 14 — TacticalExecutor + Behaviors (3-4 sessions)

**Deliverable:** `Behavior` ABC, `Unit.behavior_for(role)` inversion, concrete behaviors (`ArmyAssault`, `ArmyGarrison`, `TransportFerry`, `FighterStrike`, `FighterScout`, `ShipEscort`, `ShipPatrol`, `SubAmbush`), `DefaultBehavior` (sentry no-op).

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

## Phase 15 — StrategicAI integration & validation milestone (2 sessions)

**Deliverable:** `StrategicAI` wired together; all four layers active.

**Validation:**
- 50 seeded `StrategicAI` vs `ClassicAI` games at NORMAL difficulty.
- **Quality gate: `StrategicAI` win-rate ≥ 60%.** If not, the "smarter" design isn't smarter — investigate before proceeding. Per-difficulty: EASY ~50%, NORMAL ≥60%, HARD ≥70%.
- Save/load of mid-game `StrategicAI` state (including `AIMemory`, active TFs, goals) round-trips.

**Exit gate:** Win-rate threshold met; saves round-trip.

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

**Estimated effort:** 30-45 focused sessions sequentially. With the parallelization callouts applied, realistic wall-clock is closer to **18-25 sessions** — the biggest wins are in Phase 2 (entities), Phase 9 (per-unit ClassicAI), Phase 14 (behaviors), Phase 17 (widgets), Phase 18 (LLM backends). Infrastructure phases (0-7) parallelize modestly; the AI and UI phases parallelize *heavily* because they're collections of independent implementations of a common interface.

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

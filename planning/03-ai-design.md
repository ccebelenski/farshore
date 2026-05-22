# AI Design

Companion to [D-007](02-design-decisions.md) and [classic rules reference](01-classic-rules-reference.md).

This document describes the AI architecture. There are **three** AI personalities planned, all conforming to a single `AIController` interface:

1. **`BaselineAI`** — a greedy per-unit BFS AI with weight-driven objectives. **Purpose:** regression baseline against which `StrategicAI` is benchmarked, and a "lightweight opponent" mode for quick games. Selectable but not the default.
2. **`StrategicAI` (default)** — hierarchical goal-seeking AI with explicit strategy, operational planning, and tactical execution layers. **Purpose:** the new default. Genuinely smarter than classic; respects fog of war.
3. **`LLMAI`** — wraps `StrategicAI` but replaces the top `Strategist` component with an LLM-driven strategist. **Purpose:** optional opponent; explores how a reasoning model plays this game.

All three implement the same interface (`AIController.plan_turn(world_view) -> TurnPlan`) and are swappable at game start.

---

## 1. Shared abstractions

These classes are used by **every** AI personality. They are pure data + utility — no AI policy lives in them.

### `WorldView`
The AI's belief about the world, filtered through the same fog-of-war machinery the player uses (see [`01-game-rules-spec.md`](01-game-rules-spec.md) §6). **The AI must not read the real `Map` directly except through this view.**

`WorldView` is a **live filtered view**, not a snapshot. It holds references to the real `Player`/`City`/`Unit` objects but exposes only what fog-of-war permits. The AI sees current state every time it reads. Anything that must be frozen (intel summaries, planned moves) is the responsibility of the consumer — `IntelService` produces an immutable `IntelReport`, the strategist's `Goals` are frozen on emission, etc. Memory copies are avoided.

```
class WorldView:
    own_player: Player
    turn: int
    visible_tiles: Mapping[Coord, Tile]              # what we can see right now
    remembered_tiles: Mapping[Coord, RememberedTile]  # last-seen state of formerly visible tiles
    own_cities: list[City]
    own_units: list[Unit]
    known_enemy_units: list[KnownEnemyUnit]   # last sighting w/ turn
    known_enemy_cities: list[City]
    neutral_cities: list[City]
    rules: RuleSet
```

`RememberedTile` carries the last-observed contents (terrain, what unit/city was there) and the turn it was last seen. Stale enemy units in remembered tiles are *hypothetical*, not authoritative — the AI should weight them by staleness when planning.

### `Coord`, `Tile`, `Unit`, `City`
Defined in the core domain model (`04-class-hierarchy.md`). The AI never mutates these directly — it produces a `TurnPlan` describing intended actions, which the game engine validates and applies.

### `TurnPlan`
The output of every `AIController.plan_turn()` call. A declarative description of the AI's intent for this turn. The engine applies it; the AI does not have side-effecting authority.

```
class TurnPlan:
    production_orders: list[ProductionOrder]     # set/change city production
    moves: list[UnitMove]                        # per-unit movement intent
    sentries: list[UnitSentry]                   # explicit sentry/wake commands
    notes: dict                                  # debug/telemetry (goal IDs, scores, etc.)
```

A `UnitMove` is either a step (one cell in a direction) or a multi-step path (cells in order). The engine resolves them one move at a time, recomputing visibility after each.

**Strategic commitments survive surprises.** A `TurnPlan` represents the AI's intent for the turn. When a path is invalidated mid-execution (enemy appears, target captured by someone else, transport sunk), the engine asks the affected unit's `Behavior` for a revised next step — but the parent `Goal` and `TaskForce` are not re-evaluated mid-turn. This is deliberate: like a chess player who sacrifices a piece for position, the AI must be willing to accept short-term losses in service of a strategic commitment. Goal-level re-evaluation happens at the next turn's strategist call, not on every surprise.

Reactivity boundary:
- **Mid-turn (Behavior level):** "the path I planned no longer works — what's the best next step under the same role/mission?" — handled by `Behavior.revise(unit, view, surprise)`.
- **Turn boundary (Strategist level):** "given everything that happened, are my goals still right?" — handled by the next `plan_turn()`.
- **Never mid-turn:** dissolving task forces, swapping goals, abandoning missions due to a single surprise.

### `AIController` (abstract)

```
class AIController(ABC):
    @abstractmethod
    def plan_turn(self, view: WorldView) -> TurnPlan: ...

    @abstractmethod
    def name(self) -> str: ...

    # Mid-turn revision hook: engine calls this when a planned UnitMove is invalidated.
    # Returns the unit's next step under the existing role/mission. May return SKIP.
    def revise_move(self, unit_id: UnitId, surprise: Surprise, view: WorldView) -> UnitMove: ...
```

`revise_move` is the only mid-turn entry point. It's scoped to a single unit's next step — strategic state (goals, task forces) is not re-evaluated until the next turn. Most personalities delegate to the unit's `Behavior.revise()`. If no behavior is registered for the situation, the default is a **no-op** (sentry) — undefined behaviors never crash, they just stand still and wait for the next turn's planner to sort things out.

### `Surprise`
Tagged union describing the event that invalidated a planned move. Lives in `contracts/surprise.py`. Frozen dataclasses:

```
class Surprise: ...                       # ABC marker
class EnemySighted(Surprise):    enemy: KnownEnemyUnit; at: Coord
class PathBlocked(Surprise):     blocked_at: Coord; by: Cause
class TargetLost(Surprise):      target_id: CityId | UnitId
class EscortLost(Surprise):      escort_id: UnitId
class TerrainImpassable(Surprise): at: Coord    # e.g., predicted-land turned out water
```

Behaviors pattern-match on the surprise type to decide their single revised step.

### Information staleness during revise

Mid-turn revision sees a **live `WorldView`** (current state) but a **frozen `IntelReport`** (computed once at turn start). This is intentional, not a bug: real generals don't get perfect mid-battle re-assessments. The behavior may notice "this exact enemy is right here" via WorldView, but the broader threat picture stays at turn-start fidelity until the next strategist call. Behaviors must not try to refresh `IntelService` mid-turn — that would defeat the reactivity boundary and incur per-move costs.

---

## 2. `BaselineAI` — greedy baseline

A simple per-unit greedy AI. For each owned unit, evaluate nearby objectives (capturable cities, enemy units in range, unexplored frontier) with a weighted scoring function, then move toward the highest-scoring objective using BFS pathfinding. No cross-unit coordination, no strategic layer — just one independent decision per unit per turn.

**Why include it:**
- Regression target: if `StrategicAI` can't beat `BaselineAI` reliably, our "smarter" design isn't actually smarter. Self-play between the two is our quality gate (`05-implementation-plan.md` Phase 15).
- Lightweight opponent mode for quick games or systems too constrained for `StrategicAI`'s overhead.
- Validates the engine: a simple deterministic AI that can complete games proves the rules and combat systems work end-to-end.

**Interface adapter notes.** `BaselineAI` has no `Behavior` objects, `Goal`s, or `TaskForce`s — it's strictly per-unit greedy. To satisfy the shared `AIController` protocol:
- `revise_move()` re-runs the unit's per-unit decision function against the live `WorldView` and returns the resulting next step.
- Combat engagement is simple: engage when adjacent to an enemy in the unit's `attack_preferences` list, otherwise route around. The `CombatEvaluator` / behavior split (engagement policy lives in the behavior) is a `StrategicAI`/`LLMAI` feature only; `BaselineAI` does not consult `CombatEvaluator`.
- `IntelService`, `FeasibilityOracle`, `AIMemory` are not used by `BaselineAI`.

**Notes for implementation:**
- Weight tables (per unit kind: how strongly to weight "capture city" vs. "attack enemy" vs. "explore" vs. "return to base") are designed during Phase 9 and tuned by playtesting.
- Per-unit move functions are methods on `BaselineTactical` (one method per unit kind) to keep OO discipline.
- BFS perimeter expansion lives in `BaselineBFS` or reuses `BFSPathfinder` from `empire.pathfinding` (Phase 7).
- Target size: a few hundred lines of clean Python. If it's growing past ~800 lines, something is wrong (over-clever weight tuning or strategic layer leakage).

---

## 3. `StrategicAI` — the new default

A hierarchical AI with four cooperating layers. Each layer has a clear input and output; the layers are composed, not subclassed.

```
class StrategicAI(AIController):
    strategist: Strategist             # WHAT to achieve (goals)
    operational: OperationalPlanner    # WHO does what (task forces)
    tactical: TacticalExecutor         # HOW exactly (per-unit moves)
    intel: IntelService                # threat + opportunity assessment
    memory: AIMemory                   # cross-turn state
```

### 3.1 `IntelService` — situation assessment

Runs first each turn. Produces structured assessments from the `WorldView`. **No decisions made here** — just analysis.

Outputs:
- `Threats`: a `Threat` per known enemy force. Each carries estimated combat power, projected reach (cells reachable in N turns), and the friendly assets at risk.
- `Opportunities`: a list of `Opportunity` objects — capturable neutral cities, exposed enemy cities, undefended enemy fleets. Scored by value × probability of success × inverse distance.
- `ChokePoints`: terrain features (narrow straits, isthmi) that funnel movement. Useful for defense.
- `Theaters`: connected regions (one per landmass + adjacent waters), each tagged with its strategic state — `FRIENDLY_CORE`, `CONTESTED`, `ENEMY_CORE`, `UNEXPLORED`.

Implementation notes:
- "Projected reach" uses each enemy unit type's speed table + the canonical Chebyshev distance. Conservative — assume enemies move optimally toward our assets.
- Theater detection is a flood-fill on the `WorldView`, with predicted-terrain inference for unexplored cells (treat unexplored neighbors of land as probably-land, neighbors of water as probably-water).
- All `IntelService` output is reproducible from the same `WorldView` — no hidden state.

### 3.2 `Strategist` — goal generation

Consumes `Intel` output. Produces an ordered list of `Goals`, each with a budget (which units/cities are committed to it) and a priority.

```
class Goal(ABC):
    id: GoalId
    priority: float           # 0..1; higher = more important
    budget: ResourceBudget    # units, cities, production slots earmarked
    estimated_duration: int   # turns
    success_criteria: ...

class CaptureCityGoal(Goal): target_city, ...
class DefendCityGoal(Goal): target_city, garrison_size_needed, ...
class ExploreAreaGoal(Goal): target_region, ...
class ProjectPowerGoal(Goal): target_continent, force_composition, transport_count, ...
class DenyContinentGoal(Goal): target_continent, ...
class BuildForcesGoal(Goal): force_composition_target, ...
```

`Strategist.plan(intel: IntelReport, memory: AIMemory, feasibility: FeasibilityOracle) -> list[Goal]`.

#### `FeasibilityOracle`

Pure function over `WorldView` + production capacity. Answers cheap forward questions before the strategist commits:

- `can_assemble(composition, by_turn) -> bool` — can we build/move this force composition into a rendezvous by turn N?
- `defensible(city, threat) -> bool` — given current garrisons and reachable reinforcements, can we hold?
- `reachable(start, goal, by_turn) -> bool` — can a unit type cross this distance in N turns given known terrain?

The strategist asks the oracle *before* emitting goals, so the operational layer rarely has to reject them. There is no feedback channel from operational → strategist; the strategist re-plans every turn against the latest oracle results. This keeps the layer boundary clean (operational has no policy authority) at the cost of recomputing some assessments per turn — acceptable, since the oracle is cheap relative to A*.

Algorithm (deterministic strategist):
1. **Defensive goals first.** For each owned city, evaluate threat from `intel.threats`. If under threat above tolerance, emit `DefendCityGoal` with required garrison size.
2. **Consolidate goals.** For each `Theater` in `CONTESTED` state, if we have majority cities, emit `DenyContinentGoal` (capture neutrals + push enemy). If minority, emit either `DefendCityGoal`s or `EvacuateGoal` if hopeless.
3. **Expand goals.** For each `Opportunity` (neutral or weak enemy city), evaluate value/cost; emit `CaptureCityGoal` if cost-effective. Cap concurrent expansion goals based on production capacity.
4. **Cross-theater goals.** If a theater has surplus production and there's a viable invasion target, emit `ProjectPowerGoal`. Composition tuned to opposition (heavy armies + transport flotilla, plus escorts).
5. **Exploration goals.** For each `UNEXPLORED` theater within transport range, emit `ExploreAreaGoal` at low priority.
6. **Sort by priority × (1 / estimated_duration), budget-cap.**

Critically: goals are *proposals*. The operational layer may reject one if the budget can't be assembled.

### 3.3 `OperationalPlanner` — task forces

Consumes goals; assembles **task forces** to pursue them. A task force is a group of units with a shared mission.

```
class TaskForce:
    id: TaskForceId
    goal: Goal
    units: list[Unit]
    role_assignments: dict[UnitId, Role]   # ASSAULT, SCOUT, ESCORT, TRANSPORT, GARRISON
    rendezvous: Coord | None
    target: Coord
    state: FORMING | EN_ROUTE | ENGAGED | COMPLETE | DISBANDED
```

Responsibilities:
- **Force matching.** Given a goal's required composition (e.g., "3 armies + 1 transport + 1 destroyer escort"), select the cheapest set of available friendly units to fulfill it. Existing task forces have first claim on their units; idle units are pulled freely.
- **Production requests.** If goal requires units we don't have, emit `ProductionOrder`s. Strategist's budget includes "production slots" so this can't outrun capacity.
- **Rendezvous.** For multi-unit goals, compute a safe gathering point near the goal's origin (e.g., the city closest to the target with enough capacity to hold the force). Units flow there before the strike.
- **Reactive disbanding.** If the goal becomes infeasible (target city captured by someone else, our transports sunk), mark `DISBANDED` and free the units. Disbanding happens at turn boundaries, not mid-turn (per the reactivity boundary above).
- **Reaping.** At end-of-turn, drop `COMPLETE` and `DISBANDED` task forces older than one turn. Telemetry/debug consumers get one turn of grace. Prevents `AIMemory` from accumulating dead TFs over a long game.

`OperationalPlanner.plan(goals: list[Goal], view: WorldView, memory: AIMemory) -> list[TaskForce]`.

### 3.4 `TacticalExecutor` — per-unit movement

Consumes task forces; produces concrete `UnitMove`s for every unit.

For each `Unit`:
1. Determine its assigned task force (or "idle"/"sentry" if none).
2. Look up its `Role` in the force.
3. Ask the unit for the `Behavior` matching that `Role` — `unit.behavior_for(role)`. The unit subclass owns the role→behavior mapping; unsupported pairs return a `DefaultBehavior` (sentry or retreat-to-safety).

Behavior registration is **inverted**: rather than a central `(UnitKind, Role) → Behavior` table that grows into a sparse Cartesian product, each `Unit` subclass declares which roles it handles and returns the appropriate behavior. Example: `Army.behavior_for(ASSAULT) → ArmyAssaultBehavior()`; `Carrier.behavior_for(ASSAULT) → DefaultBehavior()` (nonsense pair). Adding a new unit type ships behaviors alongside it without editing the executor.

Concrete behaviors include:
- `ArmyAssaultBehavior` — head toward target city, fight enemy armies en route via `find_attack`, embark on rendezvous transport if needed.
- `ArmyGarrisonBehavior` — stay in city, attack any adjacent enemy.
- `TransportFerryBehavior` — load at rendezvous, sail to target shore, unload.
- `FighterStrikeBehavior` — escort flying overhead, range-aware return-to-base.
- `FighterScoutBehavior` — head toward unexplored, return when range low.
- `ShipEscortBehavior` — stay within N cells of escorted transport, intercept threats.
- `ShipPatrolBehavior` — sweep around target area or rendezvous.
- `SubAmbushBehavior` — head to known enemy shipping lane, attack on contact.
- … etc.

Each `Behavior` exposes:
- `next_move(unit, view, force) -> UnitMove` — normal turn-time planning.
- `revise(unit, surprise, view, force) -> UnitMove` — single-step revision after a mid-turn surprise (per the reactivity boundary in §1).

**Pathfinding:** A* with terrain-aware costs and a danger-weighted heuristic that prefers cells far from `Threat.projected_reach`. The cost function is parameterized (`PathCostProfile`) so a fragile fighter takes safer paths than a battleship with HP to spare. Common ground for path queries lives in a `Pathfinder` service.

**Combat decisions:** when a unit's path crosses an attackable enemy, the behavior consults a `CombatEvaluator` (which computes expected outcome from the combat probability formula defined in [`01-game-rules-spec.md`](01-game-rules-spec.md) §4.2) to get an `ExpectedOutcome` — win probability and expected damage. **The evaluator does not decide whether to engage.** That decision belongs to the `Behavior`, which combines the expected outcome with strategic context from its task force's `Role` and `Goal`:

- An `ArmyAssaultBehavior` mid-invasion may engage at <50% win probability if defeating this defender clears the path to the target city (positional value > raw EV).
- A `TransportFerryBehavior` carrying the assault force will evade even at high win probability — the cargo matters more than the kill.
- A `ShipEscortBehavior` will engage at low EV to intercept a threat against its escorted transport (trading the escort is the *job*).

This keeps the evaluator pure (no strategic policy in `combat/`) and puts engagement policy in the behavior, where the mission context lives.

### 3.5 `AIMemory` — cross-turn state

Persistent state the AI carries between turns. Examples:
- Active task forces (so they survive turns; ephemeral plans don't).
- Long-term beliefs ("there's probably an enemy carrier near (47, 12) — saw it 3 turns ago, would have moved by now").
- Production history (so we can detect we keep flipping a city's production — anti-thrash).
- Difficulty-level tunables (see §5).

Saved with the game in JSON. The `AIMemory` schema lives next to the rest of the save format.

---

## 4. `LLMAI` — strategist replaced by a language model

`LLMAI` is `StrategicAI` with one component swapped:

```
class LLMAI(StrategicAI):
    def __init__(self, ..., llm_client: LLMClient):
        super().__init__(...)
        self.strategist = LLMStrategist(llm_client)   # replaces DeterministicStrategist
```

Everything else (`IntelService`, `OperationalPlanner`, `TacticalExecutor`) is identical. **The LLM only sets goals.** It does NOT move individual units or do pathfinding. Reasons:

- **Latency.** A turn with 50 units making 50 LLM calls is too slow — especially with local CPU inference. One call per turn (or every N turns) is feasible.
- **Token budget.** Sending full board state for tactics is wasteful; the deterministic layers handle that fine. Critical for small local models with limited context.
- **Reliability.** Goal-level outputs are simpler to validate than per-unit move outputs. Small models will hallucinate; constraining the output surface limits the blast radius.
- **Strength.** A model's edge — if it has one — is *strategy* and *reading the situation*, not micromanagement.

This boundary also makes evaluation clean: we can A/B test the LLM strategist against the deterministic strategist with the operational and tactical layers held constant. It also means we can swap a 1B-parameter local model for a 70B hosted model without touching anything else.

### 4.0 Model targeting: small local first

The design center is a **small local model running on CPU** (e.g., a 1B–8B parameter model via llama.cpp / Ollama). Hosted frontier models (Claude, GPT) are an opt-in upgrade, not a baseline assumption. Rationale:

- Users without GPUs should be able to run `LLMAI` and have it function.
- "Cost" should be measured in local inference time, not dollars per turn — accessible to anyone, no API key required.
- If a small model can't play meaningfully even with the deterministic operational/tactical layers doing the heavy lifting, that's a useful negative result *and* a signal that we should fine-tune.
- If we end up needing Opus-class reasoning to play at all, the project failed — the deterministic strategist is supposed to be competent on its own.

Fine-tuning a small model on game traces (from `StrategicAI` self-play, or hand-curated positions) is an explicit fallback if base-model performance is weak. The `LLMClient` interface is deliberately backend-agnostic so we can swap base ↔ fine-tuned ↔ hosted without code changes.

### 4.1 `LLMStrategist` interface

```
class LLMStrategist(Strategist):
    def plan(self, intel: IntelReport, memory: AIMemory) -> list[Goal]:
        prompt = self._build_prompt(intel, memory)
        response = self.client.call(prompt)   # async with timeout
        goals = self._parse_goals(response)
        return goals
```

### 4.2 Prompt structure

Cache-friendly layout. Static system prompt + small per-turn delta keeps both local KV-cache reuse and hosted prompt-caching effective (the latter has a 5-minute TTL on Claude API):

1. **System prompt (cached):**
   - Empire game rules (units, combat math, fog of war, win condition).
   - Strategic principles ("seize cities to scale production", "transports are vulnerable, escort them", "control of continents matters").
   - Output schema (JSON list of goals with their fields).
   - Explicit list of valid goal types and what fields each takes.

2. **Per-turn dynamic context:**
   - Turn number, score deltas since last call.
   - `IntelReport` rendered compactly (theaters, threats, opportunities, choke points).
   - Active task forces summarized ("TF-3: 2 armies + 1 transport, en route to (47,12), 4 turns out").
   - Production state per city ("London: building battleship, 18/40").
   - Recent events: cities lost/gained, units lost, enemy sightings in last 3 turns.
   - The last set of goals issued (so the LLM can revise, not redo).

3. **Request:**
   - "Given the above, output a ranked list of goals for this turn. Use the schema. Keep total goals ≤ 8."

The dynamic portion is small (~1–4K tokens for a mid-game state). The system prompt is large (~5–10K tokens) and reused turn after turn, so caching is critical. For small local models with limited context, the system prompt may need a stripped variant — kept short and rules-only, with strategic principles moved into few-shot examples.

### 4.3 Response schema

Strict JSON, validated against a Pydantic-ish model. Invalid responses retry once, then fall back to `DeterministicStrategist`.

```json
{
  "rationale": "short prose, for debug/replay",
  "goals": [
    {
      "type": "CaptureCityGoal",
      "priority": 0.8,
      "target": {"x": 47, "y": 12},
      "force": {"armies": 3, "transport": 1, "escort": "destroyer"},
      "rationale": "neutral city on enemy's flank; cutting it off denies them an army producer"
    },
    ...
  ]
}
```

### 4.4 Reliability mitigations

- **Timeout** per turn, configurable per backend (e.g., 30s hosted; 60-180s for local CPU inference depending on model size). On timeout → fall back to `DeterministicStrategist` for this turn.
- **UI blocking is acceptable** during inference, provided the TUI shows a clear "AI thinking…" indicator (turn counter, model name, elapsed seconds). The engine stays synchronous; no async plumbing through the turn manager. Turn-based gameplay already has a "wait for the other side" pause point — making it visible is enough. Revisit if user feedback says otherwise.
- **One strategist call per turn, hard limit.** Mid-turn surprises route through `Behavior.revise()`, never back to the LLM. Keeps wall-clock and cost predictable.
- **Validation** of every goal: target coord exists, force composition feasible, priority in [0,1].
- **Progress-based drift detection.** Each goal type defines a `ProgressSignal` (production %, units assembled at rendezvous, distance closed to target). If progress is below the goal type's expected trajectory for its stage, demote priority. *Not* raw turn count — a `ProjectPowerGoal` legitimately shows no external progress for 15+ turns while forces assemble. Prevents pathological loops without suppressing valid long-horizon play.
- **No agency outside the schema.** Even if the LLM hallucinates a unit type, the parser rejects it.
- **Determinism mode.** For tests and replays, allow a recorded-transcript mode where `LLMClient` is replaced by a stub that replays a saved transcript.

### 4.5 Backends

The `LLMClient` interface is backend-agnostic. Shipped backends:

- **Local (default): llama.cpp via Ollama or equivalent.** Small instruction-tuned model (1B–8B param range). CPU-runnable; GPU used if available but not required. No API key.
- **Hosted (opt-in): Claude API.** For users who want stronger play and don't mind paying. Sonnet 4.6 is a reasonable hosted default; Opus 4.7 for "see how strong this can get"; Haiku 4.5 for cheaper hosted play. Uses Anthropic SDK with prompt caching.
- **Fine-tuned local (future).** If small base models underperform, train on `StrategicAI` self-play traces or hand-curated positions. Same `LLMClient` interface; just a different weights file.

Backend selection is a runtime config, not a code change.

### 4.6 Budget

Two budgets, depending on backend:

- **Local:** wall-clock per turn. Target: ≤10s on a modern CPU for a small model. If a model can't hit this, it's too big for the default tier — recommend hosted instead. Inference time is the only cost; no per-game dollar figure.
- **Hosted (Claude Sonnet 4.6, prompt cache warm):** ~8K cached input + ~1.5K fresh input + ~1K output → ~$0.01–0.03/turn. A 200-turn game ≈ $2–6.

User-facing controls:
- Toggle: classic / strategic / LLM AI.
- Sub-toggle for LLM AI: backend (local model file / hosted model + API key).
- Sub-toggle: "LLM strategist re-plan every N turns" (default 1 = every turn). Higher N saves time/cost at the price of stale strategy. Particularly relevant for slow local inference.

---

## 5. Difficulty knobs

All AI personalities support a `Difficulty` setting. Knobs apply to `StrategicAI` and `LLMAI`; `BaselineAI` honors only the `continent_quality` knob.

| Knob | Description | Default per difficulty |
|---|---|---|
| `continent_quality` | Which continent the AI gets (per classic difficulty model). | EASY=poor, NORMAL=balanced, HARD=good |
| `fog_cheat` | If true, AI sees the real map (no fog). **Off by default at all levels.** | always FALSE unless user explicitly enables |
| `planning_lookahead` | How many turns the strategist projects threats/opportunities. Limits depth of intel/feasibility computations. | EASY=2, NORMAL=4, HARD=8 |
| `risk_tolerance` | How willing to commit to uncertain attacks (0..1). | EASY=0.3, NORMAL=0.5, HARD=0.7 |

**Difficulty must come from AI behavior, not engine bias.** No production multipliers, no extra units at start, no faster movement. The AI plays under the same rules as the player; "harder" means the strategist is allowed to think more (`planning_lookahead`), accept more risk (`risk_tolerance`), or — at the top end — be the LLM strategist instead of the deterministic one. The `fog_cheat` toggle exists because some players want it; it is **never on by default**. The AI must beat the player without cheating to count as a "real" win — that's an explicit design goal.

---

## 6. Pluggability & extension

Every layer is an interface. Adding a new AI personality means subclassing the layer(s) you want to change and composing. Examples of future personalities:

- **`AggressiveAI`** — `StrategicAI` with a `Strategist` that always prioritizes `CaptureCityGoal` over defense.
- **`TurtleAI`** — strategist prioritizes `DefendCityGoal` and `BuildForcesGoal`; rarely attacks. Tests our defenses.
- **`SwarmAI`** — operational layer that builds only armies + transports, ignores ships.
- **`HumanInTheLoop`** — `Strategist` emits goals from human commands; `OperationalPlanner` + `TacticalExecutor` automate the rest. Useful for accessibility / quick play.

These are NOT v1 scope — listed only to validate that the interfaces support them cleanly.

---

## 7. Open questions for implementation phase

These should be revisited when we start writing code:

1. **Cross-turn task force IDs.** How do we persist them in JSON saves without making it too verbose?
2. **A* memoization.** Path queries are expensive in Python. Cache results per (start, goal, cost_profile, world_state_hash)? Or accept the cost?
3. **Threat projection scope.** Computing reachability for all enemy units every turn is O(units × cells). Acceptable on a 60×100 grid; might need optimization on larger maps.
4. **LLM error budget.** What's the right number of consecutive timeouts before we permanently fall back to deterministic for the rest of the game?
5. **Replay/movie support.** The classic had `empmovie.dat`. Worth preserving for AI debugging. Plumb in early or retrofit?

---

## 8. Build order (when we move to implementation)

Suggested ordering, narrow-to-wide:

1. Core domain (`Map`, `Tile`, `Unit`, `City`, `Game`, `RuleSet`, `WorldView`, `TurnPlan`) — see `04-class-hierarchy.md`.
2. Engine turn loop + classic rules (no AI yet; play hotseat against yourself for validation).
3. `BaselineAI` first — greedy per-unit. Validates the engine is correct (a simple deterministic AI completes games end-to-end).
4. `IntelService` — useful for player UI too (threat/opportunity HUD).
5. `StrategicAI` — strategist + operational + tactical, deterministic. Benchmark against `BaselineAI`.
6. `LLMAI` — drop in last; the deterministic baseline shows whether the LLM is actually helping.

This order also defers the most uncertain work (LLM integration) to the end, when everything around it is stable.

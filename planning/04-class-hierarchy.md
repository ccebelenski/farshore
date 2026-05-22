# Class Hierarchy Sketch

Pre-implementation sketch of the OO design. Companion to [01-game-rules-spec](01-game-rules-spec.md), [02-decisions](02-design-decisions.md), and [03-ai-design](03-ai-design.md).

This is **not** the final class list — it's a structural plan establishing layers, ownership, and dependency direction. Expect refinement during implementation.

---

## 1. Module / package layout

```
empire/
├── pyproject.toml
├── README.md
├── planning/                       # this directory (design docs)
├── Empire-for-VMS/                 # vendored C reference (read-only)
└── src/
    └── empire/
        ├── __init__.py
        ├── __main__.py             # entrypoint: `python -m empire`
        ├── core/                   # pure domain model — no UI, no AI policy
        │   ├── coord.py            # Coord, Direction, Distance helpers
        │   ├── tile.py             # Tile, TerrainKind
        │   ├── unit.py             # Unit + subclasses + UnitKind
        │   ├── city.py             # City, ProductionState
        │   ├── map.py              # Map, ViewMap, RememberedTile
        │   ├── player.py           # Player
        │   ├── ruleset.py          # RuleSet
        │   ├── game.py             # Game (root aggregate)
        │   └── identity.py         # UnitId, CityId, TaskForceId, etc. (NewType)
        ├── contracts/              # thin shared vocabulary between core and ai
        │   ├── controller.py       # AIController Protocol
        │   ├── world_view.py       # WorldView (live-filtered view of core state)
        │   ├── turn_plan.py        # TurnPlan, UnitMove, ProductionOrder, UnitSentry
        │   └── surprise.py         # Surprise tagged union
        ├── events/
        │   ├── bus.py              # EventBus
        │   └── events.py           # event dataclasses
        ├── combat/
        │   ├── resolver.py         # CombatResolver (per-blow attrition algorithm)
        │   └── evaluator.py        # CombatEvaluator (EV prediction)
        ├── mapgen/
        │   ├── generator.py        # abstract MapGenerator
        │   ├── height_field.py     # HeightFieldMapGenerator (smoothed height-field algorithm)
        │   └── profile.py          # MapProfile (size + ratios)
        ├── pathfinding/
        │   ├── pathfinder.py       # Pathfinder ABC, PathRequest
        │   ├── bfs.py              # BFSPathfinder (weighted-objective perimeter BFS)
        │   ├── astar.py            # AStarPathfinder
        │   └── cost.py             # PathCostProfile
        ├── ai/
        │   ├── controller.py       # AIController ABC, TurnPlan, UnitMove, etc.
        │   ├── world_view.py       # WorldView (fog-of-war filtered)
        │   ├── baseline_ai.py      # BaselineAI personality
        │   ├── difficulty.py       # Difficulty, knob defaults
        │   ├── strategic/
        │   │   ├── ai.py           # StrategicAI composition
        │   │   ├── intel.py        # IntelService, Threat, Opportunity, Theater
        │   │   ├── strategist.py   # Strategist ABC, DeterministicStrategist
        │   │   ├── goals.py        # Goal hierarchy
        │   │   ├── operational.py  # OperationalPlanner, TaskForce
        │   │   ├── tactical.py     # TacticalExecutor + behaviors registry
        │   │   ├── behaviors.py    # Behavior ABC + concrete behaviors
        │   │   └── memory.py       # AIMemory
        │   └── llm/
        │       ├── ai.py           # LLMAI personality
        │       ├── strategist.py   # LLMStrategist
        │       ├── client.py       # LLMClient (Anthropic SDK wrapper)
        │       ├── prompt.py       # PromptBuilder
        │       └── parser.py       # ResponseParser
        ├── persistence/
        │   ├── save_manager.py     # SaveManager
        │   ├── schema_v1.py        # v1 (de)serializers
        │   └── migration.py        # cross-version upgraders
        ├── tui/
        │   ├── app.py              # EmpireApp (Textual.App root)
        │   ├── widgets/
        │   │   ├── map_widget.py   # MapWidget (the main display)
        │   │   ├── city_list.py
        │   │   ├── unit_list.py
        │   │   ├── statusbar.py
        │   │   └── log_panel.py
        │   ├── screens/
        │   │   ├── title.py        # Title / new-game screen
        │   │   ├── play.py         # Main play screen
        │   │   └── game_over.py
        │   ├── modals/
        │   │   ├── city_production.py
        │   │   ├── help.py
        │   │   └── confirm.py
        │   └── commands.py         # InputBindings, Command pattern
        └── tests/                  # pytest tree mirrors src layout
```

**Dependency arrows are one-way:**
`tui → ai → contracts → core` and `tui → core`, with `combat`, `mapgen`, `pathfinding`, `persistence`, `events` all sitting on top of `core`. **`core` depends on nothing inside the package.** `contracts` depends only on `core`. This is the rule that keeps the domain testable in isolation and the AI vocabulary out of the domain layer.

---

## 2. Core domain (`empire.core`)

The objects below are mutable; the engine modifies them in place under controlled flow (turn manager). External callers (AI, UI) see them through references but mutate only via methods.

### `Coord` (value type)
```python
@dataclass(frozen=True, slots=True)
class Coord:
    x: int
    y: int

    def step(self, d: Direction) -> Coord
    def neighbors(self) -> Iterator[Coord]      # all 8
    def chebyshev_to(self, other: Coord) -> int
```

### `Direction` (enum)
8-direction enum (N, NE, E, SE, S, SW, W, NW). `Direction.offsets() -> Iterator[tuple[int,int]]` for iteration.

### `TerrainKind` (enum)
`LAND`, `WATER`, `CITY`. (3 values; see [`01-game-rules-spec.md`](01-game-rules-spec.md) §1.2.)

### `Tile`
```python
@dataclass(slots=True)
class Tile:
    coord: Coord
    terrain: TerrainKind
    city: City | None = None
    on_board: bool = True           # False for the unwalkable 1-cell border
```

**`Unit.coord` is the single source of truth for unit position.** Tiles do not hold an occupant list. To query "what's at this tile", call `Map.units_at(coord)`, which reads from a spatial index (`dict[Coord, list[UnitId]]`) that `Map` maintains as units are placed/moved/removed. The index is derived state — it can desync if `Unit.coord` is written directly, so `Unit.coord` is read-only externally and only `Map.move_unit()` calls the package-private `Unit._set_coord()`. This makes the spatial index a safe cache rather than a second source of truth.

### `Unit` (abstract)
```python
class Unit(ABC):
    id: UnitId
    kind: ClassVar[UnitKind]        # ARMY, FIGHTER, ... — set per subclass
    owner: Player                   # read-only after construction; ownership changes via City.capture()
    _coord: Coord                   # private; read via .coord property, write only via Map
    hits: int                       # current; max_hits is class-level
    range: int                      # remaining fuel/range (INFINITY for non-air)

    @property
    def coord(self) -> Coord: return self._coord
    # No public setter. Map.move_unit() is the sole mutation path:
    def _set_coord(self, c: Coord) -> None: ...   # package-private; called by Map only

    # class-level attributes (overridden per subclass)
    max_hits: ClassVar[int]
    speed: ClassVar[int]
    strength: ClassVar[int]
    capacity: ClassVar[int]
    base_range: ClassVar[int]
    build_time: ClassVar[int]
    legal_terrain: ClassVar[frozenset[TerrainKind]]
    symbol: ClassVar[str]           # 'A', 'F', ...

    # cargo support — only Transport and Carrier override
    def cargo(self) -> list[Unit]
    def can_carry(self, other: Unit) -> bool
    def load(self, other: Unit) -> None
    def unload(self, other: Unit, to: Coord) -> None

    # damage-scaled accessors
    def moves_this_turn(self) -> int          # ceil(speed * hits / max_hits)
    def effective_capacity(self) -> int       # capacity * hits / max_hits

    @abstractmethod
    def attack_preferences(self) -> str       # the data.c attack-priority string
```

Concrete subclasses: `Army`, `Fighter`, `Patrol`, `Destroyer`, `Submarine`, `Transport`, `Carrier`, `Battleship`, `Satellite`. Each sets its class-level attributes from the canonical table. Behavior overrides:
- `Transport` and `Carrier` override the cargo methods (and only those — different rules around what they carry, see capacity/damage logic).
- `Fighter` and `Satellite` override range-tracking and the "crash and burn" logic in `step()`.
- `Satellite` overrides `attack_preferences` to forbid attack (and is special-cased in combat — cannot be attacked).

### `UnitKind` (enum)
Pure enum mirroring the class hierarchy (`ARMY=0`, etc.). Used wherever we need a type discriminator at the data level (save files, weight tables). The mapping `UnitKind → Unit class` lives in a registry (`unit.py:UNIT_REGISTRY`).

### `City`
```python
@dataclass(slots=True)
class City:
    id: CityId
    coord: Coord
    owner: Player | None            # None = neutral
    production: ProductionState
    default_orders: dict[UnitKind, OrderKind]  # per-kind default order on production
```

### `ProductionState`
Encapsulates the production penalty logic so it can't be forgotten:
```python
class ProductionState:
    building: UnitKind | None
    work: int                       # may be negative (penalty)

    def set_target(self, kind: UnitKind, rules: RuleSet) -> None:
        # applies build_time/5 penalty if kind != self.building
    def tick(self) -> None
    def ready(self) -> bool         # work >= build_time of current target
    def consume(self) -> None       # work -= build_time, building -> producable
```

### `Map`
Owns the grid. Single source of truth for tile state.
```python
class Map:
    width: int
    height: int
    _tiles: dict[Coord, Tile]       # or 2D list keyed by (x,y)

    def tile(self, c: Coord) -> Tile
    def in_bounds(self, c: Coord) -> bool
    def neighbors(self, c: Coord) -> Iterator[Tile]
    def terrain_at(self, c: Coord) -> TerrainKind
    def units_at(self, c: Coord) -> Sequence[Unit]
    def cities(self) -> Iterable[City]

    def place_unit(self, u: Unit, c: Coord) -> None
    def remove_unit(self, u: Unit) -> None
    def move_unit(self, u: Unit, to: Coord) -> None    # validates with RuleSet
```

### `ViewMap` + `RememberedTile`
Per-player fog-of-war state:
```python
@dataclass
class RememberedTile:
    coord: Coord
    terrain: TerrainKind
    remembered_at: int               # turn last seen
    last_units: list[UnitSnapshot]   # stale snapshot
    last_city_owner: Player | None

class ViewMap:
    player: Player
    remembered: dict[Coord, RememberedTile]
    visible: set[Coord]              # what's actually scannable THIS turn

    def update_from_scan(self, scanned: Iterable[Coord], real_map: Map, turn: int) -> None
    def seen(self, c: Coord) -> bool
    def render_char(self, c: Coord) -> str   # for UI: ' ', '+', '.', '*', 'O', 'X', unit letters
```

### `Player`
```python
@dataclass
class Player:
    id: PlayerId
    name: str
    is_ai: bool
    view: ViewMap
    color: Color                      # for UI

    def cities(self, game: Game) -> Iterable[City]
    def units(self, game: Game) -> Iterable[Unit]
```

**`Player` does not own its controller.** AI controllers live in `Game.controllers: dict[PlayerId, AIController]`, populated at game construction. The `AIController` protocol is declared in `core` (it depends on nothing AI-specific — just `WorldView` and `TurnPlan` types); concrete implementations live in `empire.ai`. This keeps `core` free of imports from `ai`.

### `RuleSet`
Coherent bundle of rule values. **Every rule consultation goes through this.** No hardcoded constants in game logic. Per D-003, `RuleSet`s ship as named presets that have been validated to play well together — not as user-assembled checkbox grids.

```python
@dataclass(frozen=True)
class RuleSet:
    name: str                                      # e.g., "STANDARD", "MODERN"
    # base constants
    map_profile: MapProfile
    num_cities: int
    fighter_base_range: int
    satellite_range: int
    production_change_penalty_divisor: int = 5     # build_time/5

    # behavioral knobs (values vary per preset)
    allow_unit_stacking: bool = False
    army_capture_city_deterministic: bool = False  # original: irand(2); on: always succeed
    asymmetric_combat_bonus: float = 0.0
    seven_terrain_types: bool = False
    transport_escort_required_for_unload: bool = False
    # ... add as needed

# Shipped presets
STANDARD = RuleSet(name="STANDARD", ...)           # baseline values from 01-game-rules-spec.md
# Future: MODERN, STACKED_COMBAT, etc. — each play-tested as a whole.
```

Saves store `rules.name` (and the full struct, for forward compat); load looks up the preset by name. Custom user presets are not exposed in v1.

### `Game`
Root aggregate. Holds the whole state. Single point of authoritative mutation.
```python
class Game:
    rules: RuleSet
    map: Map
    players: list[Player]
    cities: dict[CityId, City]
    units: dict[UnitId, Unit]
    turn: int
    rng: random.Random               # seeded; persisted with state
    event_bus: EventBus
    turn_manager: TurnManager

    @classmethod
    def new_game(cls, rules: RuleSet, seed: int | None = None, ...) -> Game
    def run_turn(self) -> None       # advances one full round
    def is_over(self) -> bool
    def winner(self) -> Player | None
    def save(self, path: Path) -> None
    @classmethod
    def load(cls, path: Path) -> Game
```

### `TurnManager`
Orchestrates phases. Doesn't own state; mutates `Game` under instruction.
```python
class TurnManager:
    game: Game

    def run_round(self) -> None:
        # phases: ProductionPhase, MovementPhase(user), MovementPhase(ai),
        #         CombatResolution (interleaved with movement), EndOfTurnCheck
```

Each phase is a method (or its own small class if it grows complex). The order per round: user moves, then AI moves, then end-of-round bookkeeping + endgame check. See [`01-game-rules-spec.md`](01-game-rules-spec.md) §7.

---

## 3. Events & UI plumbing (`empire.events`)

A lightweight event bus decouples model from view. Game logic emits events; the TUI subscribes.

```python
@dataclass(frozen=True)
class UnitMovedEvent:
    unit_id: UnitId
    from_: Coord
    to: Coord

@dataclass(frozen=True)
class CombatEvent:
    attacker_id: UnitId
    defender_id: UnitId
    blows: list[CombatBlow]
    outcome: CombatOutcome

@dataclass(frozen=True)
class CityCapturedEvent:
    city_id: CityId
    by: PlayerId
    transferred_units: list[UnitId]
    destroyed_units: list[UnitId]

class EventBus:
    def publish(self, event: object) -> None
    def subscribe(self, event_type: type, handler: Callable) -> None
```

Events are immutable dataclasses; subscribers don't mutate them. Used for both UI updates and replay support (see §12 deferred items).

---

## 4. Combat (`empire.combat`)

### `CombatResolver`
Owns the per-blow attrition algorithm from [`01-game-rules-spec.md`](01-game-rules-spec.md) §4.2. Returns a `CombatOutcome` plus the per-blow log (consumed by `CombatEvent`).

### `CombatEvaluator`
Pure prediction. Given two units (or hypothetical units) and an RNG, computes win probability and expected damage. Used by the smart AI to decide whether to engage. **Does not mutate.**

```python
class CombatEvaluator:
    @staticmethod
    def win_probability(attacker: Unit, defender: Unit) -> float
    @staticmethod
    def expected_outcome(attacker: Unit, defender: Unit) -> ExpectedOutcome
```

---

## 5. Map generation (`empire.mapgen`)

### `MapGenerator` ABC
```python
class MapGenerator(ABC):
    @abstractmethod
    def generate(self, profile: MapProfile, rng: random.Random) -> tuple[Map, list[City]]
```

### `HeightFieldMapGenerator`
A height-field map generator designed during Phase 5. Algorithm sketch (subject to refinement):
1. Assign each cell a random initial height in `[0, H_max]`.
2. Smooth over `smooth_iterations` passes (each cell becomes the average of itself and its 8 neighbors).
3. Cells below the water-line threshold become `WATER`; cells above become `LAND`.
4. Place `num_cities` city sites on land with minimum Chebyshev spacing `min_city_distance`. If insufficient land for the required spacing, regenerate (with a retry cap).

Parameterized by `MapProfile`. Specific threshold values, smoothing window, and retry behavior are our design choices, tuned during Phase 5 playtesting.

### `MapProfile`
```python
@dataclass(frozen=True)
class MapProfile:
    width: int
    height: int
    water_ratio: int           # percentage 0-100 (water fraction target)
    smooth_iterations: int
    num_cities: int
    min_city_distance: int

# presets
SMALL    = MapProfile(50,  30, 70, 5,  25, 6)
STANDARD = MapProfile(100, 60, 70, 5,  70, 8)
LARGE    = MapProfile(150, 90, 70, 5, 140, 10)
```

---

## 6. Pathfinding (`empire.pathfinding`)

### `Pathfinder` ABC
```python
class Pathfinder(ABC):
    @abstractmethod
    def find_path(self, request: PathRequest) -> Path | None
    @abstractmethod
    def find_objective(self, request: ObjectiveSearch) -> Path | None
```

### `PathRequest`
```python
@dataclass
class PathRequest:
    start: Coord
    goal: Coord
    cost_profile: PathCostProfile
    view: ViewMap                     # AI plans against its own view, not real map
    blocking: BlockingPolicy = BlockingPolicy.RESPECT
```

### `BFSPathfinder`
Perimeter-expansion BFS with weighted objective scoring. Used by `BaselineAI` and as a fallback when full A* is overkill.

### `AStarPathfinder`
True A* with admissible Chebyshev heuristic. Used by `StrategicAI`. Optionally danger-weighted (cost penalty per cell within threat reach).

### `PathCostProfile`
```python
@dataclass(frozen=True)
class PathCostProfile:
    base: dict[TerrainKind, int]
    danger_weight: float        # 0 = ignore danger; higher = more risk-averse
    unknown_cost: int           # cost to enter an unscouted cell
    embark_cost: int
    disembark_cost: int
```

---

## 7. AI (`empire.ai`)

Full design in [`03-ai-design.md`](03-ai-design.md). Class skeletons:

```python
# ai/controller.py
class AIController(ABC):
    def plan_turn(self, view: WorldView) -> TurnPlan: ...
    def on_enemy_sighted(self, ...) -> None: ...
    # etc.

# ai/world_view.py
@dataclass
class WorldView:
    own_player: Player
    turn: int
    visible_tiles: Mapping[Coord, Tile]
    remembered_tiles: Mapping[Coord, RememberedTile]
    ...

# ai/baseline_ai.py
class BaselineAI(AIController):
    pathfinder: BFSPathfinder

# ai/strategic/ai.py
class StrategicAI(AIController):
    intel: IntelService
    feasibility: FeasibilityOracle        # cheap forward checks before goal emission
    strategist: Strategist                # plug
    operational: OperationalPlanner
    tactical: TacticalExecutor
    memory: AIMemory

# ai/strategic/feasibility.py
class FeasibilityOracle:
    """Pure forward-check service. No state; same WorldView → same answers."""
    def can_assemble(self, composition: ForceComposition, by_turn: int, view: WorldView) -> bool: ...
    def defensible(self, city: City, threat: Threat, view: WorldView) -> bool: ...
    def reachable(self, start: Coord, goal: Coord, kind: UnitKind, by_turn: int, view: WorldView) -> bool: ...

# ai/strategic/strategist.py
class Strategist(ABC):
    def plan(self, intel: IntelReport, memory: AIMemory) -> list[Goal]: ...

class DeterministicStrategist(Strategist): ...

# ai/llm/strategist.py
class LLMStrategist(Strategist):
    def __init__(self, client: LLMClient, model: ModelChoice): ...

# ai/llm/ai.py
class LLMAI(StrategicAI):
    def __init__(self, llm_client: LLMClient, model: ModelChoice, ...):
        super().__init__(strategist=LLMStrategist(llm_client, model), ...)

# ai/strategic/goals.py
class Goal(ABC): ...
class CaptureCityGoal(Goal): ...
class DefendCityGoal(Goal): ...
class ExploreAreaGoal(Goal): ...
class ProjectPowerGoal(Goal): ...
# ...

# ai/strategic/operational.py
@dataclass
class TaskForce:
    id: TaskForceId
    goal: Goal
    units: list[Unit]
    role_assignments: dict[UnitId, Role]
    rendezvous: Coord | None
    state: TaskForceState

class OperationalPlanner:
    def plan(self, goals: list[Goal], view: WorldView, memory: AIMemory) -> list[TaskForce]: ...

# ai/strategic/tactical.py
class TacticalExecutor:
    behaviors: dict[tuple[UnitKind, Role], Behavior]
    pathfinder: AStarPathfinder

    def plan_moves(self, forces: list[TaskForce], view: WorldView) -> list[UnitMove]: ...

# ai/strategic/behaviors.py
class Behavior(ABC):
    def next_move(self, unit: Unit, view: WorldView, force: TaskForce | None) -> UnitMove: ...

class ArmyAssaultBehavior(Behavior): ...
class TransportFerryBehavior(Behavior): ...
class FighterStrikeBehavior(Behavior): ...
# ... etc. (one per UnitKind × Role pair)
```

---

## 8. Persistence (`empire.persistence`)

```python
class SaveManager:
    def save(self, game: Game, path: Path) -> None
    def load(self, path: Path) -> Game

# schema_v1.py
class V1Serializer:
    SCHEMA_VERSION: ClassVar[int] = 1
    def to_dict(self, game: Game) -> dict
    def from_dict(self, payload: dict) -> Game

# migration.py
class Migration:
    from_version: int
    to_version: int
    def apply(self, payload: dict) -> dict
```

`SaveManager.load()` dispatches on `payload["schema_version"]`. Each successive schema version registers a `Migration` from the previous one, so old saves walk up the migration chain to the current version. No save is ever rejected for being "too old" — only for being "too new" (forward compat is unsolvable).

### Load order: topological, not phase-then-link

Entities cross-reference each other (`Unit.owner: Player`, `City.owner: Player`, `TaskForce.units: list[Unit]`, `Goal.target_city: City`). A naive two-phase "instantiate with IDs, then re-link" approach forces every reference field to accept a transient `Union[Id, Object]` type during load — a footgun for static typing and for any code that happens to touch a half-loaded entity.

Instead, instantiate in **topological order**, so every entity is born with its references already resolved:

1. `RuleSet` (no refs).
2. `Map` + `Tile`s (no refs to higher entities).
3. `Player`s (no refs to entities below them).
4. `City`s (refs `Player`).
5. `Unit`s (refs `Player`, `Coord`; `Map.place_unit` populates spatial index).
6. `ViewMap`s per player (refs `Player`; reads tiles).
7. `Goal`s (refs `City`/`Unit`).
8. `TaskForce`s (refs `Goal`/`Unit`).
9. `AIMemory` per AI player (refs `TaskForce`s).
10. `Game` constructed from the fully-linked aggregate.

Each step uses lookup tables built by the previous step (`players_by_id`, `cities_by_id`, etc.). No entity ever exists in a half-linked state. This is the mirror image of save's reference→ID flattening, just walked in reverse dependency order.

---

## 9. TUI (`empire.tui`)

Textual-based. Three top-level screens, several reusable widgets.

```python
class EmpireApp(App):
    """The Textual application root."""
    CSS_PATH = "empire.tcss"
    BINDINGS = [...]

    def __init__(self, game: Game, ...): ...

class TitleScreen(Screen): ...
class PlayScreen(Screen):
    """The main play UI: MapWidget center, sidebars, status bar, command line."""
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield CityListWidget(id="cities")
            yield MapWidget(id="map")
            yield UnitListWidget(id="units")
        yield StatusBarWidget(id="status")
        yield LogPanel(id="log")
        yield CommandLineWidget(id="cmd")
        yield Footer()

class GameOverScreen(Screen): ...

class MapWidget(Widget):
    """The scrollable map. Owns the cursor position. Reads from the current player's ViewMap."""
    cursor: reactive[Coord]
    def render_cell(self, c: Coord) -> Text: ...
    def on_key(self, event: events.Key) -> None: ...     # arrow keys move cursor
    def on_mouse_down(self, event: events.MouseDown) -> None: ...
```

**Discipline:** widgets read from the model but mutate only via commands posted to a queue / dispatcher. They never call `game.run_turn()` directly. The command pattern keeps "what the player asked to do" auditable and testable.

```python
class Command(ABC):
    def execute(self, game: Game) -> CommandResult: ...

class MoveUnitCommand(Command): ...
class SetCityProductionCommand(Command): ...
class EndTurnCommand(Command): ...
class SaveGameCommand(Command): ...
class QuitCommand(Command): ...
```

Input bindings live in `tui/commands.py`. Each binding maps a key (e.g., `M`) to a command factory.

---

## 10. Dependency rules (enforced via imports — and lint)

```
core           <-- nothing in this project
contracts      <-- core
combat         <-- core
mapgen         <-- core
pathfinding    <-- core
events         <-- core
persistence    <-- core, contracts, ai, combat (for re-attaching after load)
ai             <-- core, contracts, combat, pathfinding
tui            <-- core, contracts, ai (for AI selection at game start), events
```

`contracts` holds the AI-facing vocabulary (`AIController`, `WorldView`, `TurnPlan`, `Surprise`) so `core` stays free of AI concepts while `ai` and `tui` can still type-check controller wiring.

Anything circular is a smell — surface it during code review. `import-linter` or a custom test enforcing the matrix above is cheap insurance.

---

## 11. Testing strategy (per layer)

- **`core`** — pure unit tests. No mocks needed (no external deps). Use `random.Random` with fixed seeds for determinism.
- **`combat`** — table-driven tests against the manpage's probability table (`vms-empire.6:218-228`). Run many seeded trials to verify our distribution matches.
- **`mapgen`** — golden tests (fixed seed → expected map summary stats). Don't compare full maps; compare summaries (cell count by terrain, city count, continent count).
- **`pathfinding`** — small handcrafted maps with known shortest paths.
- **`ai`** — primarily integration tests: play `StrategicAI` vs `BaselineAI` for N games at a fixed seed range, expect win rate > 60%. Smaller unit tests on `IntelService` (deterministic given a `WorldView`) and `CombatEvaluator`.
- **`persistence`** — round-trip every schema version. For each released schema, keep a golden save in `tests/fixtures/`.
- **`tui`** — Textual has a pilot/snapshot mode. Snapshot tests of widget rendering for a handful of canned game states.
- **`llm`** — replay-mode tests using recorded transcripts. Mock `LLMClient` returning canned responses. Validate prompt-building (golden prompts) and response parsing.

---

## 12. Refinement deferred until implementation

These are NOT decided yet — flagged for revisit when we start writing code:

1. **City `default_orders` dict shape.** Classic uses an int per piece type. Worth typing more strictly?
2. **Reactive vs polled UI updates.** Textual supports both; pick the simpler one for v1.
3. **Mouse interactions.** Defer to post-v1 unless they fall out for free.
4. **Replay system.** Plumb event bus early; implement actual playback later if there's demand.

Resolved during design review (see also `03-ai-design.md` for AI-side resolutions):
- ~~`Tile.occupants` vs `Unit.coord`~~ → `Unit.coord` canonical and read-only; `Map` maintains derived spatial index; `Map.move_unit()` is the sole mutation path.
- ~~`Player.controller`~~ → moved to `Game.controllers`; `AIController` protocol in `contracts`.
- ~~AIController protocol location~~ → new `empire.contracts` package between `core` and `ai`.
- ~~Save load order (two-phase typing problem)~~ → topological-order load; no transient `Union[Id, Object]` types.
- ~~User-assembled rule toggles~~ → named `RuleSet` presets only.
- ~~Async-ness of LLM calls~~ → UI blocks during inference with a visible "thinking" indicator. Simpler than async plumbing; acceptable since turn-based gameplay already has a wait point. Engine remains synchronous.

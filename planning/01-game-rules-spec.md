# Game Rules Specification

The rules of the game, in our own terms. Inspired by the genre Walter Bright created with *Empire* (1977); see [`00-project-notes.md`](00-project-notes.md).

Numerical values are our design choices, subject to playtesting. Values marked `(TBD)` are decided during the relevant implementation phase. The implementer should treat this document as canonical — if it's ambiguous, fix the spec.

---

## 1. World

### 1.1 Map

The world is a 2D rectangular grid of square cells. Standard dimensions are width × height = 100 × 60 cells; the implementation supports configurable sizes via `MapProfile`. Cells along the outermost ring of the grid are unwalkable border (no unit or city may occupy them). Movement is 8-directional (orthogonal + diagonal); diagonal and orthogonal moves cost the same (one movement point).

### 1.2 Terrain

Each interior cell has one of three terrain kinds:

- **Land** — passable by land units (Army). Impassable to sea units. Air units pass over.
- **Water** — passable by sea units. Impassable to Army. Air units pass over.
- **City** — a special cell occupied by a `City` object. Both land and sea units can enter (the city is treated as land for Army, and as a port for sea units adjacent to water).

Terrain is fixed at game start by map generation (see §6) and does not change during play.

### 1.3 Cities

Cities are the production centers of the game. There are `num_cities` cities on the map, scaled with map area (default density: roughly one city per 80 cells). Each city:

- Has a unique identity and a fixed coordinate.
- Has an owner: a player, or `neutral` (no owner).
- Holds production state: which unit type is being built, and accumulated work toward completion.
- May hold a stack of units; for v1, stacking is restricted to 1 unit per cell (unless `allow_unit_stacking` is enabled in the active `RuleSet`).

Cities are captured by Army units (and only Army units) entering the cell — see §4.5.

---

## 2. Units

### 2.1 Unit types

The game has the following unit types, organized by domain:

| Kind         | Domain | Role |
|--------------|--------|------|
| Army         | Land   | Ground assault; only unit that captures cities. |
| Fighter      | Air    | Fast, range-limited; scout and air strike. |
| Patrol       | Sea    | Light, fast naval unit. |
| Destroyer    | Sea    | Anti-submarine + escort. |
| Submarine    | Sea    | Stealthy ambush against shipping. |
| Transport    | Sea    | Carries Army cargo across water. |
| Carrier      | Sea    | Carries Fighter cargo; mobile airbase. |
| Battleship   | Sea    | Heavy capital ship. |
| Satellite    | Orbital | Reconnaissance; cannot attack or be attacked. |

Specific numerical attributes (hit points, speed, strength, build time, range) are unit-type design choices made during Phase 2. They should be chosen for game balance, not by reference to any prior product. Suggested *qualitative* relationships:

- **Hit points** scale with hull weight: Submarine (low) < Patrol < Destroyer < Carrier ≈ Transport < Battleship (highest); Army and Fighter are light.
- **Speed**: Patrol > Destroyer > Battleship > Carrier ≈ Transport; Fighter is the fastest unit overall; Army is slow.
- **Strength** (combat power per blow): Battleship > Destroyer > Carrier > Patrol > Submarine; Army strong vs Army, weak vs ships.
- **Build time**: Army shortest; Carrier/Battleship longest.
- **Range** (air units only): Fighter has a fuel range; runs out of fuel → crashes. Satellite has its own range model — fixed lifetime in turns, then deorbits.

Final numerical values land in Phase 2 with rationale per choice.

### 2.2 Cargo

Two unit types carry other units:

- **Transport** carries Army cargo only.
- **Carrier** carries Fighter cargo only.

Cargo capacity is a fixed per-type number. Cargo units occupy the carrier's cell (they are not on the map independently while aboard). Loading and unloading happen during movement adjacent to the carrier; specifics in §3.4.

### 2.3 Damage and effective capability

Units have a maximum HP (`max_hits`) and a current HP (`hits`). When `hits < max_hits`, both the unit's `moves_this_turn` and its `effective_capacity` (for carriers) are reduced proportionally — round-up — so a unit at half HP gets roughly half its speed and capacity. The exact formula is `ceil(stat * hits / max_hits)`.

Units repair when in friendly cities (regenerate 1 HP per turn while stationary in a friendly city).

### 2.4 Satellites

Satellites are special:

- Launched from a city (production).
- Move along a fixed orbital trajectory (one cell per turn in a chosen direction, wrapping at map edges or bouncing — implementation choice; default: bounce off edges).
- Cannot attack.
- Cannot be attacked.
- Have a fixed lifetime (`base_range` turns) after which they deorbit and disappear.
- Provide vision of every cell they pass through (and the cell they currently occupy) to their owner.

---

## 3. Movement

### 3.1 Per-turn movement budget

Each unit gets `moves_this_turn()` movement points per turn (see §2.3 for damage scaling). One point is spent per cell entered. A unit may move up to its budget; remaining points may not be carried over.

### 3.2 Terrain rules

A unit can only enter a cell whose terrain is in its `legal_terrain` set. The standard rules:

- Army: `LAND`, `CITY`.
- Fighter: any (air).
- Patrol, Destroyer, Submarine, Transport, Carrier, Battleship: `WATER`, and `CITY` only when the city is adjacent to water (treated as a port).
- Satellite: orbits; ignores terrain.

### 3.3 Collisions

When a unit attempts to enter a cell occupied by another unit:

- **Same-player unit:** the move is rejected (unless `allow_unit_stacking` is enabled).
- **Enemy unit:** combat is triggered. See §4.

### 3.4 Loading and unloading

- **Load:** A friendly Army unit may move into the cell of a friendly Transport (if Transport has cargo capacity). This consumes one movement point from the Army and uses no cargo capacity beyond the slot. The Army is now aboard.
- **Unload:** A unit aboard a Transport may move from the Transport's cell to an adjacent legal cell, using one movement point from the unloading unit's *next* turn budget (i.e., Army cannot load and unload in the same turn).
- Same rules apply for Fighter ↔ Carrier.

### 3.5 Fighter fuel

Fighters have a `range` counter, decremented by 1 per cell moved. When `range` reaches 0 while the Fighter is over non-friendly-Carrier non-friendly-City terrain, the Fighter is lost. A Fighter that lands at a friendly city or Carrier refuels (range reset to `base_range`).

---

## 4. Combat

### 4.1 Triggering

Combat is triggered when a unit attempts to enter a cell occupied by an enemy unit. The attacker pays the movement point. Combat resolves before the move is recorded.

### 4.2 Attrition model

Combat is a **per-blow attrition loop**. On each blow:

1. With probability `p`, the defender loses 1 HP.
2. Otherwise, the attacker loses 1 HP.
3. Repeat until one side reaches 0 HP.

The probability `p` is a function of the matchup, derived from each unit's `strength` and the attack-preference relationships in §4.3. Specific formulas land in Phase 6.

The unit that reaches 0 HP is destroyed. The survivor moves into the contested cell, retaining its current HP (which may now be very low).

### 4.3 Attack preferences

Each unit type has an ordered list of which targets it engages best against — its "attack preferences." This is a string of unit-type characters in order from "most prefers to fight" to "least prefers to fight," with the unit's combat strength against each target derived from where the target sits in this ordering.

Example: an Army is best against other Armies and Transports; it is poor against Battleships. The Battleship is best against ships; weaker against Army.

The exact tables are a design choice (Phase 6).

### 4.4 Stacked combat

When `allow_unit_stacking` is enabled, multiple units may share a cell. Combat against a stack proceeds one-on-one (top-of-stack first) until the attacker is repelled or the stack is destroyed.

### 4.5 City capture

When an Army successfully moves into an enemy or neutral city's cell (combat resolved, defenders defeated):

- The city's ownership transfers to the Army's player.
- Any units stationed inside the city are destroyed.
- **The Army is consumed by the capture**: it disbands into the city at capture time, becoming its abstract defence. It never occupies the cell as a board unit. (Decided 2026-06-12, replacing the earlier "occupies, then disbands at turn-end" wording — the assault consumes the army, win or lose.)
- The new owner inherits the city's production state (the in-progress build continues; new owner may change target, paying the change penalty — see §5.2).

Under `army_capture_city_deterministic = false` (the default), city capture succeeds on a probability check (50%) — even after defeating the defender, the city may "resist" and the Army is destroyed instead. With the rule on, capture is deterministic. Either way the Army is gone: success disbands it into the city; failure destroys it.

### 4.6 Ground bombardment

Surface warships can shell adjacent shore and air targets — the mechanism by which a navy projects power onto the coast.

- **Who:** Battleship, Destroyer, Patrol (surface gun platforms). Submarines (ship hunters), Carriers (project power through their fighters), and Transports (unarmed) cannot bombard.
- **When:** once per turn, as the ship's entire action that turn (re-laying the guns costs the turn). The ship must have **at least 2 HP** — it always reserves its last point, so a bombardment can never sink the firing ship. (A Patrol, at 2 HP, fires once and drops to 1; it must repair before it can fire again.)
- **Target:** one cell at Chebyshev distance 1. The shot ignores terrain (it is a strike, not a move) and the firing ship does **not** move. Only enemy units are valid; satellites are never targetable.
- **Resolution** — the salvo strikes one occupant, by priority **ship → fighter → army**:
  - An **army or fighter** is destroyed outright regardless of HP; the firing ship takes exactly **1 HP** of damage.
  - A **ship** (only ever a hull docked in the shelled city) is resolved as ordinary combat (§4.2) instead — attrition damage to both, the firing ship staying in place. The flat 1-HP cost does not apply.
- **Cities:** a city has no HP and cannot be captured by gunfire (only Armies capture cities, §4.5), but its garrison is **not** shielded — shelling a coastal city hits the units inside it. Because a docked ship is the priority target, a berthed hull soaks the salvo and effectively screens the airbase behind it until it is gone.
- **Out of scope:** open-water ship-versus-ship engagements are unchanged — they remain ordinary move-in combat (§4.1). Bombardment is not a ranged anti-ship weapon; a lone ship on open water is not a bombardment target.

The strategic effect: a warship denies the adjacent coastline to enemy armies and fighters, enabling combined-arms landings (shell the beach clear, then unload). But each salvo costs 1 HP, restored only by berthing in a friendly dry-dock (§5.4) at +1 HP/turn — so bombardment is a finite, refreshable resource.

---

## 5. Production

### 5.1 Per-turn tick

Each turn, each owned city accumulates one production point toward its current build target. When accumulated work ≥ that target's `build_time`, the unit is produced: it appears **on the city's cell** and accumulated work resets to zero.

A produced unit may share the city cell even when something is already there — stacking on a city cell is permitted as a transient state regardless of `allow_unit_stacking`. The city's support limits (§5.4) decide, at turn-end, how many units may remain; the rest are disbanded. Production therefore never stalls on a full cell.

### 5.2 Changing production

The player (or AI) may change a city's production target at any time. Doing so imposes a setback: accumulated work is reduced by `current_target.build_time / production_change_penalty_divisor` (default divisor: 5). The penalty is to discourage thrash and reward commitment. The accumulator may go negative.

### 5.3 Default orders

Each city may have a default order per unit kind: what to do with the unit immediately upon production. Options are `SENTRY` (sit in the city), `MOVE_TO(coord)` (head to a destination), or `ATTACK_NEAREST_ENEMY`. The implementation maintains this per-city, per-unit-kind map.

**Unset is not `SENTRY`.** When a city has no explicit default for a kind, the produced unit gets **no standing order** — it awaits orders, entering the player's order cycle (or, for an AI, its controller decides). Auto-sentrying produced units would mean the player is never prompted for them and the AI would treat them as already-handled. A city must be *explicitly* set to `SENTRY` for its output to hold station.

### 5.4 City support limits (garrison / airbase / dry-dock)

A city supports a limited number of friendly units resting on its cell, by category. At each player's **turn-end**, units of a category beyond its limit are **disbanded** (oldest keep their slots; newest excess are removed).

| Category | Limit | Notes |
|---|---|---|
| Army (land) | **0** | Armies may never garrison a friendly city — a garrison would make the city effectively uncapturable. Consequently an army may **not move into an already-friendly city** (capturing an enemy/neutral city is still legal: it isn't friendly until taken — and the conqueror disbands into the city at capture time, §4.5). The only way an army stands on a friendly city is production with nowhere to go; a freshly produced unit is exempt for its birth turn (its owner planned before it existed) and is disbanded at its **next** turn-end if it hasn't marched out. |
| Fighter (air) | **8** | The city is an airbase, holding the same number of fighters as a Carrier. |
| Sea | **1** | The city is a dry-dock: one ship may berth and repairs +1 HP/turn (§2.3). A ship in dry-dock can neither **load nor unload** cargo. |

Satellites are exempt (they orbit rather than dock; §2.4).

---

## 6. Fog of war

### 6.1 The view map

Each player has a `ViewMap` — their private belief about the world's state. A `ViewMap` tracks:

- `visible`: the set of cells this player can currently see this turn.
- `remembered`: a map of cells the player has seen *at some point*, holding the last-known state (terrain, what was there, when last seen).

### 6.2 Vision sources

A cell is in `visible` this turn if:

- A unit owned by the player is currently within scan radius of that cell (scan radius varies by unit kind — Satellites and Fighters see widely; Submarines see narrowly).
- A city owned by the player is within scan radius (cities see their immediate vicinity).

### 6.3 Remembered tiles

When a previously-visible cell falls out of view, its last-observed state is committed to `remembered`, tagged with the turn it was last seen. Stale enemy units in remembered cells are *hypothetical* — the enemy may have moved.

### 6.4 The real map and the view map

The game engine maintains the authoritative `Map`. Players (and AIs) interact with the world only through their `ViewMap` — they do not have access to the real `Map` directly. This is enforced by `WorldView` in `empire.contracts`.

The `fog_cheat` toggle in `RuleSet` exists for testing/debugging and grants the AI access to the real `Map`. It is OFF by default at all difficulty levels.

---

## 7. Turn structure

A round consists of two player turns plus end-of-round bookkeeping:

1. **Player 1 turn:**
   1. Production phase: tick every Player 1 city.
   2. Movement phase: Player 1 issues moves; engine resolves them one at a time, recomputing visibility after each move.
   3. Endgame check.
2. **Player 2 turn:** same structure.
3. **End of round:** turn counter advances; any time-decaying state (Satellite lifetime, etc.) ticks.

In single-player vs AI, Player 1 is the human and Player 2 is the AI (or vice versa per setup).

---

## 8. Victory and defeat

A player loses when they have **zero cities** at the end of any turn. The remaining player wins.

If both players reach zero cities simultaneously (rare; only possible under stacking rules where two armies trade their last cities in the same round), the game is a draw.

There is no time limit by default. A `turn_cap` `RuleSet` toggle exists for testing — when set, the game ends in a draw at the cap.

---

## 9. Setup

### 9.1 Map generation

The map is generated by a `MapGenerator`. The standard generator produces a **height-field map** (see [`04-class-hierarchy.md`](04-class-hierarchy.md) §5 for class details; algorithm chosen during Phase 5). Briefly:

- Random initial height values per cell.
- Smoothing passes to produce continents and bays.
- Cells below a water-line threshold are `WATER`; above are `LAND`.
- City sites are placed on land with a minimum Chebyshev spacing, regenerating the height field if too few sites are available.

The exact algorithm — number of smoothing passes, threshold values, spacing minimums — is designed during Phase 5 as our choice.

### 9.2 Starting positions

At game start, each player is assigned one city as their capital. The capitals are placed on different landmasses where possible. Difficulty level affects continent assignment: the `continent_quality` `RuleSet` knob biases which continent the AI gets (smaller landmass = harder for the AI).

**Capital-eligible continents.** A continent is valid for hosting a starting capital iff:

- It contains **at least 3 cities** — the capital plus ≥ 2 others. This guarantees the starting player has nearby capture targets to fuel early-game expansion without immediately needing naval transport.
- At least one city on the continent (other than the capital itself, or at minimum at least one in addition to the capital) is **ocean-coastal** (adjacent to an on-board water cell connected to the open ocean). This lets the player build and host transports without first having to conquer an inland city.

This is a *capital-selection* constraint, not a map-generation constraint. Map generation produces continents and cities under its own invariants (see `planning/05-implementation-plan.md` Phase 5 and the `HeightFieldMapGenerator` docstring); capital selection then chooses where to place starting capitals from among the continents that satisfy the above. If fewer than `num_players` continents qualify, the map is rejected and a fresh one is generated.

All other cities begin neutral. They have no production; once captured, they begin producing per the new owner's settings.

### 9.3 Starting units

Each player starts with one Army in their capital. No other units exist at game start.

---

## 10. Rulesets

The game ships with named `RuleSet` presets — curated bundles of rule values play-tested as a whole. See [`02-design-decisions.md`](02-design-decisions.md) D-003. The initial preset is:

- **`STANDARD`** — the rules described in this document with their default values. The baseline experience.

Future presets layered on `STANDARD` may toggle:

- `allow_unit_stacking` — multi-unit cells.
- `army_capture_city_deterministic` — remove the 50% capture roll.
- `asymmetric_combat_bonus` — give the attacker or defender a positional edge.
- `seven_terrain_types` — richer terrain (mountains, forests, etc.) with movement costs.
- `transport_escort_required_for_unload` — Transports cannot unload Army without an adjacent friendly escort.

Each preset is named, validated end-to-end, and ships as a coherent bundle. There is no user-assembled "à la carte" rule mixing in v1.

---

## 11. Open spec questions for implementation phases

The following are intentionally left open here and resolved in their respective implementation phases:

- **Numerical values** for unit attributes (Phase 2).
- **Combat probability formula** linking `strength` to per-blow `p` (Phase 6).
- **Mapgen algorithm specifics** (Phase 5).
- **City scan radius** (Phase 2 / Phase 8).
- **Default orders catalog** beyond the three listed in §5.3 (Phase 8).
- **Tie-breaking when multiple cities produce on the same turn into a contested cell** (Phase 8).

Each resolution is the implementer's design choice, documented in the relevant phase's commit message and (for substantive choices) added back into this spec.

# FARSHORE — Game Rules

The rules of FARSHORE. Section numbers (§) are referenced throughout the codebase.

---

## 1. World

### 1.1 Map

The world is a 2D rectangular grid of square cells. Standard dimensions are width × height = 100 × 60 cells; other sizes are configurable. Cells along the outermost ring of the grid are unwalkable border (no unit or city may occupy them). Movement is 8-directional (orthogonal + diagonal); diagonal and orthogonal moves cost the same (one movement point).

### 1.2 Terrain

Each interior cell has one of three terrain kinds:

- **Land** — passable by land units (Army). Impassable to sea units. Air units pass over.
- **Water** — passable by sea units. Impassable to Army. Air units pass over.
- **City** — a special cell occupied by a city. Both land and sea units can enter (the city is treated as land for Army, and as a port for sea units adjacent to water).

Terrain is fixed at game start by map generation (see §9) and does not change during play.

### 1.3 Cities

Cities are the production centers of the game. The number of cities scales with map area (default density: roughly one city per 80 cells). Each city:

- Has a unique identity and a fixed coordinate.
- Has an owner: a player, or neutral (no owner).
- Holds production state: which unit type is being built, and accumulated work toward completion.
- May hold units; stacking is restricted to 1 unit per cell (unless the `allow_unit_stacking` rule is enabled in the active ruleset).

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

Unit attributes:

| Kind       | Hit points | Speed | Strength | Cargo | Range | Build time | Scan |
|------------|-----------|-------|----------|-------|-------|------------|------|
| Army       | 1         | 1     | 1        | —     | unlimited | 5      | 2 |
| Fighter    | 1         | 8     | 1        | —     | 20 cells of fuel | 10 | 5 |
| Patrol     | 2         | 4     | 1        | —     | unlimited | 15     | 3 |
| Destroyer  | 3         | 3     | 2        | —     | unlimited | 20     | 3 |
| Submarine  | 2         | 2     | 3        | —     | unlimited | 25     | 1 |
| Transport  | 3         | 2     | 0        | 6 Armies | unlimited | 30 | 2 |
| Carrier    | 8         | 2     | 1        | 8 Fighters | unlimited | 40 | 4 |
| Battleship | 18        | 2     | 4        | —     | unlimited | 50     | 3 |
| Satellite  | 1         | 1     | 0        | —     | 50 turns of lifetime | 50 | 10 |

**Range** applies to air and orbital units only: a Fighter that runs out of fuel crashes (§3.5); a Satellite's range is its lifetime in turns, after which it deorbits (§2.4). **Scan** is the Chebyshev radius of vision (§6.2).

### 2.2 Cargo

Two unit types carry other units:

- **Transport** carries Army cargo only.
- **Carrier** carries Fighter cargo only.

Cargo capacity is a fixed per-type number. Cargo units occupy the carrier's cell (they are not on the map independently while aboard). Loading and unloading happen during movement adjacent to the carrier; specifics in §3.4.

### 2.3 Damage and effective capability

Units have a maximum HP and a current HP. When damaged, both the unit's per-turn movement and its cargo capacity (for carriers) are reduced proportionally — rounded up — so a unit at half HP gets roughly half its speed and capacity.

Units repair in friendly cities: a unit that begins and ends its turn stationary in a friendly city regenerates 1 HP per turn.

### 2.4 Satellites

Satellites are special:

- Produced in a city like any other unit, but they emerge **unlaunched**: a satellite sits in its city until its owner launches it.
- At launch, the owner chooses a one-way orbit direction — the only order a satellite ever takes. From then on it moves its full speed each turn along that fixed heading, **wrapping** at the map edges (to a satellite, the world is round). The heading never changes.
- Cannot attack, cannot be attacked, and cannot be steered after launch.
- Lifetime burns one turn per round **from the moment of production**, launched or not — a parked satellite is wasting fuel, not serving as a free radar tower. When lifetime reaches zero, the satellite deorbits and crashes.
- Provides wide vision (scan 10) of its surroundings to its owner as it passes.

---

## 3. Movement

### 3.1 Per-turn movement budget

Each unit gets a per-turn movement budget equal to its (damage-scaled, §2.3) speed. One point is spent per cell entered. A unit may move up to its budget; remaining points may not be carried over.

### 3.2 Terrain rules

A unit can only enter a cell whose terrain it can legally occupy. The standard rules:

- Army: land and cities.
- Fighter: anywhere (air).
- Patrol, Destroyer, Submarine, Transport, Carrier, Battleship: water, and cities only when the city is adjacent to water (treated as a port).
- Satellite: orbits; ignores terrain.

### 3.3 Collisions

When a unit attempts to enter a cell occupied by another unit:

- **Same-player unit:** the move is rejected (unless `allow_unit_stacking` is enabled).
- **Enemy unit:** combat is triggered. See §4.

### 3.4 Loading and unloading

- **Load:** A friendly Army unit may move into the cell of a friendly Transport (if the Transport has cargo capacity). This consumes one movement point from the Army — even onto a water cell the Army could never normally enter. The Army is now aboard.
- **Loading order (a loading dock):** A Transport or Carrier put into *Loading* holds position and sweeps adjacent eligible cargo aboard **every turn** — freshly produced units, or units that walked up — until its hold is full, at which point it wakes. See §3.6.
- **Unload:** A unit aboard a Transport may move from the Transport's cell to an adjacent legal cell. The disembark **consumes the landed unit's move for that turn** — a just-unloaded unit cannot move again until its next turn (the landing *is* its action). A unit also cannot load and unload in the same turn: it must have been aboard since a previous turn (a unit that loaded this turn already spent its move boarding).
- Same rules apply for Fighter ↔ Carrier.

### 3.5 Fighter fuel

Fighters have a fuel counter, decremented by 1 per cell moved. When fuel reaches 0 while the Fighter is anywhere other than a friendly Carrier or friendly city, the Fighter is lost. A Fighter that lands at a friendly city or Carrier refuels to full.

### 3.6 Standing orders

A unit can be given a persistent order that the engine carries out turn after turn, without prompting:

- **Sentry** — hold position and skip the order cycle until woken.
- **Heading** — walk one cell per turn in a fixed direction until interrupted.
- **Go-to** — head to a chosen destination, one leg per turn, and stop there.
- **Patrol route** (ships) — loop between two points indefinitely.
- **Explore** — head for the nearest reachable unexplored frontier, coastline first. Explorers coordinate: two units never chase the same frontier cell.
- **Return-To-Base** (fighters) — fly home to the nearest own airbase or carrier with room, re-picking the destination every turn (carriers move, cities fall). Refuses the order if no landing spot is reachable on current fuel.

Rules common to all standing orders:

- Standing orders **never auto-attack**: an ordered unit wakes one step short of an enemy rather than blundering into combat.
- Autonomous movement halts at the **edge** of a discovered hostile artillery ring (§4.7); stepping into one is always a deliberate move.
- A surprise **wakes** a unit; a surprise never puts a unit to sleep. What counts as a surprise depends on the order:
  - **Sentry / Loading** (holding position): *any* enemy inside scan range wakes the unit.
  - **Heading / go-to / patrol / explore** (moving): only **news** wakes the unit — a **new** enemy contact (one that was *not* already inside scan range when the order was issued or after its previous step), or discovering a city the player has never seen. An enemy the player could already see when giving the order never cancels it; a contact that leaves scan and later returns is news again.
- A **go-to** whose planned route turns out to be blocked — newly-revealed terrain (fog lifting onto water or an obstacle) or a parked friendly unit — **re-plans** a route to the same destination over known terrain and keeps going; it wakes only when no route remains. Headings and looping patrols have fixed geometry and wake instead. A city's MOVE_TO rally default uses the same known-terrain routing, so fresh production marches around coasts rather than into them.
- Fighters on autonomous orders wake at bingo fuel (just enough to get home).

---

## 4. Combat

### 4.1 Triggering

Combat is triggered when a unit attempts to enter a cell occupied by an enemy unit. The attacker pays the movement point. Combat resolves before the move is recorded.

### 4.2 Attrition model

Combat is a **per-blow attrition loop**. On each blow:

1. With probability `p`, the defender loses 1 HP.
2. Otherwise, the attacker loses 1 HP.
3. Repeat until one side reaches 0 HP.

The probability `p` is a function of the matchup, derived from each unit's strength and the attack-preference relationships in §4.3.

The unit that reaches 0 HP is destroyed. The survivor moves into the contested cell, retaining its current HP (which may now be very low).

### 4.3 Attack preferences

Each unit type has an ordered list of which targets it engages best against — its "attack preferences," ordered from "most prefers to fight" to "least prefers to fight." The unit's combat strength against each target is derived from where the target sits in this ordering.

Example: an Army is best against other Armies and Transports; it is poor against Battleships. The Battleship is best against ships; weaker against Army.

### 4.4 Stacked combat

When `allow_unit_stacking` is enabled, multiple units may share a cell. Combat against a stack proceeds one-on-one (top-of-stack first) until the attacker is repelled or the stack is destroyed.

### 4.5 City capture

When an Army successfully moves into an enemy or neutral city's cell (combat resolved, defenders defeated):

- The city's ownership transfers to the Army's player.
- Any units stationed inside the city are destroyed.
- **The Army is consumed by the capture**: it disbands into the city at capture time, becoming its abstract defence. It never occupies the cell as a board unit.
- The new owner inherits the city's production state (the in-progress build continues; the new owner may change target, discarding accumulated work — see §5.2).

Under the STANDARD ruleset, city capture succeeds on a probability check (50%) — even after defeating the defender, the city may "resist" and the Army is destroyed instead. Under FORTIFIED_CITIES (§10), capture is deterministic — the artillery gauntlet (§4.7) is the cost instead. Either way the Army is gone: success disbands it into the city; failure destroys it.

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

### 4.7 City artillery

Under the FORTIFIED_CITIES ruleset (§10), cities fight back. Every city — owned or neutral — has **one artillery shot per round**, with range 2 (Chebyshev).

- **Opening barrage:** each round opens with a symmetric barrage, before any player moves: every city fires at its most dangerous in-range enemy — armies first, then fighters, then other units, nearest first.
- **Deferred volley:** cities that still have their shot in hand fire again at the end of each player's segment, **after** that player has moved and scanned — so a fortified city is always discovered before it fires. There is no reactive fire during your movement; you always see the gun before it sees you shoot.
- **Effect of a shot:** each shot has a 50% chance to deal 1 HP of damage — an instant kill against a 1-HP army or fighter. Hit or miss, the shot may **pin** its target for the round (50% chance): a pinned land or air unit loses its move; a pinned ship has its move halved. Firing reveals the city to its victim.
- **Autonomous movement** (standing orders, go-to legs) halts at the **edge** of a discovered hostile artillery ring. Stepping into a ring is always a deliberate, player-issued move.

The strategic effect: cities are no longer free grabs for a lone army. A coordinated assault — several attackers arriving together, so the single shot per round cannot pin them all — reliably lands someone; under this ruleset the capture itself is deterministic (§4.5), because running the gauntlet was the cost.

---

## 5. Production

### 5.1 Per-turn tick

Each turn, each owned city accumulates one production point toward its current build target. When accumulated work reaches that target's build time, the unit is produced: it appears **on the city's cell** and accumulated work resets to zero.

A produced unit may share the city cell even when something is already there — stacking on a city cell is permitted as a transient state regardless of `allow_unit_stacking`. The city's support limits (§5.4) decide, at turn-end, how many units may remain; the rest are disbanded. Production therefore never stalls on a full cell.

### 5.2 Changing production

The player (or AI) may change a city's production target at any time. Doing so **discards all accumulated work**: effort toward one unit does not transfer to another (a half-built battleship is not partial progress toward an army). This discourages thrash and rewards commitment.

### 5.3 Default orders

Each city may have a default order per unit kind: what to do with the unit immediately upon production. Options are **sentry** (sit in the city), **move-to** (head to a destination), or **attack nearest enemy**.

**Unset is not sentry.** When a city has no explicit default for a kind, the produced unit gets **no standing order** — it awaits orders, entering the player's order cycle (or, for an AI, its controller decides). A city must be *explicitly* set to sentry for its output to hold station.

### 5.4 City support limits (garrison / airbase / dry-dock)

A city supports a limited number of friendly units resting on its cell, by category. At each player's **turn-end**, units of a category beyond its limit are **disbanded** (oldest keep their slots; newest excess are removed).

Moving into your **own** city is allowed while the mover's support category has capacity — a city is a stationary carrier for fighters, and a one-berth port for ships.

| Category | Limit | Notes |
|---|---|---|
| Army (land) | **0** | Armies may never garrison a friendly city — a garrison would make the city effectively uncapturable. Consequently an army may **never move into an already-friendly city** (capturing an enemy/neutral city is still legal: it isn't friendly until taken — and the conqueror disbands into the city at capture time, §4.5). The only way an army stands on a friendly city is production with nowhere to go; a freshly produced unit is exempt for its birth turn and is disbanded at its **next** turn-end if it hasn't marched out. |
| Fighter (air) | **8** | The city is an airbase: up to 8 fighters stack and refuel there, the same complement as a Carrier. |
| Sea | **1** | The city is a dry-dock: one ship may berth and repairs +1 HP/turn (§2.3). A ship in dry-dock can neither **load nor unload** cargo. |

Satellites are exempt (they orbit rather than dock; §2.4).

---

## 6. Fog of war

### 6.1 The view map

Each player has a private view of the world — their belief about its state. It tracks:

- **Visible**: the set of cells the player can currently see this turn.
- **Remembered**: cells the player has seen *at some point*, holding the last-known state (terrain, what was there, when last seen).

### 6.2 Vision sources

A cell is visible this turn if:

- A unit owned by the player is currently within scan radius of that cell (scan radius varies by unit kind — see §2.1; Satellites and Fighters see widely, Submarines narrowly).
- A city owned by the player is within scan radius (cities see their immediate vicinity).

### 6.3 Remembered tiles

When a previously-visible cell falls out of view, its last-observed state is committed to memory, tagged with the turn it was last seen. Stale enemy units in remembered cells are *hypothetical* — the enemy may have moved.

### 6.4 The real map and the view map

The game engine maintains the authoritative map, but every player — human and AI alike — interacts with the world only through their own fogged view. The AI does not cheat: it never reads the real map.

---

## 7. Turn structure

A round consists of one segment per player plus end-of-round bookkeeping:

1. **Round opening** (artillery rulesets only): every city's artillery re-arms, last round's pins clear, and the symmetric opening barrage fires (§4.7).
2. **Each player's segment**, in order:
   1. **Production**: tick every owned city (§5.1).
   2. **Standing orders**: the engine steps every unit on a persistent order (§3.6).
   3. **Movement**: the player issues moves; the engine resolves them one at a time, recomputing visibility after each move.
   4. **Scan**: the player's fog of war updates from their final positions.
   5. **City artillery** (artillery rulesets only): the deferred volley — hostile cities with a shot still in hand fire on this player's units (§4.7).
   6. **Disband**: units over a friendly city's support limits are removed (§5.4).
3. **End of round**: the turn counter advances; satellites advance along their orbits and burn lifetime (§2.4); units resting in friendly cities repair (§2.3).

In single-player, one segment is the human's and the other is the AI's.

---

## 8. Victory and defeat

A player loses when they have **zero cities** at the end of any turn. The remaining player wins.

If both players reach zero cities simultaneously (rare; only possible under stacking rules where two armies trade their last cities in the same round), the game is a draw.

There is no time limit by default. A turn-cap rule exists — when set, the game ends in a draw at the cap.

---

## 9. Setup

### 9.1 Map generation

The map is generated as a **height-field**:

- Random initial height values per cell.
- Smoothing passes to produce continents and bays.
- Cells below a water-line threshold are water; above are land.
- City sites are placed on land with a minimum spacing, regenerating the height field if too few sites are available.

### 9.2 Starting positions

At game start, each player is assigned one city as their capital. The capitals are placed on different landmasses where possible. Difficulty level affects continent assignment: a smaller starting landmass is a harder start.

**Capital-eligible continents.** A continent is valid for hosting a starting capital iff:

- It contains **at least 3 cities** — the capital plus at least 2 others. This guarantees the starting player has nearby capture targets to fuel early-game expansion without immediately needing naval transport.
- At least one city on the continent, in addition to the capital, is **ocean-coastal** (adjacent to water connected to the open ocean). This lets the player build and host transports without first having to conquer an inland city.

If fewer continents qualify than there are players, the map is rejected and a fresh one is generated.

All other cities begin neutral. They have no production; once captured, they begin producing per the new owner's settings.

### 9.3 Starting units

Each player starts with one Army in their capital. No other units exist at game start.

---

## 10. Rulesets

The game ships with named ruleset presets — curated bundles of rule values play-tested as a whole. The shipped presets are:

- **`STANDARD`** — the classic rules described in this document with their default values. No city artillery; city capture uses the 50% resistance roll (§4.5).
- **`FORTIFIED_CITIES`** — cities defend themselves with artillery (§4.7: range 2, one shot per round, 50% hit, 50% pin), and city capture is deterministic (§4.5) — the gauntlet of fire is the cost.

Future presets layered on `STANDARD` may toggle:

- `allow_unit_stacking` — multi-unit cells.
- `asymmetric_combat_bonus` — give the attacker or defender a positional edge.
- `seven_terrain_types` — richer terrain (mountains, forests, etc.) with movement costs.
- `transport_escort_required_for_unload` — Transports cannot unload Army without an adjacent friendly escort.

Each preset is named, validated end-to-end, and ships as a coherent bundle. There is no user-assembled "à la carte" rule mixing.

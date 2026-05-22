# VMS Empire Canonical Rules — Source Reference

> Extracted from `/home/ccebelenski/projects/empire/Empire-for-VMS/` (Walter Bright's *Empire: Wargame of the Century*, VMS C port). All citations are `file:line` references into that tree. This is the authoritative classic-rules reference for the Python reimplementation.

---

## 1. Units

Static unit table is defined exactly once in `data.c:29-72` as `piece_attr[]`. The struct is in `empire.h:182-195`. Order matches the type IDs in `empire.h:63-73` (ARMY=0, FIGHTER=1, PATROL=2, DESTROYER=3, SUBMARINE=4, TRANSPORT=5, CARRIER=6, BATTLESHIP=7, SATELLITE=8). Fields: `sname` (map letter), `name`/`nickname`/`article`/`plural` (display), `terrain` (legal terrain chars), `build_time`, `strength` (damage per hit), `max_hits`, `speed`, `capacity`, `range`.

| Type | Symbol (You / Enemy) | Speed | Strength | Max Hits | Capacity | Range | Build | Terrain (terrain[]) |
|---|---|---|---|---|---|---|---|---|
| Army | A / a | 1 | 1 | 1 | 0 | INFINITY | 5 | `"+"` (land only) |
| Fighter | F / f | 8 | 1 | 1 | 0 | 32 | 10 | `".+"` (land + sea) |
| Patrol | P / p | 4 | 1 | 1 | 0 | INFINITY | 15 | `"."` (sea) |
| Destroyer | D / d | 2 | 1 | 3 | 0 | INFINITY | 20 | `"."` |
| Submarine | S / s | 2 | 3 | 2 | 0 | INFINITY | 20 | `"."` |
| Transport | T / t | 2 | 1 | 1 | 6 | INFINITY | 30 | `"."` |
| Carrier | C / c | 2 | 1 | 8 | 8 | INFINITY | 30 | `"."` |
| Battleship | B / b | 2 | 2 | 10 | 0 | INFINITY | 40 | `"."` |
| Satellite | Z / z | 10 | 0 | 1 | 0 | 500 | 50 | `".+"` |

Notes:

- **Damage tracking.** A piece's current health is `piece_info_t.hits` (`empire.h:137`). `max_hits` is the table value. Each "blow" in combat subtracts the attacker's `strength` from the defender's `hits` (`attack.c:111-115`).
- **Speed scales with damage.** `obj_moves()` in `object.c:73-79` returns `ceil(speed * hits / max_hits)`. A battleship at 5/10 hits gets 1 move/turn instead of 2; an army at 1 hit always gets 1.
- **Capacity scales with damage too** via `obj_capacity()` in `object.c:85-91` — a damaged transport can carry fewer armies; survivors over capacity drown after a fight (`attack.c:144-149`).
- **Fighter range.** `range=32` and `speed=8` means 4 turns of unsupported flight; this is decremented in `move_obj()` (`object.c:347`). The comment in `data.c:44-47` notes the range is deliberately an even multiple of speed. Crash and burn at `range == 0` (`compmove.c:435-439` for AI, `usermove.c:183-186` for user, `object.c:451-456` for satellites which also "crash and burn"). Refueling happens on landing: when AI fighter `init`s in city, `range = piece_attr[FIGHTER].range` (`compmove.c:422-425`); for user, when fighter lands on 'O' or 'C', `range` is reset (`usermove.c:174-182`).
- **Transport / Carrier capacity** is 6 armies / 8 fighters as written. Carrier capacity scales linearly with hits (so an 8-hit carrier holds 8 fighters; at 4 hits holds 4).
- **Satellite quirk.** Speed 10 but it moves diagonally and bounces off edges (`object.c:407-436`). It dies after 500 cells of travel (range counted down each move). Launched in a random diagonal direction by `produce()` at `object.c:320-322`. `scan_sat()` (`object.c:570-586`) sees out to distance 2 instead of 1 — "one square farther than other objects" per manpage.
- **First-unit cost penalty.** When a city's production is *set* to type X (new city, or production change), `cityp->work` is initialized to `-(build_time/5)` (`object.c:648`, also `compmove.c:276`, `compmove.c:629`). This means the first unit takes `build_time + build_time/5 = 6/5 build_time` turns. The manpage's parenthesized numbers (Army 5/6, Fighter 10/12, Battleship 40/48 etc.) reflect this. Subsequent units take only `build_time` turns.

Production time accumulation: `city.work` increments by 1 each turn the city is owned (`usermove.c:53`, `compmove.c:99`). When `work >= build_time`, `produce()` is called and `work -= build_time` (`object.c:299`). So work can carry over into the next unit.

---

## 2. Terrain

There are only three real-map tile contents (`empire.h:211`, `map[i].contents` is one of):

| Char | Meaning |
|---|---|
| `'+'` | Land |
| `'.'` | Water |
| `'*'` | City (unowned) |

Cities also have `cityp` pointer in `map[i].cityp`. There is no distinct "mountain", "forest", or "shore" terrain — shore is computed dynamically by `rmap_shore()` (`map.c:1164-1174`) and `vmap_shore()` (`map.c:1176-1188`).

The user/computer `view_map_t.contents` field uses an expanded character set (`empire.h:218`):
- `' '` unseen
- `'+'`, `'.'` land/water seen
- `'*'` neutral city, `'O'` user city, `'X'` computer city (mapping in `object.c:594`: `char city_char[] = {'*', 'O', 'X'};`)
- Unit letters: uppercase = user, lowercase = computer (`object.c:613-615`)

Movement legality is enforced by `good_loc()` in `object.c:468-499`:
- If `vmap[loc].contents` is in the piece's `terrain[]` string, it's a legal move.
- Armies may step onto a non-full friendly transport (`object.c:483-486`).
- Ships and fighters may enter a friendly city (`object.c:488-490`).
- Fighters may land on a non-full friendly carrier (`object.c:493-496`).

Edges (perimeter cells where row 0, MAP_HEIGHT-1, col 0, MAP_WIDTH-1) are marked `on_board = FALSE` in `game.c:153-154`. Nothing may move onto them; the world is a `60 × 100` rectangle with a one-cell unwalkable frame.

---

## 3. Cities

Defined at `empire.h:78-84`. `NUM_CITY = 70` (`empire.h:58`). City state: `loc`, `owner` (UNOWNED/USER/COMP), `func[NUM_OBJECTS]` (per-piece-type default mission), `work` (accumulated turns), `prod` (currently building type, or NOPIECE).

**Capture mechanics** (`attack.c:44-89`): Armies attacking a city roll `irand(2)`. On a 0, the army dies and the city stands. On a 1, the city is captured: `kill_city()` runs (changes owner, kills armies inside, transfers all other hardware to the attacker — see below), the city's owner becomes the attacker, and the army dies. So a city always costs the attacker one army regardless of outcome.

**`kill_city()`** (`object.c:240-282`): All armies in the city die. All other hardware (transports, fighters, ships) switches owner (`p->owner = (p->owner == USER ? COMP : USER)`). Transports' cargo is killed (`object.c:255-260`). Satellites are unaffected. The city's `func[]` array and `prod` are cleared. **This means an army taking a city captures any non-army units sitting in it intact — including fighters, fully-loaded carriers, and battleships.**

**Production rules** (one type at a time per city):
- Setting production via `set_prod()` (`object.c:629-652`) sets `prod` and resets `work = -(build_time / 5)`. **This is the 20% switching penalty.** Switching mid-build wipes progress and incurs the negative head start equivalent to about 20% of the new piece's build time.
- AI's `comp_set_prod()` (`compmove.c:267-277`) applies the same penalty when changing production, but it short-circuits if already producing the requested type (`if (cityp->prod == type) return`).
- When `work >= build_time`, `produce()` is called (`object.c:290-323`) and a unit appears at the city; `work -= build_time` so excess turns roll over.
- Newly-captured city has `prod = NOPIECE`. For a USER-captured city, `set_prod()` is invoked immediately to ask the user what to build (`attack.c:80`). For a COMP-captured city, `comp_prod()` decides next turn during `do_cities()` (`compmove.c:89-95`).

**Initial city assignment** in `game.c:297-340`. `select_cities()` finds continents with at least 2 cities including ≥1 shore city, ranks them by value, lets the user pick a difficulty (a pair from the cross product of continents — `0` is easy and `ncont*ncont-1` is hard; `game.c:310-314`). User's initial city is set by `set_prod()` (user picks); computer's is initialized to ARMY with no setup penalty (`game.c:330-332`).

**City `func[type]`** is the default movement function assigned to each new piece of that type produced here, applied in `piece_move()` (`usermove.c:120-122`) and reapplied after a piece moves into the city (`usermove.c:580-593`). This is the mechanism behind the "V <piece> <func>" command.

---

## 4. Combat Resolution

Entry point: `attack()` in `attack.c:31-42`. Dispatches to `attack_city()` if target is a city, else `attack_obj()`.

### City attack (`attack.c:44-89`)
- Single `irand(2)` coin flip. 0 → attacker dies, no city change. 1 → `kill_city()` runs, owner flips, attacker dies anyway. **50% capture chance; attacker always loses its army.**

### Unit-vs-unit attack (`attack.c:96-131`)
- Special case: cannot attack a satellite (`attack.c:109`).
- Loop until one side has `hits <= 0`:
  - `irand(2)` decides who throws this blow.
  - `0` → defender lands a hit: `att_obj->hits -= piece_attr[def_obj->type].strength`.
  - `1` → attacker lands a hit: `def_obj->hits -= piece_attr[att_obj->type].strength`.
- Survivor occupies the loser's square via `survive()` (`attack.c:140-149`), which kills overflow cargo if the survivor is a transport/carrier that gained the square but doesn't have capacity for what it's carrying.

This is **a per-blow stochastic attrition loop**, fully deterministic given an RNG seed. No diminishing-returns modeling; a healthy battleship at 10 hits with strength 2 hitting an army (1 hit, strength 1) will win on the first attacker-roll. Note that subs do 3 damage per landed blow — a healthy sub vs. a healthy battleship needs ~3-4 attacker-rolls in a row to win; conversely a battleship landing two blows obliterates the sub.

**Pieces a unit may attack adjacent to** are kept as preference-ordered strings (`data.c:101-104`):
- `army_attack[] = "O*TACFBSDP"` (most attractive first: enemy city > unowned city > transport > army > carrier > fighter > battleship > sub > destroyer > patrol)
- `fighter_attack[] = "TCFBSDPA"`
- `ship_attack[] = "TCBSDP"` — note **ships will not attack adjacent armies or fighters from the sea**; only other ships and transports
- `tt_attack[] = "T"` — empty transports will only ram other transports

This is searched by `find_attack()` (`compmove.c:850-875`) for the AI; user attacks happen by movement input.

**Subs vs. surface.** No special concealment. Subs are detected and visible as 's' as soon as adjacent. The sub's only edge is its strength=3.

**Probability table.** Per `vms-empire.6:218-228`, e.g. AFPT vs. AFPT = 50%, AFPT vs. B = 0.0977%, S vs. B = 6.25%, B vs. S = 99.5%. These follow from the algorithm: probability of winning ≈ a function of `hits` and `strength` ratios. (The manpage explicitly notes the 1.2 release fixed an earlier buggy version of this table.)

---

## 5. Fog of War / Visibility

Each side has its own `view_map_t[MAP_SIZE]`: `user_map` and `comp_map` (`extern.h:22-23`). Each cell stores `contents` (the player's last-known character) and `seen` (turn number last updated).

**Per-unit vision = 1 cell radius** (8 neighbors plus own cell). Computed in `scan()` (`object.c:545-564`): for each of 8 adjacent locations plus the unit's own loc, call `update()`. `update()` (`object.c:597-621`) writes the current real-map contents into the view map. Satellites use `scan_sat()` (`object.c:570-586`) which scans the 8 cells at distance 2 (and re-scans the unit's own location), giving them an effective radius of 2.

**Memory persists.** `view_map[loc].contents` is only overwritten by `update()`; it is never cleared. So a player remembers the last thing they saw at a cell forever (ghost units, stale terrain). This is the "ghost piece" phenomenon mentioned in `BUGS:258-266`.

**Vision triggers** are everywhere `scan()` is called:
- After any `move_obj()` (`object.c:377`)
- When killing a piece (`object.c:217`) or city (`object.c:280`)
- For every COMP piece each turn at start of `comp_move()` (`compmove.c:46-48`)
- For every USER piece each turn at start of `user_move()` (`usermove.c:38-42`)
- After capturing a city to let the loser see what happened (`attack.c:88`)

**No cheating in vision.** Both sides go through the same `scan()` machinery against the real `map[]`. The AI never reads `user_map` and the user only sees the enemy map after computer resignation (the `E`xamine command in `empire.c:92-95`, gated on `resigned`).

---

## 6. Map Generation

`make_map()` in `game.c:98-156`. Algorithm:

1. Fill every cell with a uniform random height in `[0, MAX_HEIGHT=999]` (`game.c:102-103`).
2. Smooth the height field SMOOTH times (`game.c:107-121`). Each smoothing pass averages each cell with its 8 neighbors (sum/9). Edge cells use themselves as the off-board neighbor. Default `SMOOTH = 5` (set by `-s` flag, `main.c:46`).
3. Count cells per height bin; find the lowest height such that the cumulative count ≥ `WATER_RATIO%` of MAP_SIZE AND ≥ NUM_CITY (`game.c:124-139`). This becomes the water line. Default WATER_RATIO = 70.
4. Cells at or below the waterline are `'.'` (water); above are `'+'` (land). The outermost ring (row 0, row MAP_HEIGHT-1, col 0, col MAP_WIDTH-1) is marked `on_board = FALSE` (`game.c:153-154`).

**Map size:** `MAP_WIDTH = 100`, `MAP_HEIGHT = 60`, `MAP_SIZE = 6000` (`empire.h:206-208`). Hard-coded; `BUGS:103` notes this should be a parameter.

**City placement** in `place_cities()` (`game.c:169-197`):
- `NUM_CITY = 70` cities total.
- Minimum spacing computed in `main.c:99-101`: `MIN_CITY_DIST = floor(sqrt(land_per_city))` where `land_per_city = MAP_SIZE*(100-WATER_RATIO)/100 / NUM_CITY`. At defaults that's `floor(sqrt(6000*0.3/70)) = floor(sqrt(25.7)) ≈ 5`.
- Greedy random sampling: pick a random land cell, place city, remove all land within `MIN_CITY_DIST` from the candidate pool (using `dist()` which is Chebyshev/king-distance from `math.c:60-72`).
- If no candidates remain, `regen_land()` (`game.c:205-226`) decrements `MIN_CITY_DIST` by 1 and rebuilds the list. This continues until all 70 cities are placed.
- Cities are placed on land only — they overwrite `'+'` to `'*'` (`game.c:190`).
- Continent validity check: `select_cities()` (`game.c:297`) iterates continents (computed by `find_cont()` → `good_cont()` → recursive `mark_cont()`), requires ≥1 shore city and ≥2 cities total. If no such continent exists, the entire placement is redone (`game.c:71-77`).

Note `make_map()` uses `irand(MAX_HEIGHT)` for noise. There's a minor off-by-one in `game.c:127` (`i <= MAP_SIZE` instead of `< MAP_SIZE` in the height_count loop) — reads one past the end of the array.

---

## 7. Win Conditions

Checked once per AI move in `check_endgame()` (`compmove.c:1124-1183`). Three outcomes:

1. **Computer resigns** (`compmove.c:1151-1164`): if `ncomp_city < nuser_city/3 && ncomp_army < nuser_army/3`. Sets `resigned = TRUE`, `win = 2`. The user is prompted whether to keep playing (mop-up) or end. The `E`xamine and `W`atch movie commands become enabled.
2. **Computer destroyed** (`compmove.c:1165-1173`): `ncomp_city == 0 && ncomp_army == 0`. `win = 1`. User has won outright.
3. **User destroyed** (`compmove.c:1174-1182`): `nuser_city == 0 && nuser_army == 0`. `win = 1` (same value — confusing). User loses.

Cities and armies only — fleet doesn't matter for victory. A user with battleships but zero cities and zero armies has lost.

`date += 1` increments at the start of `check_endgame()` (`compmove.c:1132`), so `date` counts AI moves (== turns) elapsed. Each round is "one user move + one comp move".

There is no time limit and no explicit "destroy all enemy pieces" check — surviving subs/transports after losing all cities and armies don't matter (per the game-over messages, "remnants of the enemy fleet may need rooting out", but the program already considers the game won/lost).

---

## 8. AI (`compmove.c`)

### High-level structure

`comp_move(nmoves)` (`compmove.c:35-65`):
1. Refresh `comp_map` via `scan()` for every owned piece.
2. For each granted move:
   - Build `emap` = `comp_map` with `vmap_prune_explore_locs()` applied — this *predicts* what unexplored cells are likely land or water based on neighbor counts (`map.c:746-863`). The AI then uses this filled-in map for objective-finding.
   - `do_cities()` — production decisions.
   - `do_pieces()` — movement.
   - Optionally append a movie frame.
   - `check_endgame()`.

There is **no separate strategic layer** — no "plan a campaign". All strategy emerges from per-unit objective-finding and per-city production heuristics. The "interest" computation in `comp_prod` (`compmove.c:170-172`) is the only per-continent reasoning.

### Per-unit decision-making

Move order is global, NOT per-city: `move_order[] = {SATELLITE, TRANSPORT, CARRIER, BATTLESHIP, PATROL, SUBMARINE, DESTROYER, ARMY, FIGHTER}` (`data.c:93-94`). All transports move before all carriers, etc. **Pieces of one type are moved in linked-list (allocation) order, NOT in any geographic order.**

Each piece type has its own routine:
- `army_move()` (`compmove.c:498-576`): 7-step priority — (1) immediate attack via `find_attack`, (2) `vmap_find_lobj` for nearest land objective (using `army_fight` weights from `data.c:155`), (3) compute land-objective cost vs. boarding cost, (4) `vmap_find_lwobj` for nearest loadable transport (`army_load`), (5) compare costs, (6) move toward the cheaper one. If on a transport, uses `vmap_find_wlobj` with the `tt_unload` weight table (`data.c:145-147`) to pick a continent to invade.
- `transport_move()` (`compmove.c:889-928`): empty transports attack adjacent tt's, then load (`vmap_find_wlobj` with `tt_load`); if no armies to load, explore via `tt_explore`. Full transports unload using `tt_unload`. Switches between `func=0` (loading) and `func=1` (unloading) based on cargo count vs. capacity.
- `fighter_move()` (`compmove.c:942-966`): immediate attack, else return-to-base if `range <= dist_to_nearest_city + 2`, else find objective via `fighter_fight` weights.
- `ship_move()` (`compmove.c:975-1007`): damaged ships head to port via `vmap_find_wobj` with `ship_repair`; healthy ships attack adjacent enemies, else seek objective with `ship_fight`. Damaged ship *in* port skips the turn so it can repair (and `cpiece_move()` `compmove.c:443-448` adds +1 hit if it never moved).
- Satellite: drift in fixed diagonal direction, bounce off edges (`object.c:443-458`).

### Pathfinding

Custom **breadth-first perimeter expansion** in `map.c:expand_perimeter()` and friends, NOT A*. Two perimeter lists (`from`, `to`) alternate; cells are added in cost-order. Implementation details:
- `path_map_t.cost` stores g-cost (no heuristic, hence not A*).
- Different unit classes use different entry points: `vmap_find_aobj` (air, T_AIR), `vmap_find_lobj` (land), `vmap_find_wobj` (water), `vmap_find_lwobj` (land-to-water for an army boarding a TT — costs 2 to enter land, 1 to enter water), `vmap_find_wlobj` (water-to-land for a TT unloading).
- Objectives are matched by character: `move_info_t.objectives` is a string of "interesting" chars, and `move_info_t.weights[]` gives the cost added when reaching that char. Lower weight = more attractive. See e.g. `tt_unload` (`data.c:145-147`) which weights 4-city continents (`'4'`) at 1 (very attractive) but already-owned continents (`'0'`) at 101 (far less attractive).
- Termination: when the perimeter is empty OR when `best_cost <= cur_cost` (we can't improve).
- After finding the destination, `vmap_mark_path()` recursively traces shortest paths back, then `vmap_find_dir()` picks the next single move, preferring diagonals (`map.c:1065-1066`) and squares with more interesting neighbors / more path-adjacent cells (`map.c:1097-1103`).

The `vmap_prune_explore_locs()` algorithm (`map.c:746-863`) is what makes this work: it fills in unexplored cells with predicted terrain based on neighbor counts, so the AI doesn't waste cycles routing around blank squares it actually could walk through.

### Production decisions (`comp_prod`, `compmove.c:136-260`)

1. **Defend own continent.** Walk `cont_map` for this city's continent. `need_count = user_cities - comp_army_producers + interest (+ 1 if any enemy cities present)`. If positive → produce ARMY.
2. **Special priorities.** If any enemy city is reachable and this city has no production → ARMY.
3. **Ratios.** Based on `total_cities`, select one of four hand-tuned ratio arrays (`compmove.c:117-121`):
   - ≤10 cities: `ratio1[] = {60, 0, 10, 0, 0, 20, 0, 0, 0}` (60% armies, 10% patrol, 20% transport)
   - ≤20: `ratio2[] = {90, 10, 10, 10, 10, 40, 0, 0, 0}`
   - ≤30: `ratio3[] = {120, 20, 20, 10, 10, 60, 10, 10, 0}`
   - >30: `ratio4[] = {150, 30, 30, 20, 20, 70, 10, 10, 0}`
4. **Transport guarantee.** First non-lake city becomes the TT producer. Carriers and satellites are weighted 0 — **the AI will never voluntarily build carriers or satellites** (per the comment at `compmove.c:79` and the `#if 0` block at `compmove.c:229-237`).
5. **Lake check.** `lake()` (`compmove.c:360-371`) classifies a city as on a lake if its water continent has no attackable city and no unexplored territory. Lake cities only build armies/fighters.
6. **Production change penalty.** Any change of `prod` invokes `comp_set_prod()` which sets `work = -(build_time/5)`, same 20% penalty as the user.
7. **Overproduction check.** `overproduced()` (`compmove.c:283-298`) sees if switching this city's production would improve the global ratio balance.

### Does the AI cheat?

After grepping `compmove.c` for COMP/USER asymmetries:

- **Vision: no.** AI calls the same `scan()` against the real map. No `user_map` reads anywhere in `compmove.c`.
- **Production: no.** Same `produce()` routine, same `work++`, same `build_time` constants, same first-piece penalty.
- **Combat: no.** Same `attack()` and `irand(2)` for both sides.
- **Move ordering advantage.** The COMP always moves *after* the user in a round (`empire.c:46-48`, etc.). So it sees the user's final positions before deciding. Minor edge.
- **Armies in cities.** `BUGS:15-18` explicitly says: "The computer is allowed to leave armies in cities. Fixing this feature might be difficult. This feature also gives the computer a small advantage which slightly helps overcome the natural superiority of humans." When a user army enters a friendly city, `user_skip()` calls `move_army_to_city()` (`usermove.c:604-606`) which moves it onto the city tile (uses up the move). The AI's `army_move()` has no such constraint — armies in COMP cities can defend by stacking, while user armies are kicked out.
- **Free repair in port.** Both sides get +1 hit/turn when a damaged boat sits in port (`compmove.c:443-448` AI, `usermove.c:191-197` user). Symmetric.
- **Damaged-ship blocking bug.** `BUGS:30-37` describes a known exploit where the user blocks the AI's damaged ship; the AI doesn't try to detour.

**Net verdict: the AI does not cheat in any major way.** It has one minor structural advantage (armies-in-cities) and one minor tempo advantage (moves second).

### Notable AI weaknesses

- Won't build carriers or satellites ever (`compmove.c:118-121`, ratios are 0).
- Doesn't escort transports — they sail solo (mentioned in `BUGS:506-512`).
- Multiple transports can target the same destination (`BUGS:163`).
- No "track enemy ships" memory (`BUGS:215-230`).
- On low-water maps, the AI still wastes a city on a transport producer.
- The `move_order` array means transports move before armies; combined with one-at-a-time army loading via `load_army()`, this can leave armies orphaned on shore for an extra turn.
- Movement order is allocation order; no spatial coherence in moves.
- `find_attack()` always attacks the first valid target — empty transports rushing into stronger surface ships frequently.

---

## 9. Save Format

**Binary blob, native endianness, no header, no version field.** Filename: `empsave.dat`. Code in `game.c:498-529` (write) and `game.c:539-618` (read).

Layout (sequential `fwrite()`):
1. `map[MAP_SIZE]` (the real map — but pointers inside it are not valid on reload and get rebuilt)
2. `comp_map[MAP_SIZE]`
3. `user_map[MAP_SIZE]`
4. `city[NUM_CITY]`
5. `object[LIST_SIZE]` (every piece slot, dead or alive)
6. `user_obj[NUM_OBJECTS]`, `comp_obj[NUM_OBJECTS]` (these are pointer arrays — saved but not read back as pointers, lists are reconstructed)
7. `free_list` pointer (saved/loaded but only to indicate dead slots)
8. Scalars: `date`, `automove`, `resigned`, `debug`, `win`, `save_movie`, `user_score`, `comp_score`

On reload, all `next`/`prev`/`ship`/`cargo` pointers are zeroed and rebuilt by `restore_game()` (`game.c:572-613`) based on each piece's `owner`, `type`, `loc`. Embark linkage is reconstructed by `read_embark()` (`game.c:626-649`) using each ship's saved `count`.

**Implications for reimplementation:**
- Save format is NOT portable across architectures (struct padding, pointer sizes, int sizes).
- LIST_SIZE = 5000 piece slots, so the file is large even with few units.
- No version number — any code change that changes struct layout invalidates old saves.
- `empmovie.dat` (separate, append-only) gets a `mapbuf[MAP_SIZE]` per turn for replay (`game.c:713-739`).

A modernized save format should be JSON / msgpack with explicit schema versioning.

---

## 10. Quirks and Surprises

Mining for things that would catch out a reimplementer working from memory:

1. **8-direction adjacency + Chebyshev distance**, not 4 or 6. `dir_offset[]` includes all 8 (`data.c:76-83`). `dist()` is `max(abs(dx), abs(dy))` — king's move (`math.c:60-72`). All movement, attacks, and visibility are 8-way.

2. **The outermost cells of the map are unusable.** Effective playable area is `(MAP_WIDTH-2) × (MAP_HEIGHT-2) = 98 × 58`. Pieces can never reach the very edge (`game.c:153-154`, enforced in `good_loc()` `object.c:475`).

3. **First-piece production penalty of build_time/5** when starting fresh OR changing production. Easy to miss. See `object.c:648`, `compmove.c:276`, `compmove.c:629`. Switching production mid-build is *not* free — you lose progress AND get the penalty.

4. **City capture transfers all non-army hardware to the attacker.** `kill_city()` at `object.c:262-265` flips owners. This is huge — capturing an undefended port can hand you a fleet. Cargo of transports captured this way dies (`object.c:255-260`).

5. **Cities are also water for repair purposes.** When AI ships use `vmap_find_wobj` for repair (`ship_repair = {COMP, "X", {1}}` per `data.c:167-169`), they need water-traversable terrain that includes their own city tile. `terrain_type()` (`map.c:707-711`) treats friendly cities as T_WATER, enemy cities as T_UNKNOWN.

6. **Cities cap at 70 globally** — across the whole map. With NUM_CITY = 70 cities and large continents, density is low.

7. **Difficulty selection is highly nonlinear.** "Difficulty level" is an index into ranked continent pairs (`game.c:308-316`). Higher numbers don't make the AI smarter; they give the AI a bigger continent. Per the manpage hint (`vms-empire.6:620-629`), this can actually *help* the AI by forcing earlier transport use, so lower numerical difficulty can produce stronger AI play.

8. **Damage halves speed and capacity for damaged transports** — a transport at 0 hits (impossible — it dies first) would have capacity 0. At 1 hit (which is also max), no effect for tt. But a carrier at 4/8 hits carries only 4 fighters and moves at 1/turn. Fighters not in the carrier when it gets hit may fall overboard during a successful attack (`attack.c:171-181`).

9. **Fighter range doesn't decrement on stationary turns.** `BUGS:42-46`. If you SENTRY a fighter mid-flight, its range stays the same forever. The AI doesn't exploit this, but it's a real behavior in the canonical rules.

10. **Satellite range is 500.** It moves 10/turn, so it lasts 50 turns before crashing (`object.c:451-456`). Direction is set at birth and bounces off edges; you have no influence after launch.

11. **`find_obj_at_loc()` preference order.** When multiple pieces occupy a tile, `find_obj_at_loc()` (`object.c:154-168`) returns the piece with the highest `type` number, except satellite. So a transport (type 5) wins over an army (type 0). This determines what an attacker actually hits when attacking a stack.

12. **Armies riding a transport can attack only certain things.** `army_attack[]` is the same list, but `army_move()` (`compmove.c:519-521`) passes terrain `"+*"` (city or land) for armies-on-ships and `".+*"` for armies-on-land. Armies on a ship can't attack adjacent enemy ships during the AI turn.

13. **A satellite cannot be attacked** (`attack.c:109`). They're invulnerable reconnaissance.

14. **Empty TTs are willing to attack adjacent enemy TTs only.** `tt_attack[] = "T"` (`data.c:101`). They will not engage ships or armies on adjacent water (`compmove.c:898-905`).

15. **Ships repair only 1 hit/turn but only when stationary in a friendly city.** `compmove.c:443-448` and `usermove.c:191-197`. Critical for endurance battles.

16. **The map height variable says `60` but column 0 and column MAP_WIDTH-1 are unreachable.** `MAP_WIDTH=100`, `MAP_HEIGHT=60` (`empire.h:206-207`).

17. **Random number generation.** `irand(n)` is `rand() % n` (`math.c:33-40`). Seeded once with `srand(time(0) & 0xFFFF)` (`math.c:30`). 16-bit seed window. For deterministic tests in a reimpl, replace with explicit seeded RNG.

18. **`dist()` is king's distance, but `MIN_CITY_DIST` decrements when stuck.** `regen_land()` shrinks the minimum after a placement failure (`game.c:218-220`), so on awkward maps cities can end up closer than the initial computed minimum.

19. **Two unused movement modes:** `ARMYLOAD` and `TTLOAD` are `ABORT;`'d (`usermove.c:303`, `usermove.c:353`). They're dead code in this version.

20. **`win == 1` means both "user won" and "user lost".** Distinguished only by message text in `compmove.c:1165-1182`. `win == 2` means computer resigned. Easy bug magnet.

21. **The "score" tracked is just the cumulative `build_time` of pieces destroyed.** `attack.c:165, 184`. Not the value of cities held or any other metric.

22. **`emap` (pruned-explore map) and `comp_map` differ.** The AI does most of its objective searches against `emap` (predicted terrain), but `comp_map` is what gets displayed and saved. Whenever you see `make_unload_map`, `make_army_load_map`, etc. (`compmove.c:598-744`), they copy and decorate `comp_map`; they call `unmark_explore_locs` to revert predicted-but-not-actually-seen cells back to ' ' for certain searches (`compmove.c:582-591`).

23. **Saving the game writes ~half a megabyte** of fixed arrays every time (per the README's notes). `save_interval` defaults to 10 turns (`main.c:49`).

24. **A fighter landing on a city/carrier in mid-move** has its remaining moves consumed for the turn (`compmove.c:433-434`, `usermove.c:174-182`). It cannot take off again the same turn. The range is reset on landing.

---

## Implementation pointers (file:function reference table)

| Concern | File | Symbol |
|---|---|---|
| Unit table | `data.c:29-72` | `piece_attr[]` |
| 8-direction offsets | `data.c:76-83` | `dir_offset[]` |
| AI attack preference strings | `data.c:101-104` | `*_attack[]` |
| AI objective weight tables | `data.c:108-185` | `tt_unload`, `army_fight`, ... |
| Map gen | `game.c:98-156` | `make_map` |
| City placement | `game.c:169-197` | `place_cities` |
| Continent ranking | `game.c:347-489` | `find_cont`, `good_cont`, `make_pair` |
| Save/restore | `game.c:498-618` | `save_game`, `restore_game` |
| Attack city | `attack.c:44-89` | `attack_city` |
| Attack unit | `attack.c:96-131` | `attack_obj` |
| Move legality | `object.c:468-499` | `good_loc` |
| Damage-scaled movement/capacity | `object.c:73-91` | `obj_moves`, `obj_capacity` |
| City capture transfers fleet | `object.c:240-282` | `kill_city` |
| Vision scan | `object.c:545-621` | `scan`, `scan_sat`, `update` |
| Production setup penalty | `object.c:629-652` | `set_prod` |
| AI top-level | `compmove.c:35-65` | `comp_move` |
| AI city production | `compmove.c:82-260` | `do_cities`, `comp_prod` |
| AI production ratios | `compmove.c:117-121` | `ratio1..4[]` |
| AI army logic | `compmove.c:498-576` | `army_move` |
| AI transport logic | `compmove.c:889-928` | `transport_move` |
| AI fighter logic | `compmove.c:942-966` | `fighter_move` |
| AI ship logic | `compmove.c:975-1007` | `ship_move` |
| BFS pathfinding | `map.c:578-626` | `expand_perimeter` |
| Terrain prediction | `map.c:746-863` | `vmap_prune_explore_locs` |
| Path tracing | `map.c:971-1108` | `vmap_mark_path`, `vmap_find_dir` |
| Endgame checks | `compmove.c:1124-1183` | `check_endgame` |
| RNG | `math.c:28-40` | `rndini`, `irand` |
| Chebyshev distance | `math.c:60-72` | `dist` |

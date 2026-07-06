# 08 — LLM Strategic General (design + live-model findings)

Status: **brainstorm + live 4B experiments, no integration code yet.** The
deterministic game/AI is untouched by this work. Resume from "Where we stopped."

## The architecture (settled)

- **Two layers.** The LLM is the *strategic general* (sets doctrine); `PortfolioAI`
  is the *executor/staff* (plans + moves every turn). The LLM never touches the
  board or resolves anything — it reads a briefing and issues intent.
- **Output = task-force assignments**, not abstract "posture → dials." The general
  groups units into task forces and gives each an objective; the executor drives
  each TF toward its objective. Concrete, actionable, gradeable — and it kills the
  abstract-posture-to-dials compiler entirely.
- **Cadence:** strategic tempo (every N turns / on triggers), not per-turn.
- **Safety:** the executor's playout is a *competence floor* — a dumb doctrine can't
  lose the game outright. And the executor-only path stays the deterministic,
  testable baseline; the LLM is a toggle layer on top.

## The "cooking fork" (settled): recipe, not open kitchen

- **Pre-baked deterministic workflow, NOT agentic MCP tool-discovery.** Agentic
  tool-use IS planning/orchestration — the exact thing small local models fail at,
  and it re-imports the failure mode we offloaded. Also N calls/epoch = CPU latency wall.
- **Separate the tool surface from the orchestrator.** Tools = read-only perception
  API + a "simulate this doctrine" capability (the executor's playout as the general's
  sandbox). Orchestrator = deterministic workflow (local default) OR agentic driver
  (hosted opt-in). Same seam either way.

## Prompt anatomy (learned empirically): **RULES + ROLE + STATE + TASK**

`ROLE` is a load-bearing pillar — without it the model defaults to "engine implementer"
and asks for combat formulas / production currency. It is the general, not a front-line
officer; restate the role in the TASK too (models anchor on the last instruction).

## The pre-chew line (located empirically)

Three levels, on a `sea=CONTESTED` example:
- **too little:** `sea=CONTESTED (warships 1 vs patrols)` — model won't connect it → attacks.
- **right:** `sea=CONTESTED → enemy shipping present, transports at risk` — flag the
  *consequence*; the model supplies the *response*.
- **too much:** `sea=CONTESTED → build navy first` — that's the decision → veneer.
Rule: **annotate each fact with its immediate risk/consequence; do the arithmetic,
don't draw the conclusion.** BUT — with raw board + rules the model derived the naval
caution on its own (see findings), so lean toward raw + rules and let it reason;
pre-chew only where it visibly can't.

## The SHOULD gate (user, 2026-07-05, via Ian Malcolm)

Capability is not the ship criterion. The executor already plays a competent game at
zero seconds per turn, so the command general ships ONLY if playtesting says the game
is BETTER with it on — a more interesting, characterful, narratable opponent at equal
difficulty, worth its minutes. That is a fun question, not a benchmark question, and
it can fail while every battery passes. Acceptable outcome: the general remains the
narrator tier + a lab program that hardened the executor, and the command tier never
ships. Standing safeguards: the deterministic game never depends on the model;
difficulty never scales with hardware; opt-in behind a measured probe.

## Build order (settled): the LLM is integrated LAST

1. `Doctrine`/task-force + `Briefing` schema (shared TYPES). 2. Compiler/executor seam,
proven with a `FakeGeneral` stub (no LLM). 3. SITREP renderer + memory tables. 4. The
eval harness (staged board + constructed history → grade output fields → pass-rate
scoreboard), validated against the stub. 5. Plug in the real model — **narration-first**
(read-only commentator can't break gameplay), then graduate to the command general.
The harness shares TYPES with the game, mocks the PRODUCERS; two integration handshakes:
(a) prove the compiler steers, (b) prove real briefings ≡ mocked ones.

## Model + constraints

- Candidate CPU-floor model: **Qwen3.5-4B (unsloth GGUF).** Thinking mode = a reserve
  dial (off by default; on for hard/counterintuitive calls), NOT a hardware rung.
- CPU is the design ANCHOR; GPU is optional acceleration (GGUF backend autodetects).
  Let hardware scale QUALITY (narration, coherence), NOT difficulty (anchored by the
  executor). The general is an opt-in ADD-ON (multi-GB model, heavy deps), never in the
  14MB core binary.
- Fine-tuning: **design FOR it, don't commit** — log traces so every prompted game builds
  a corpus. Framing: the general as a learned amortization of doctrine-search (executor
  = teacher; AlphaZero spine on a prose layer). Use a LoRA adapter to keep base fluency
  for narration.
- **Measured performance envelope** (user, 2026-07-05, Qwen3.5-4B Q8): multi-GPU rig
  (unoptimized, layer-split) 68 t/s gen / ~5000 t/s prefill; DGX Spark GB10 38 t/s /
  ~3000 t/s at a 100W ceiling — the GB10 is the likely shape of the future audience
  machine, and at strategic cadence its ~4.5 min/epoch is livable. **CPU floor
  (i9 Ultra 285K, DDR5-6000 dual-channel, naive/no tuning = the player-realistic
  number): 6.8 t/s gen, 55 t/s prefill.** A 10k-token thinking epoch ≈ 25 min (+64s
  cold prefill; ~2x better at Q4, still a crawl). User gut check, adopted as the
  TIER SPLIT: "not unusable for chat functions; as an AI general it would crawl."
- **TIER SPLIT (working conclusion, 2026-07-05):** the CPU floor gets the NARRATOR
  (short non-thinking outputs, 15-45s, the narration-first integration step); the
  COMMAND GENERAL is an accelerated-tier feature, gated at setup by a MEASURED
  throughput probe (time one short generation on the player's box), not by
  GPU-detection assumptions. This is "hardware scales quality, not difficulty" with
  numbers attached; the executor stays everyone's competence floor. Corollary: the
  cache-native layout is mandatory even for the narrator tier — at 55 t/s prefill,
  short outputs still pay the full prompt toll without it.

## Deployment architecture (settled): ONE SEAM, TWO MODES

The game talks to the model through exactly one seam — **OpenAI-compatible chat
completions against a `base_url`** — and self-contained vs external-server differ only
in who starts the server:

- **Managed mode (self-contained; the Ollama pattern).** `--install-general` fetches two
  pinned, sha256-verified artifacts into the app data dir: a llama-server release binary
  for the platform (llama.cpp publishes static builds for our whole CI matrix, ~20MB)
  and the GGUF weights (~3GB). At play time the add-on spawns
  `llama-server -m weights.gguf --port <ephemeral>`, waits on `/health`, talks to
  localhost, reaps it on exit. The runtime is a *fetched artifact* like the weights —
  pinned, not owned: no pip dependency, no ABI coupling, nothing compiled. Inference
  stays out-of-process (a runtime crash/OOM can't take the TUI down; no GIL contention
  with the Textual event loop).
- **BYO mode (external server / hosted opt-in).** User sets the endpoint (their own
  llama.cpp/vLLM/LM Studio, or a hosted API key); we spawn nothing. Same client code,
  one config field.

**Rejected for this role: in-process PyTorch/transformers.** CPU generation is
memory-bandwidth-bound and torch has no production 4-bit CPU path, so the same 4B runs
~BF16: ~8.4GB of weights vs 2.9GB GGUF, ~3 tok/s vs ~15-25 — a thinking block goes from
minutes to a quarter-hour — in ~3x the RAM, inside our process. Torch remains the
*training-side* tool if/when LoRA fine-tuning happens (offline, not shipped).

**BYO trust boundary (accepted: caveat emptor).** In BYO mode we cannot control or
verify what model actually answers — accepted, because the design already absorbs it:
- The compile step (LLM text → task-force assignments) is TOTAL regardless of mode:
  validate against the real roster/cities, reject nonconforming output, fall back to
  executor-only doctrine. A swapped model can degrade quality, never correctness —
  the executor floor catches bad strategy, the validator catches bad format. BYO adds
  no new failure class, only frequency.
- Log the response's reported `model` id into every transcript (catches the common
  honest mistake — wrong server/model loaded — and keeps the fine-tuning corpus
  attributable) and surface it in-game. Unverifiable against a lying endpoint; fine.
- Posture: managed mode = the tested configuration; BYO = modding territory. Warn once
  on an unrecognized model id; don't police. Later, the eval-harness scenario battery
  doubles as a model-qualification check for serious BYO users.
- **Supported model = exactly ONE, pinned by name+quant** (user, 2026-07-05, after the
  temp/min_p sensitivity findings: model tuning demonstrably matters). We state the
  supported model precisely; a player who swaps it owns the consequences. Sampling
  params ship with the pin (temp 1.0 / top_p 0.95 / top_k 20 / min_p 0 / presence 1.5,
  thinking on).
- Never depend on reasoning format: thinking arrives as `reasoning_content`, inline
  `<think>` tags, or not at all depending on backend — parse only the final structured
  section.

Client discipline: strictly OpenAI-compatible — no llama.cpp-specific endpoints — with
one passthrough: `chat_template_kwargs` via `extra_body` (the thinking-mode dial).
Known costs to plan for: resume-and-verify download plumbing for the ~3GB fetch, and
unsigned-executable friction on macOS/Windows (same code-signing question already
deferred for our own binary). The lab runner for prompt experiments builds on this same
seam (`openai` package in a dev-only dependency group, never in the game's deps).

## Methodology win

**Ask the model what it needs.** A fresh 4B with no shared context found real gaps in the
primer (sea movement, production, etc.) *because* it couldn't fill them from conversation
the way we unconsciously did. The primer converged via its own gap-questions; convergence
signal = it stops asking "what game is this" and starts asking "what's the exact boundary
of this one rule."

---

## ARTIFACT: the corrected RULES PRIMER (current best)

```
=== FARSHORE — RULES PRIMER ===

YOUR ROLE
  You command at the strategic level. You form task forces and give each an
  objective ("Task Force 1: secure the eastern sea"). You do NOT move
  individual units, choose individual targets, or resolve combat — subordinate
  officers carry out your intent and report back. If you find yourself naming a
  unit's destination tile, you've dropped too low.

VICTORY
  You win when the enemy owns zero cities (a player with no cities cannot build
  and is finished). Neutral cities do not decide the game. Only cities build.

UNITS
  name        moves-on  HP  spd  str  carries      builds-in
  ARMY        land       1    1    1   —             5 turns
  FIGHTER     air        1    8    1   —            10   (range 20; must refuel)
  PATROL      sea        2    4    1   —            15
  DESTROYER   sea        3    3    2   —            20
  SUBMARINE   sea        2    2    3   —            25
  TRANSPORT   sea        3    2    0   6 armies     30   (str 0: cannot fight)
  CARRIER     sea        8    2    1   8 fighters   40
  BATTLESHIP  sea       18    2    4   —            50
  SATELLITE   orbit      1    1    0   —            50   (recon only; cannot attack or be attacked; 50-turn life)

MOVEMENT & TERRAIN
  - Land units move on land + cities; they cannot enter water.
  - Sea units move on water + cities; they cannot cross land, and must sail
    around it. Cities are the only tiles where land and sea units meet.
  - A unit moves up to its speed at full HP; damaged units move less. A unit
    regains HP only on a turn it does not move.
  - One unit per tile (exception: cargo, below).

COMBAT  (the engine resolves it; you only weigh it)
  Probabilistic. Higher strength and higher current HP win more often; either
  side can lose HP or be destroyed. Strength-0 units (transport, satellite)
  never fight — a transport caught by an enemy warship is destroyed.

SEA TRANSPORT  (how armies cross water — and what is expected of you)
  An army can never enter water. Transports carry them: a transport loads up
  to 6 armies at a city or coastal tile, sails, and unloads onto adjacent
  land. YOUR OFFICERS DO ALL OF THIS. If a task force contains armies and a
  transport and you give it an objective across water, the officers march
  the armies to the transport, load, sail, escort, unload, and press the
  objective — you never order the rendezvous, the loading, or the landing.
  Expected use: put the armies, the transport, and (if the sea is not safe)
  a warship into ONE task force and name the objective. A transport has
  strength 0 — alone it is a target, not a warship; a warship in the same
  task force escorts it automatically. Never STAGE land units on water.
  Cargo is kind-locked — transports carry armies only, carriers carry
  fighters only; ships never carry ships. If a loaded transport or carrier
  is destroyed, everything aboard is lost with it.

CAPTURING CITIES
  Only an army captures a city, by entering it. Capture is a 50% roll, and a
  successful capture consumes the army (it garrisons the city). A city can take
  several armies to secure, and each attempt spends one.

PRODUCTION
  No currency. A city builds ONE unit at a time; it finishes after that unit's
  build-time in turns, then starts the next. Switching what a city builds
  DISCARDS all accumulated progress — effort toward one unit does not transfer
  to another.

AIR & SPECIAL
  - Fighter: must reach a FRIENDLY city or a carrier within its range (20) or
    it is lost. It cannot land on an enemy city.
  - Satellite: reveals everything within scan radius 10 — terrain AND any enemy
    units currently there — but far-reaching and deep in enemy territory. It
    only sees; no other effect, cannot be shot down.

VISION / FOG
  You see within any of your units'/cities' scan radius (army 2, sub 1,
  patrol/destroyer/battleship 3, transport 2, carrier 4, fighter 5, sat 10).
  You remember terrain you've seen, but an enemy unit is known only where you
  can see right now — remembered enemy positions may be stale.
```

Confirm vs RULES.md if reused: fighter refuel site; undefended-city-capture roll detail.
For a FORTIFIED game, add a city-artillery line (cities fire on adjacent enemies).
Primer revision log: VICTORY neutral wording (grounding A/B, confirmed 3/3); SEA
TRANSPORT mechanics-and-expectations section replacing CROSSING WATER (user ask after
lift2's rendezvous micromanagement — officers-handle-lift made a first-class
convention; lift3 tests it). The transport role line ("a hauler, not a warship") is
now IN this section, closing that queued item.

## ARTIFACT: the test BOARD + TASK (raw data, turn 38)

```
MAP  legend: . land  ~ water  ? fog   O my city  E enemy city  N neutral city
 r0  . . O . . . ~ ~ ~ ~ ? ? ? ?
 r1  . . . . N . ~ ~ ~ ~ ? E ? ?
 r2  . O . . . . ~ ~ ~ ~ . E . ?
 r3  . . . . O . ~ ~ ~ ~ . . . ?
 r4  . . . . . . ~ ~ ~ ~ ? ? ? ?
 r5  ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ? ? ? ?

MY UNITS  (col,row)
  army #1 (0,0) #2 (1,0) #3 (3,0) #4 (0,1) #5 (1,1) #6 (3,1) #7 (0,2) #8 (2,2)
  transport #9 (6,2) empty
  destroyer #10 (6,3)
MY CITIES  (2,0) build ARMY 2 left · (1,2) build TRANSPORT 12 left · (4,3) build ARMY 4 left
NEUTRAL CITIES  (4,1) on my continent
KNOWN ENEMY  city (11,1), city (11,2); destroyer near (10,2) seen 3t ago; army (11,1) seen 3t ago

TASK: group your units into TASK FORCES, one objective each. Output per TF: member
units + one-line objective. No individual moves.
```

## Latest result (Qwen3.5-4B, **thinking ON**) — thesis VALIDATED + 3 bugs

It produced 4 task forces at the right altitude. GOOD (the win): derived the naval
caution from RAW data with NO labels — refused to dump armies across water, wants recon
first, ESCORTED the transport with the destroyer, STAGED the invasion for "sufficient
strength assembled." That is the whole thesis, reasoned from positions+counts+rules.
ERRORS (ranked):
1. **Hallucinated a satellite** (none on the board). Thinking was ON and it still invented
   a unit → deliberation improves coherence but does NOT prevent grounding errors;
   grounding must be enforced by the data/prompt.
2. **Phantom choke-point:** tasked land armies to "hold a choke point to stop the enemy
   destroyer crossing water" — land can't interdict sea, sea can't cross land, and that
   destroyer is across the map. Has the rule; spatial application failed.
3. **Missed the free neutral at (4,1)** — the obvious land-reachable free city, untasked.

## Where we stopped / NEXT STEP

- **Next experiment:** add a hard completeness frame to the board — e.g.
  `AVAILABLE UNITS (your entire force — you have NOTHING else):` — and re-run. Prediction:
  kills the phantom satellite. If so, the reusable rule is "grounding = an explicit
  completeness assertion," not a model problem.
- Missed-neutral, diagnosed: model self-reported "too focused on threat + fog," BUT the
  primer's VICTORY line *"Neutral cities do not decide the game"* likely nudged it to
  write neutrals off (accidental bias in our text). Two fixes, keep them distinct:
  (a) PRIMER WORDING (factual): add "neutral cities can be captured by an army and then
  build for you; they don't win the game but they add production" — removes the
  "don't matter" implication without telling it to grab them. (b) STEER THE PROCESS, NOT
  THE STRATEGY: add a completeness discipline (same family as the AVAILABLE-UNITS roster
  fix) — "account for every unit and every city (yours/enemy/neutral) and state what each
  is doing or why it's left alone." Forces *consideration*, not the decision. AVOID
  "prioritize neutrals" — that's injecting our answer (the veneer end).
- Then: the spatial choke-point confusion (has the rule, mis-applies it spatially).
- Bigger picture, when ready to build: start the schema (task-force + briefing TYPES) and
  the `FakeGeneral`-backed eval harness per the build order above. The LLM integrates last.
  This is a long way off: the prompts are the hard part, not the wiring — stay in the
  experimentation loop until the prompt line is well understood.

## NEXT EXPERIMENT: the grounding A/B (run-ready)

One run proves nothing at nonzero temperature — establish a base rate, then flip one
lever at a time. Three conditions, **3 runs each** (thinking ON; suggested sampling per
model card: temp 1.0, top_p 0.95, top_k 20, presence_penalty 1.5):

- **BASE ×3** — the primer + board above, verbatim. Measures the base rate of the three
  error classes (phantom unit / spatial illegality / missed neutral) before any fix.
- **A ×3 (roster frame only)** — same primer; in the board, replace the line
  `MY UNITS  (col,row)` with:
  `AVAILABLE UNITS  (col,row) — this is your ENTIRE force; you have NOTHING else`
  Tests the grounding fix in isolation. Prediction: phantom satellite dies.
- **B ×3 (A + both neutral fixes)** — A's board line, plus:
  1. primer VICTORY paragraph becomes:
     "You win when the enemy owns zero cities (a player with no cities cannot build
     and is finished). Only cities build. A neutral city can be captured by an army
     like any other city; it then builds for you — taking neutrals does not win the
     game, but each one adds production."
  2. append to TASK:
     "Account for every unit and every city on the board — yours, enemy, and neutral:
     each must appear in a task force, or be named with one line saying why it is
     deliberately left alone."
  Prediction: the free neutral at (4,1) gets tasked (or at least consciously declined).

**Grade each run on 4 axes** (score = count across the 3 runs):
1. phantom units — any unit referenced that is not on the roster;
2. spatial legality — any land-interdicts-sea / sea-crosses-land style assignment;
3. neutral (4,1) — tasked / consciously declined / ignored;
4. strategic core intact — transport escorted, invasion staged not rushed (the thesis
   result; must NOT regress while we fix grounding).

If B's extra discipline bloats output or degrades axis 4, fall back to A + primer
wording only (drop the account-for-everything clause) and re-test.

## RESULTS: grounding A/B (2026-07-04, Qwen3.5-4B UD-Q8_K_XL, thinking ON, 9/9 converged)

Ran via `lab/run_battery.py` (transcripts: `lab/transcripts/grounding-ab/`).
Runs took 70–110s at ~75 tok/s; thinking 4.9k–8.2k tokens (B ~15% longer).

| axis | BASE (×3) | A (×3) | B (×3) |
|---|---|---|---|
| phantom units | 0 | 0 | 0 |
| spatial-legality errors | 1 wobble | 0 | 1 wobble |
| neutral (4,1) capture-tasked | 0 (ignored/hedged) | 0 (ignored/guarded) | **3/3** |
| strategic core intact | 3/3 | 3/3 | 3/3 |

1. **Phantom base rate is ZERO on this setup** — every run, BASE included, ran an
   explicit roster audit in its thinking ("Wait, I missed the Satellite? … no satellite
   to command"). The original hallucination was a tail event and/or serving-config
   difference (quant/params/seed), so the completeness-frame hypothesis is UNTESTABLE
   here — nothing to kill. Lesson kept: grounding errors at 4B are a low-rate tail →
   the validator layer is the defense, not prompt wording. (B-s2's thinking did anchor
   its audit on the frame — "the prompt says ENTIRE force… so only" — so the frame is
   used when present; keep it, cheap insurance.)
2. **The neutral fix WORKED, 3/3, mechanism visible.** B runs task the capture with the
   production rationale; B-s3's thinking quotes the new primer line verbatim before
   deciding. The bias really was our old "do not decide the game" wording. KEEP: new
   VICTORY wording + account-for-everything discipline.
3. **The choke-point error did not recur** (0/9 land-interdicts-sea). One capability
   wobble instead: BASE-s2 tasked the str-0 transport to "threaten" the enemy destroyer.
   Same family (has the rule, misapplies to a unit). → Next primer rev: add ROLE
   framing to the units table — factual category, not decision: e.g. "TRANSPORT — a
   hauler, not a warship; contributes nothing to sea combat or sea control." Also one
   direction slip (B-s2 "advance south" toward an eastern target).
4. **Unprompted competence, new:** 4/9 runs sized the amphibious lift to EXACTLY 6
   armies = transport capacity (all three A runs + B-s3), holding the remainder back.
   Capacity-aware force sizing from the raw units table.
5. **Schema insight from B:** told to account for cities, the model folded CITIES and
   production into its task forces as members ("TF-1: Army #1-8 + Production Hubs at
   (2,0),(4,3)"). It naturally thinks of production as part of a TF's order of battle →
   the Doctrine/TaskForce schema should carry an explicit cities/production slot per TF.
6. **Altitude discipline self-enforces:** B thinking shows it policing the role rule on
   itself ("Do NOT say 'Move to (4,1)'. Say 'Capture Neutral City'"). The ROLE pillar
   is doing work inside deliberation, not just in the output.

**Verdict:** B is the new baseline prompt (roster frame + neutral wording +
account-for-everything). Next battery: spatial/capability stress — a board + primer rev
targeting the remaining error family (unit-role misuse, direction errors), and
qualification runs at the Q4 CPU floor before any result is called shipping truth.

## SITREP: replace the grid with a theater briefing (experiment in flight)

User observation from the transcripts, confirmed: the model burns a large share of its
thinking re-deriving coordinates from the ASCII grid, with self-corrections ("col 1 is
N? no, col 4"). Char-grid → coordinate mapping is a known LLM weakness — and the
strategic altitude doesn't need tiles anyway. A general reads a labeled briefing map,
not raw terrain.

**Design:** the engine derives a fog-honest THEATER section deterministically (connected
components + distance fields — machinery the AI layer already has): named regions
(HOME CONTINENT, CENTRAL SEA, FARSHORE CONTINENT, …) with extent, coasts, adjacency,
crossing widths (arithmetic pre-done: "4 water tiles — a transport crosses in 2 turns"),
what each region holds, and explicit fog edges ("its eastern extent is unseen").
Pre-chew line respected: distances and membership are arithmetic; corridor/priority
judgments stay the model's. **The region names double as the addressable nouns for
orders** — perception and command share a vocabulary. That coupling is why this
experiment runs before the schema one.

Battery `sitrep` (lab/prompts/sitrep/): REGIONS arm only — the GRID arm is
grounding-ab's condition B verbatim (completion tokens 6648/6751/8223, mean ~7.2k).
Measure: thinking tokens, grounding accuracy, strategic core. Predict: big token drop,
no strategy loss, fewer coordinate slips.

**RESULTS (ran 2026-07-04, 3/3 converged): token prediction WRONG, everything else
RIGHT.** Tokens went UP: 9901/12024/6845 (mean ~9.6k, +33% vs GRID) — the surplus is
NOT map confusion (zero grid-reconstruction lines) but *deeper operational planning*:
the pre-computed logistics facts gave the model hooks for wave scheduling and
embarkation grouping it previously couldn't reason about precisely. Wins: neutral
capture-tasked 3/3, capacity-exact 6-army lift 3/3, zero unit grounding errors, and —
the coupling we wanted — all 3 runs addressed orders to region nouns unprompted
("Cross the Central Sea", "secure Farshore West Coast"). Two new defects, both ours:
(1) naming collision — "FARSHORE CONTINENT" vs the game's title sent s2 into
which-side-am-I deliberation (fix: enemy landmass renamed EASTERN CONTINENT);
(2) s3 leaked its self-revision into the answer ("Correction: … Revised Plan:") —
the output contract exists to forbid exactly this. ~~Verdict: REGIONS stays, for the orders-vocabulary coupling, not for tokens.~~
**OVERRULED — REGIONS REJECTED (user course-correction, same day).** Named regions are
not a game construct: the derivation/naming/turn-to-turn-identity layer is new engine
machinery whose bugs would masquerade as model behavior in every later experiment, and
its nouns flow outward as order targets the engine can't execute without a resolver.
The raw board was presented deliberately so our heuristics don't become another
variable — and the data agrees: 9/9 grid runs had zero coordinate-grounding errors
(the grid is slow to read, never wrong). **STANDING RULE: prompts and contracts use
engine-native constructs only** — raw board, city coordinates, unit ids, at most
universal primitives (compass directions). The token lever is the thinking dial
(OFF is the intended shipping default), measured by the doctrine battery's
THINK/NOTHINK arms (those results are representation-independent and stand).

## DOCTRINE SCHEMA: action verbs + parsable output (draft, test after sitrep)

Second user observation: prose objectives aren't actionable — the executor needs a
contract. Draft:

**Verb vocabulary (5, strategic altitude only):**
- `CAPTURE <target>` — take the city/cities (across water if the TF has lift; the
  executor compiles the amphib pipeline — the general never says "load/sail/unload")
- `DEFEND <target>` — hold cities/region against attack
- `SCOUT <target>` — reveal a region / refresh stale contacts
- `PATROL <target>` — sea control of a region (interdict, screen, blockade)
- `STAGE <target>` — mass and wait there; explicit not-yet-committed posture
Targets **(v2, per the engine-native rule)**: a city `(x,y)` for
CAPTURE/DEFEND/STAGE — cities are the game's strategic currency and map 1:1 onto how
the executor already plans; for SCOUT/PATROL, a coordinate anchor or a bare compass
direction (universal primitive, compiles onto existing explore/patrol behaviors with
no region machinery). ~~a REGION NAME from the theater briefing~~ (rejected — not
executable). Coverage check:
every sound TF objective in the 9 grounding-ab transcripts maps onto these five
(blockade→PATROL, home guard→DEFEND, amphib assault→CAPTURE, await-strength→STAGE).
ESCORT is not a verb — a warship in a TF with a transport IS the escort (membership
encodes it). LIFT is not a verb — lift is how the executor implements CAPTURE/STAGE
across water.

**Output contract (line-oriented; model-natural, regex-parsable, no JSON brace
fragility for small models — managed mode can grammar-enforce later):**
```
TF <n>: UNITS <id list> | <VERB> <target> | WHY <one line>
BUILD (x,y): <UNIT KIND> | WHY <one line>        (one per owned city)
```
`BUILD` lines answer finding #5 (the model folds production into TFs on its own):
production intent gets its own channel instead of contaminating TF membership. Maps
onto the existing `ProductionOrder` contract; TF lines become the future
`Doctrine`/`TaskForceOrder` TYPES (schema-first now, dataclasses when the seam is
built).

**Validation = the total compile step (BYO trust boundary):** ids ⊆ roster, every unit
in exactly one TF, verb ∈ vocabulary, target resolves (on-board city / known region
name), kind ∈ buildable; anything else → reject → executor-only fallback.

Battery `doctrine` (after sitrep verdict): winner board + the contract appended to
TASK vs the free-prose baseline. Measure: parse rate (mechanical), verb/target
validity, and strategy quality — the known risk is format pressure degrading small-model
reasoning; that is exactly the thing to measure, not assume.

## RESULTS: doctrine v1 (regions board) + v2 (raw grid), THINK vs NOTHINK

12 runs total (2 boards × {THINK,NOTHINK} × 3 seeds), graded by `lab/grade_doctrine.py`
after making the parser lenient on trivia (optional WHY keyword, case, `#` prefixes,
freeform TF labels), strict on semantics (roster, verbs, targets, coverage).

**Headline — user's hypothesis CONFIRMED: think exists for a reason.**
- THINK: 4/6 full PARSE OK; worst failures = 1 near-miss (single non-city STAGE
  target) and 1 non-convergence (v2-s3 hit the 12,288 cap still thinking; no output).
  Zero semantic howlers. 7k–12.3k tokens.
- NOTHINK: **0/6** PARSE OK, and the failures are understanding, not formatting:
  phantom units returned (#11–#13 invented, v2-s3), armies ordered to PATROL open
  water (v1-s3), CAPTURE aimed at a destroyer sighting tile "to secure the neutral
  destroyer" (v1-s2), and v2-s3 leaked 1.3k tokens of visible self-argument
  ("Final Final Orders:") into the answer channel. 0.1k–1.3k tokens.

**The roster-audit discipline lives in the thinking channel** — the "wait, do I have a
satellite? no" loops that killed phantoms in every grounding run are exactly what
NOTHINK skips, and phantoms promptly returned. The 50–90x token gap does not buy a
usable general at 4B. Shipping question reframes to budgeted thinking: THINK at
strategic cadence (~10k tokens every N turns is minutes even at the Q4 CPU floor),
with finish=length treated as a failed epoch → validator rejects → executor fallback
(the non-convergence guard).

**Board trend (weak, n=3/arm):** THINK parsed 3/3 on the regions board but 1/3 on the
raw grid (one near-miss, one runaway) — consistent with grid-decode + contract
accounting competing for the same deliberation budget. Watch, don't conclude.

**Contract v3 fixes queued from observed friction:** drop the WHY keyword (pure
compliance risk — every model omitted it); TF labels freeform; STAGE should accept any
coordinate, not just cities (two runs staged at sensible non-city marshaling points —
engine-native and executor-compilable, the contract was wrong, not the model).

## TASKING CONTINUITY (settled in discussion): the TF registry is ENGINE state

The two-regimes problem (code's idea of tasking vs the model's) and the
snapshot problem (each epoch is a fresh board) share one answer: **task forces are
persistent engine objects** — id, member unit ids, verb, target, formed-on-turn — owned
by the code, amended by the general. One source of truth; there is never a moment when
"what is TF-2" has two answers.

- **Executor side:** each TF compiles to a scoped planning problem (these units, this
  objective); PortfolioAI's plan/playout machinery works *within* TF boundaries. The
  general owns what/with-whom; the staff owns how — the competence floor survives as
  tactical freedom inside each tasking. Compile-time feasibility check: an impossible
  order (CAPTURE across water, no lift) → "cannot comply", reported back, never
  silently reinterpreted. Engine bookkeeping only it can do: attrition updates TF
  rosters; new production lands in an UNASSIGNED pool surfaced in the next briefing.
- **Briefing side: CURRENT TASKINGS as a pure EVENT LEDGER.** Per TF: formed turn,
  roster then/now, combat/capture events since last epoch with outcomes, target
  ownership now. **No status adjectives** — "stalled" is a judgment, and judging is
  what the general is FOR (engine = bookkeeper, not analyst; the temporal cousin of
  the regions rejection). Even summary selection is editorial: report events
  ("capture attempted twice, failed twice"), not curated headlines ("no progress in
  4 turns").
      TF-1  formed t38 · CAPTURE (11,1)
        members then: #3 #4 #5 #6 #7 #9 · now: #3 #4 #6
        since last epoch: lost #5, #7 in combat at (11,1); capture attempted x2, failed x2
        (11,1) still enemy-owned
- **Output side: amendments, not fresh plans** — CONTINUE (default), RETASK, FORM,
  DISBAND. Persistence becomes the path of least resistance (the command-interface
  mirror of §5.2's anti-thrash production rule), and the model's own past intent is
  its strongest grounding anchor.
- **Risks, both measurable:** rubber-stamp anchoring (CONTINUE forever, oblivious to
  the ledger) vs re-plan thrash (dissolving a healthy invasion because it re-read the
  map cold). These are the two failure axes of the next battery.

## PARKED (with trigger conditions)

- **Multi-call decomposition** (user, 2026-07-04): the epoch need not be one prompt —
  the model can digest/group/rank in separate fixed pipeline stages before deciding.
  Still the pre-baked recipe (we wire the stages), not agentic. Do it ONLY on a
  demonstrable reason — the candidate trigger already on film: v2 THINK-s3's runaway
  looked like grid-decode and order-accounting fighting over one deliberation budget;
  if non-convergence or budget-exhaustion grounding errors show up at meaningful rates,
  "split digest from decision" is the named fix. Note a
  model-written digest is exempt from the engine-native rule (model-to-model words, no
  Python taxonomy) — but each stage multiplies CPU-floor latency, so it pays rent or
  stays out.
- **Multimodal board input** (rendered map screenshot instead of ASCII; server already
  loads mmproj) — sidesteps char-grid geometry entirely; revisit after the text line
  is understood.
- ~~Q4 min-spec qualification~~ **RETIRED (user, 2026-07-04): OBE** — BYO means we
  don't control the model choice, and the quant delta on short-prompt structured
  output isn't expected to matter (same for KV-cache quantization: our epochs are
  few-k-token prompts, not chat chains). Residue: when managed mode pins its GGUF,
  run the current battery once against that artifact as a pin-time smoke check —
  one command, not a qualification program.
- **Rules-clarification rev** for unit-role misuse ("TRANSPORT — a hauler, not a
  warship") — fold into the next primer touch.
- **STANDING DOCTRINE section** (user, 2026-07-05, after the stability arcs: "tested
  strategies — guidance on how to play a strong game"). Principled line: DOCTRINE is
  board-independent maxims the general applies with judgment; DICTATION names moves —
  never cross into the latter (the veneer cliff). Every line must be EARNED by a
  measured failure. Draft (~130 static tokens, cache-prefix resident):
      STANDING DOCTRINE  (tested — lessons your service has already paid for)
      - A sound standing order beats a new plan. Amend only what events have
        made wrong; re-planning a working campaign is how campaigns die.
      - Meet surprises with uncommitted reserves first. A committed task
        force pulled off its mission costs you two missions.
      - A transport in waters where an enemy warship may operate sails with
        a warship in its task force, or it waits.
      - Capture attempts consume armies, and attempts fail. Assault with
        spares, never with exactly enough.
      - A staged force exists to launch. When the condition you named
        arrives, go — staging past your own trigger is production wasted.
      - Cities are your only source of force. Match what each builds to the
        campaign's next phase, not its last one.
  SEQUENCING: instrument fixes first (contract v5 REINFORCE + repaired canonical
  histories + cache-native layout = corrected baseline, arcs re-run as regression
  gate), THEN doctrine as a single measured arm. Metrics: A1 churn, B1 reserve
  usage, escort adoption. Grade for doctrine-PARROTING too (citing maxims where they
  don't apply — visible in WHY lines). Long-game note: this section is the
  fine-tuning target; prompt doctrine today = LoRA-baked doctrine later.
- **"Previously on FARSHORE" recap experiment** (user, 2026-07-05): we control the
  context, so the model can't learn from prior moves the way an exposed chain would
  allow — test a recap section vs the snapshot-only briefing, MEASURED. Content tiers:
  (1) engine-authored campaign event log (factual: losses w/ location + TF composition
  at the time — composition is roster fact, cause-attribution would be analyst);
  (2) the model's own prior amendment lines verbatim (model-authored → exempt);
  (3) model-authored war diary replayed forward — HOLD as phase 2: unvalidatable free
  text that compounds across epochs. Test 1+2 as the RECAP arm. Instrument: the LOSS
  scenario + replacement transport — snapshot baseline demonstrably produces the
  unescorted-convoy repeat (LOSS-s1), so the metric is ESCORT ADOPTION RATE on the new
  convoy, plus token cost, runaway rate, and A1-churn (more past to re-litigate is a
  plausible backfire). **Cache constraint (user): the briefing must be KV-cache
  friendly — llama.cpp reuses the longest byte-identical prefix, so order = static
  first, volatile last: primer → contract rules → recap (APPEND-ONLY by design — its
  cache citizenship is half its value) → taskings → board state → turn header. Current
  layout is cache-hostile (contract at the end, turn header near the top). Tension to
  measure: small models anchor on the last instruction (the ROLE finding), so moving
  the contract early needs a tiny static reminder at the very end — verify compliance
  holds, and verify cache reuse via the server's prompt-token timings. Matters most at
  the CPU floor, where full prefill is tens of seconds per epoch.**
- **FORTIFIED assault-sizing probe** (user, 2026-07-05; for game-integration time, not
  the lab batteries): under FORTIFIED_CITIES the approach to a city is an artillery
  killing field — does the general intuit that force is needed, or walk armies into
  death one by one? Two-phase: (1) FORTIFIED scenario with NO artillery text in the
  primer, casualty ledger showing approach losses — does it read the attrition and
  size the next wave up? (this is the doctrine-learning-from-events axis from the
  LOSS scenario, sharpened); (2) add the factual artillery line ("cities fire on
  adjacent enemy units each turn; a lone army rarely survives the approach") and
  measure the delta. Mechanics fact only — never "bring N armies" (pre-chew line).
- **Early-game spot check** (user): one city, world in fog — expected doctrine is
  EXPLORE. Doubles as the contract's degenerate case: the correct order set is nearly
  empty (one BUILD line, maybe one tiny SCOUT tasking), so it probes whether the model
  can issue almost nothing when almost nothing is right, or whether it invents task
  forces to fill the template. Also the purest BUILD-decision test (army-first
  opening from rules alone). Run once the amendment loop is solid.

**Next battery — two-epoch amendment test:** epoch-1 output becomes CURRENT TASKINGS
(hand-built registry + ledger), board mutated by events (e.g. transport sunk
mid-crossing; a TF grinding at a city with two failed captures). Grade: does it
CONTINUE what's healthy, amend what broke, and NOTICE the grinding TF from the raw
ledger alone (retask/reinforce/explicit recommit all pass; oblivious CONTINUE fails)?
If the 4B reads stalls off raw ledgers, the no-analyst design is confirmed with zero
Python smarts; if not, the minimal annotation goes in as a measured fix, not a
precaution. Uses contract v3.

## RESULTS: two-epoch amendment battery (LIFT/GRIND/LOSS ×3, THINK, 8/9 converged)

**HEADLINE — intent does not survive epochs on its own: the registry must persist each
TF's WHY.** LIFT went 0/3: with the ledger recording only `STAGE (5,2)` + events, all
three runs read the staged force as "holding" and NOBODY launched the invasion — the
new transport #16 sat idle in every run, while attention went to retasking the patrol
at the fresh destroyer sighting and (finally) capturing the neutral. The stage's
*purpose* lived in the WHY line of the epoch-1 output and my hand-built ledger dropped
it. Fix (cheap, and engine-native-safe since it's the model's own words replayed
model-to-model): the registry stores the WHY given at FORM/RETASK time and CURRENT
TASKINGS replays it — `STAGE (5,2) — "awaiting second transport before striking east"`.
Re-run LIFT with intent-bearing ledgers as the next battery.

Other findings:
- **Grounding under loss HOLDS: 3/3.** No dead unit id (of #3-#9) appeared in any LOSS
  order; TF-1's fate was handled explicitly (2 DISBAND, 1 retask-to-protect-survivor).
  No phantom ids anywhere in the battery.
- **LOSS recovery coherence mixed:** s1 formed a NEW one-army unescorted convoy into
  the same sea with the ambusher still at large — grounded in the roster, blind to the
  lesson in the ledger. Doctrine-learning-from-events is a real, separate axis.
- **DISBAND semantics gap (contract):** disbanding a TF with survivors (#10) leaves
  their disposition unspecified — s1 orphaned the damaged destroyer. Contract v4:
  DISBAND releases members to UNASSIGNED (say so), or require explicit re-homing.
- **GRIND was too soft to discriminate:** with 4 armies still ashore, CONTINUE is
  *defensible*, and both converged runs continued (with acknowledgment language, no
  reinforcement). Stall-noticing needs a hopeless ledger (all landed armies spent,
  captures failed) to be a real test. Not evidence for engine-side stall analysis yet.
- **UNASSIGNED pool works:** every converged run FORMed with unassigned units —
  the pool did not get forgotten (though transports in it mostly did).
- **Neutral drive now over-fires:** 7/8 runs formed a (4,1) capture TF; LIFT-s3
  double-tasked it (TWO TFs, 9 armies, one neutral city).
- **Amendment-grammar drift:** bare `TF 3: PATROL (8,3)` (RETASK keyword dropped) in
  2 runs — parser should treat a bare verb as RETASK (lenient-trivia rule).
- **BUILD coverage erodes in amendment mode** (3/3 in epoch-1 THINK-v2 → spotty here;
  one run also switched builds to CARRIER/BATTLESHIP on a whim at temp 1.0).
- **1 non-convergence (GRIND-s2, the longest ledger)** — third budget-exhaustion of
  the night; evidence for the parked multi-call trigger keeps accumulating.

## RESULTS: lift2 (WHY-bearing ledger × representation, 5/6 converged)

**WHY persistence works — motivationally.** With the stage's reason replayed in the
ledger, every converged run turned invasion-ward (prior LIFT: 0/3 even looked east).
But only **1/6 composed a legal launch**: WHY-UNI-s1's textbook amendment set —
DISBAND TF-1 + TF-3, FORM TF-4 from the six staged armies + transport + destroyer,
CAPTURE (11,1), capacity-aware and escorted — the best single output of the night.

**New failure mode exposed: rendezvous micromanagement.** 4 runs expressed "armies
must meet the transport" as `STAGE <water tile>` — staging ARMIES at sea coordinates
like (7,2)/(6,0). The model doesn't trust "your officers handle the sea lift" (that
clause only annotated the CAPTURE verb). Primer/contract rev: state the lift
convention prominently — a TF containing armies + a transport ordered CAPTURE across
water loads/sails/unloads automatically; STAGE never targets water. Also seen once:
warship-only TFs ordered to CAPTURE (no armies — compiler catches it).

**Marker leakage (unified board):** WHY-UNI runs ordered `UNITS c d e f g h n p` —
map letters instead of #ids. Expected risk, mild remedy: the validator accepts
markers as our own published deterministic aliases and normalizes to ids
(lenient-trivia rule), keeping #ids canonical in the registry.

**Representation verdict: no token win (means ~11.4k OLD vs ~10.5k UNI), no harm,
and the one flawless composition came from the unified board.** Weak positive at
n=3; keep UNI as the default going forward (it is also simply the truer rendering of
the game's own display), re-evaluate as sample grows. 4th budget-exhaustion runaway
of the night (WHY-OLD-s2) — multi-call receipts keep accumulating.

## RESULTS: lift3 (SEA TRANSPORT primer rev — predictions confirmed)

2/2 converged runs produced the **textbook launch**: DISBAND the stage + the patrol,
FORM one strike TF from the six staged armies + transport + destroyer, CAPTURE (11,1),
explicitly citing escort. Water-staging: **zero** (was 4/6 in lift2). Legal launch:
2/2 (was 1/6 across lift2). The lift convention was the missing knowledge, and one
primer section fixed it. Third confirmed single-edit mechanism of the night
(neutral wording → WHY persistence → lift convention), each: observed failure →
one measured edit → falsifiable prediction → confirmed.

Residue:
- **BUILD lines went 0/3** — amendment mode consistently under-produces them. Better
  design than policing it: contract v4 makes BUILD optional with "no BUILD line =
  keep current build" as the stated default (thrash-averse, shorter output, and
  tonight's omissions become correct behavior retroactively).
- Grammar drift continues in trivial ways (`DISBAND TF 1 |` order-flipped; marker ids
  in one run) — all within the accept-and-normalize policy.
- **1 runaway (s2) — 5th tonight, a stable ~15-20% of THINK runs at the 12,288 cap.**
  This is the real shipping-loop concern: mitigations to evaluate = retry-on-length in
  the validator loop (cheap, ships first), raised cap (costs latency at the CPU
  floor), or the parked multi-call split (evidence keeps accruing).

## RESULTS: runaway mitigation (autopsy + sampler battery + budget probe)

**Autopsy first:** the capped runs are decision churn, NOT token loops (3-4 near-dup
lines out of ~360; GRIND-s2 wrote a complete correct plan inside its thinking, then
died re-litigating contract fine print). Repetition penalties target a disease we
don't have — and would endanger our inherently repetitive answer format.

**Sampler battery (vs lift3 baseline 1/3 runaway):**
- TEMP06 (temp 0.6, presence 1.5): **2/3 runaway — WORSE.** Matches the vendor
  warning that low temperature causes non-termination in thinking models (user called
  this). Temp 1.0 IS the tuned operating point; do not lower it.
- CLAR (ambiguities closed, BUILD-optional): 1/3 runaway — **no better.** The v4
  clarifications stay for design reasons, but they did not move convergence.
- Every converged run in every arm produced the textbook launch. **Composition is
  solved; convergence is a ~1-in-3 lottery no prompt/sampler lever moved.**

**Thinking-budget probe:** per-request `reasoning_budget`/`thinking_budget` fields are
silently ignored by this llama-server build via the OpenAI endpoint (byte-identical
outputs, same seed). Not standard OpenAI (their knob is `reasoning_effort`;
Anthropic's is a token budget). A launch-flag budget on the server side remains
possible but is global, not per-epoch — and a budget only truncates churn anyway;
GRIND-s2 would have been saved at 10k, but a mid-spiral interrupt answers from
half a decision.

**Mitigation shipped: retry-on-length** (`retry_on_length = N` per battery; runner
re-rolls a finish=length run with seed+1000·attempt, records attempt count; default 0
so lab batteries keep observing runaways as data). At ~1/3 runaway rate, 2 retries
give ~96% epoch delivery; the validator's executor-fallback covers the tail. This is
the shipping loop's shape: a runaway is a failed roll, not a failed epoch.

**prodprofile demo: 3/3 epochs delivered, all attempts=1.** Honest asterisk: the
retry path never fired — the explicit min_p=0.0 (vs the prior silent 0.05) re-rolled
the sampling trajectories, and all three seeds happened to converge, including the
one that ran away under CLAR. Confirms min_p is NOT inert (any future comparison
against pre-min_p batteries must account for it) and leaves the retry loop
code-reviewed but not yet exercised live. Output quality: 2/3 textbook launches (s3
took BOTH transports); s2 went conservative — and revealingly kept "awaiting
transport convoy" as its reason while transport #16 sat delivered, a WHY-anchoring
miss worth one line in a future ledger ("the awaited transport has arrived").
Separator drift (· for |, <(x,y)> brackets, quoted WHYs) — all within
accept-and-normalize.

## RESULTS: stability arcs (21 runs; 20 delivered, 8 retries, 1 triple-runaway)

**The user's question — does the judgment hold up across turns? Answer: it holds
when COMMITTED, wobbles when IDLE, reacts to shocks with a nearest-force bias.**

- **Hold-when-committed: STRONG (6/6).** A3 mid-crossing 3/3 CONTINUE (nobody
  recalled the fleet over the shadowing destroyer); C1 beachhead 3/3 pressed the
  assault. Once a campaign is moving, the general keeps its nerve.
- **Hold-when-idle: WEAK (1/3).** A1 (nothing changed, wait 4 more turns) drew a
  premature one-transport launch and a full three-TF re-plan. Idle hands re-plan.
  The anti-thrash default (CONTINUE unless events demand) may need to be *stated*
  in the contract rather than hoped for.
- **Trigger conversion: FRAGILE (0/3 clean at A2** — one triple-retry runaway, one
  water-staging relapse, one liftless RETASK CAPTURE**)** vs lift3's 2/2. High seed
  variance; the water-staging disease is reduced by primer_c, not cured.
- **Shock response: 3/3 reacted, 0/3 proportionally.** All three B1 runs diverted the
  STAGED STRIKE FORCE to defend (4,3) and left six reserve armies idle — including
  two sitting IN the threatened city. Partially defensible (the staged armies are
  geographically closest) but nobody backfilled the stage → the campaign thread
  drops. B2 partially redeems: 2/3 handled defense + launch cleanly in one order set.
- **One true incoherence:** C2-s2 ordered the beachhead TF — five armies ashore on
  the enemy continent — to CAPTURE the home neutral across the ocean. The other two
  C2 runs made a *defensible* retarget ((11,2), the ungarrisoned city, instead of
  re-grinding (11,1)).

**Two causes are OURS, with fixes:**
1. **The canonical histories gaslight the model about (4,1).** A rational general
   would have taken the home neutral 15 turns before these boards; its persistent
   neutrality is an anomaly the model keeps (correctly!) trying to fix — neutral-
   gravity appears in 9 of 18 delivered order sets and reaches absurdity in C2-s2.
   Fix: canonical worlds capture (4,1) around t52; later boards show it as ours.
   The "over-firing neutral drive" may be largely a scenario artifact.
2. **The contract is missing REINFORCE.** B1's right answer (add reserves to the
   defense/stage) requires disbanding a mid-mission TF just to change membership —
   nobody ever does that, so reserves stay idle and standing TFs get diverted
   instead. Contract v5: `TF <id>: REINFORCE UNITS <ids> | <one line>` (keeps
   objective, adds members). Predicted to fix B1 proportionality and reduce
   disband-churn generally.

Delivery stats: retry loop proven (8 firings, incl. one 2-attempt save), but A2-s1
ran away on ALL THREE attempts — same-prompt persistent runaway means some prompts
are churn attractors, not lottery tickets; the multi-call receipts pile grows.

## RESULTS: stability2 (instrument fixes; 16/21 delivered)

**The instruments came clean:** zero neutral-capture orders (the (4,1) obsession was
100% our board artifact — repaired history eliminated it outright, including round-1's
worst incoherence), and REINFORCE was adopted immediately and heavily (12+ uses),
including its exact intended composition: `TF-1: REINFORCE UNITS #16` — the transport
docks into the staged force with no disband dance.

**With clean instruments, the true signature is now unmistakable:**
- **Commitment: 8/8 held** (A3 crossing, C1/C2 beachhead — pressed every time, no
  abandonment, naval threat handled implicitly by the embedded escort).
- **THE standing failure is trigger-to-launch conversion: 0/3.** B2 runs docked the
  awaited transport into TF-1 and STILL left the objective at STAGE — staging past
  their own stated trigger even with the lift physically in formation. A2's only
  delivered run reinforced correctly then gave the CAPTURE order to the wrong TF.
  Doctrine maxim #5 ("a staged force exists to launch") now has a precise target and
  a clean baseline.
- **Shock response FLIPPED failure modes: 2/2 oblivious** (round 1: 3/3 overreacted).
  With REINFORCE available, both B1 runs fed the invasion staging and ignored two
  enemy armies beside (4,3) — reinforce-existing-TFs may have displaced
  form-a-defense as the salient move. Judgment deficit confirmed in both directions;
  doctrine maxim #2's target.
- **New REINFORCE misuse: cross-water reinforcement** (home armies "added" to a fleet
  mid-ocean / beachhead overseas, 4 instances) — grammatically valid, physically
  impossible. The compile step needs a reachability check + a "cannot comply" report
  channel (already designed for infeasible CAPTUREs; REINFORCE joins it).
- **Delivery REGRESSED: 16/21 vs 20/21** — runaways clustered on the high-load epochs
  (A1/A2/B1/B2 each lost a seed). Cannot attribute (three changes bundled): suspects
  are the layout flip (contract moved off the anchor position) and REINFORCE
  enlarging the decision space. If the doctrine arm (same layout) stays this bad,
  layout-vs-load gets its own A/B; the multi-call trigger evidence keeps mounting.

## RESULTS: doctrine arm (19/21 delivered; graded mechanically by grade_amendments.py)

**Maxim #1 works: A1 went 3/3 zero-churn holds** (baseline: premature launches and
full re-plans). Commitment stayed perfect under doctrine (A3 3/3 held, C1/C2 pressed
6/6, no bypass wobble, no doctrine-paralysis on committed ops). No verbatim parroting.

**ROOT CAUSE FOUND for the frozen trigger — a contract interaction, not a judgment
failure.** To launch legally in one epoch the model must dock the transport AND flip
the verb, but one-line-per-TF forbids REINFORCE+RETASK together; the old escape
(DISBAND+FORM) is now correctly discouraged by maxim #1. The maxims and the line rule
interact to lock staged TFs in place. Evidence: B2-s2 BROKE the rule to do the right
thing (RETASK CAPTURE (11,2) + REINFORCE #16, flagged as a 2-line violation); A2-s2
docked the transport with WHY "load convoy for eastern strike" and left the verb at
STAGE — **treating its reason as executable. Reasons are not orders** — worth a
contract sentence: officers execute the VERB; a STAGE TF stays put whatever its WHY
says. **Contract v6: REINFORCE and RETASK for the same TF are permitted together in
one epoch** (they are one military act: "commit the new asset and go").

**Maxim #2 half-works — reserves now get used (5/6 B-runs) but aimed at the wrong
city:** garrison-feeds to the capital (2,0) while the landing threatens (4,3), whose
own two idle armies again went untouched. Salience hypothesis: TF-2 is the only
DEFEND-verbed TF, so defense impulses route into it regardless of geography. The
spatial-threat read remains the genuine open deficit (not fixable by contract; maybe
by ledger phrasing of WHERE the threat points, which is bookkeeper-legal: "two enemy
armies adjacent to city (4,3)" — already present — the miss is real).

Delivery 19/21 with heavy retry traffic (18 firings) — doctrine text neither calmed
nor worsened the churn attractors; retries rescued more than stability2's run.

## RESULTS: earlygame probe (3/3, first attempt, textbook restraint)

One city, one army, world in fog: **every run emitted exactly ONE order line** —
`FORM TF 1: UNITS 1 | SCOUT EAST` (the predicted explore instinct) or a single
neutral-city capture (both defensible openings). Zero template-filling, zero phantom
units, zero gratuitous BUILD lines (the optional-BUILD default behaving). The
contract's degenerate case passes completely: the model can issue almost nothing
when almost nothing is right.

## RESULTS: trigger-v6 (7/9 delivered, 12 retries)

**Trigger prediction: PARTIAL.** The v6 pair was used exactly as designed once —
B2-s3: `REINFORCE UNITS #16 #11 #12` + `RETASK CAPTURE (11,1)`, legal, feasible,
launched. Mechanism proven. But reliability didn't follow: one half-conversion each
way (dock-no-flip, flip-no-dock), and A2 stayed a churn attractor (2/3 undelivered;
that board has now eaten 7 of 9 attempts across batteries — candidates: retire/split
it, and it heads the multi-call evidence file).

**The user's complexity worry: VINDICATED IN DIRECTION.** B2-s2 merged the pair into
ONE line on its own (`REINFORCE UNITS #16 | RETASK STAGE (7,2)`) — the RETASK rode
in the WHY slot, invisible to the parser: silent drift, worse than a loud error. The
model *wants* single-line composition — which is exactly the user's proposed simpler
form. **Contract v7 (adopt): drop the two-line exception; RETASK gains an optional
add — `TF 1: RETASK CAPTURE (11,1) ADDING #16 #11` — one line, one rule.**

**Maxim-2 aim refinement: WEAK.** 1/3 aimed at the right city — but by diverting the
staged strike force, not by using reserves (right city, wrong force); the others
repeated garrison-feeding and a tile-CAPTURE diversion. **Spatial threat salience is
now confirmed robust across three contract/doctrine generations — parked as the
standing model-side deficit.** Next lever class if pursued: not wording — scenario
variation (threat adjacency, garrison emptiness) to map when salience does/doesn't
fire; or accept and let the executor's own threat response backstop it (the
competence floor exists precisely for this).

**Lab phase closes here** (per the post-v6 recommendation): contract v7 freezes as
the schema draft; next work = build-order steps 1-2 (shared TYPES + FakeGeneral
seam), lab retained for regression.

## WRINKLE LEDGER (consolidation punch list — iron, don't A/B; user call 2026-07-05)

1. **One canonical parser/validator** replacing the three overlapping lab grammars
   (grade_doctrine, compile_orders, grade_amendments) — becomes the real compile-step
   validator in engine code, carrying the full accept-and-normalize catalog: markers →
   ids, `·`/`|` separators, `<(x,y)>` brackets, bare verb = RETASK, freeform TF
   labels, optional keywords, case.
2. **Silent drift detection:** reasons containing order-like text (verb + target in a
   WHY slot) are flagged/rejected — "reasons are not orders" enforced mechanically,
   not just stated. (B2-s2 is the fixture.)
3. **Cannot-comply channel:** infeasible orders (liftless cross-water CAPTURE,
   unreachable REINFORCE, land units STAGEd on water) reject WITH a reported reason
   that appears in the next epoch's ledger. Never silently reinterpreted.
4. **Delivery pipeline as code:** token cap → seed-shifted retries → reject →
   executor-only epoch. (~1/3 runaway is an accepted operating cost at the
   accelerated tier.)
5. **Briefing spec FROZEN** (deploy shape, no further layout experiments): unified
   board w/ marker cross-index, WHY persisted at FORM/RETASK, pure event ledger,
   arrived-precondition event lines, cache-native ordering + end-reminder, contract
   v7 text.
6. **Test corpus:** the lab's ~60 real transcripts become validator unit fixtures —
   every observed drift replayed forever.
Parked with labels: spatial threat salience (model limit; executor backstop), churn
attractors (multi-call evidence file), "previously on" recap, FORTIFIED assault
probe, early-game BUILD-decision variants.

**Order compiler (lab/compile_orders.py, user ask):** the second half of the compile
step, prototyped — takes parsed orders + the SAME board text the model read (terrain
from the ASCII grid, landmasses by flood fill, roster parsed from the prompt) and
answers "what would the game do": pipeline (LAND ASSAULT / AMPHIBIOUS ASSAULT /
GARRISON / MARSHAL / SEA PATROL / RECON), feasibility (lift present, capacity, terrain
class, ownership), and the action skeleton. First run over doctrine-v2 found what
validation could not:
- caught INFEASIBLE orders that were grammatically valid (CAPTURE (11,2) by one army
  with no transport; land units patrolling sea; CAPTURE of an own city ×5 in the
  NOTHINK-s3 meltdown);
- exposed a CONTRACT GAP: both clean THINK-v2 runs compiled to fully-feasible but
  entirely defensive plans (marshal + garrison + patrol + BUILD TRANSPORT, no enemy
  CAPTURE at all). Coherent build-up-first posture — but the contract cannot express
  sequenced intent ("stage now, strike when the second transport launches"), so a
  staged plan is indistinguishable from turtling. The tasking-continuity design is the
  intended answer (intent persists as standing TFs amended across epochs) — the
  two-epoch battery should test exactly this: does the staged TF convert to CAPTURE
  once the lift exists?

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

CROSSING WATER
  An army cannot enter water. To move armies across water: load them into a
  transport at a coast/city, sail, unload onto adjacent land. Cargo is
  kind-locked — transports carry armies only, carriers carry fighters only;
  ships never carry ships. If a loaded carrier is destroyed, everything aboard
  is lost with it.

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
the output contract exists to forbid exactly this. **Verdict: REGIONS stays, for the
orders-vocabulary coupling, not for tokens.** The token lever is the thinking dial
(OFF is the intended shipping default and had never been measured) → folded into the
doctrine battery as a THINK/NOTHINK arm.

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
Targets: a city `(x,y)` or a REGION NAME from the theater briefing. Coverage check:
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

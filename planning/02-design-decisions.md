# Design Decisions Log

Cross-cutting design decisions made during planning. Update entries here if a decision is revisited.

---

## D-001 — Language: Python, strictly object-oriented

**Decision:** Python (3.11+ assumed for modern typing) with strict OO discipline. No procedural-script fallback even when shorter.

**Date:** 2026-05-22

**Rationale:** User preference. Performance is not a constraint (turn-based, small grids). OO models the domain naturally — units, cities, players, maps are all rich entities with state and behavior. Discipline keeps the codebase navigable as it grows.

**Implications:**
- Entry point is a thin shim that instantiates a `Game` (or equivalent) and calls `.run()`. No real logic at module scope.
- Polymorphism for unit types — `Unit` subclasses or strategy objects, NOT `if unit_type == "army"` branches.
- Free functions only for true stateless utilities; even then, prefer staticmethods on the relevant class.
- No module-level mutable state.

---

## D-002 — Scope v1: single-player vs AI

**Decision:** First playable target is single-player vs one AI opponent. No hotseat, no networking.

**Date:** 2026-05-22

**Rationale:** Smallest scope that produces a complete, playable game. Lets us focus AI effort.

**Future:** Hotseat would be cheap to add later (just gate `Player.is_ai`). Networking is out of scope.

---

## D-003 — Rule variations ship as named `RuleSet` presets

**Decision:** The game's default rules are defined in [`01-game-rules-spec.md`](01-game-rules-spec.md). Rule variations ship as **named `RuleSet` presets** — coherent bundles of rule values play-tested as a whole — rather than à la carte toggles.

**Date:** 2026-05-22

**Rationale:** Curated bundles avoid the combinatorial trap where independently sensible toggles combine into broken/unfun configurations. Game balance is a property of the rule *set*, not of individual rules. The default preset is *our* baseline, designed as part of this project; it is not a reproduction of any prior product's rules.

**Implications:**
- `RuleSet` is a first-class object passed to `Game` at construction. Every place that consults a rule must read from it, not a hardcoded value.
- Shipped presets (initial): `STANDARD` (the baseline defined in `01-game-rules-spec.md`); future bundles named for their theme (e.g. `MODERN`, `STACKED_COMBAT`).
- No user-exposed "build your own ruleset" mechanism in v1. Custom bundles live in code, are play-tested, then shipped.
- A `RuleSet` is still implemented as a typed object internally — so individual rule values are addressable in code — but the *menu* of options the player sees is the preset list, not a checkbox grid.

---

## D-004 — TUI library: Textual

**Decision:** Use [Textual](https://github.com/Textualize/textual) for the TUI layer.

**Date:** 2026-05-22

**Rationale:**
- Modern, actively maintained.
- Rich rendering (256/true-color, mouse, CSS-like styling).
- Widget/screen abstractions reduce the layout drudgery curses requires.
- Async event loop — fits well with later possibilities (smooth animation, background AI worker for "thinking" indicator).
- Good developer experience (live reload, CSS-driven theming).

**Implications:**
- Rendering layer lives in Textual `App` / `Widget` classes. **Keep these as thin views** — they read from `Game`/`Map`/`Unit` objects and dispatch input events; they do NOT contain game logic.
- Single hard dep on `textual` (and its transitive `rich`).
- Some classic UI conventions (`V` to set city function, scrolling map cursor, etc.) need to map cleanly to Textual widgets. Open question: do we want a single MapWidget that owns the cursor, or a separate CursorOverlay? Defer until sketching class hierarchy.

**Risks:**
- Textual's API has evolved fast; pin a known-good minor version.
- Mouse-heavy users vs. keyboard-purists may pull the UI in opposite directions. Keep keyboard the primary input; mouse is a bonus.

---

## D-005 — Map size: configurable; persistence: JSON with schema version

**Decision:** Map dimensions are a runtime parameter (default ~100×60; smaller for quick play, larger as a stretch). Saves are JSON files with an explicit `schema_version` field.

**Date:** 2026-05-22

**Rationale:**
- **Configurable size:** new players probably want quicker games. Larger maps are a natural sandbox for the better AI we're planning.
- **JSON saves:** human-inspectable, easy to migrate across schema changes via versioned readers, no binary endianness issues.
- **Schema version field:** lets us evolve the save format without breaking older saves. Pattern: each `Game.load(path)` reads the version field and dispatches to the matching reader, which may upgrade-in-memory before returning a current-form `Game`.

**Implications:**
- `MapGenerator` takes dimensions as parameters. Default profile presets: SMALL (~50×30), STANDARD (100×60), LARGE (150×90). Settable on CLI / startup menu.
- City count and water ratio should scale with map area, not be hardcoded.
- Save file structure: top-level dict with `schema_version`, `rules` (the active `RuleSet`), `map`, `cities`, `units`, `players`, `turn`, `rng_state`. Pretty-print on save (`indent=2`).
- **RNG state must be persisted** for reproducibility — see D-006.

**Risks:**
- JSON for a 150×90 map with full unit state could grow into hundreds of KB. Mitigate by compact representations where possible (run-length for terrain, compact per-tile only where state exists).
- If saves grow large enough to be slow, fall back to JSON-lines or msgpack. Don't optimize prematurely.

---

## D-006 — RNG: explicit, seeded, persisted

**Decision:** Use Python's `random.Random` instance owned by `Game`, seeded explicitly at game start. RNG state must be saved with the game state.

**Date:** 2026-05-22

**Rationale:**
- A per-game `Random` instance with explicit seed enables deterministic tests of combat, AI behavior, and map generation.
- Persisting RNG state across save/load preserves the same sequence of outcomes, which matters for save-scumming prevention (or detection) and for replay/debug.
- Avoids the trap of process-wide global RNG state, which makes parallel tests and reproducibility impossible.

**Implications:**
- Nothing in the codebase calls module-level `random.random()` / `random.randint()`. Everything goes through `game.rng` (or a `RNG` adapter).
- `Game.__init__(seed: int | None = None)`. None → seed from time.
- `save()` writes `random.getstate()` tuple; `load()` restores it.

---

## D-007 — AI design: three personalities (baseline / strategic / LLM) sharing one interface

**Decision:** Three AI personalities, all conforming to a single `AIController` interface and swappable at game start. Full design in [`03-ai-design.md`](03-ai-design.md).

1. **`BaselineAI`** — a simple greedy per-unit BFS AI. Serves as a regression baseline and a "lightweight opponent" mode. Not the default.
2. **`StrategicAI` (default)** — hierarchical: `IntelService` → `Strategist` → `OperationalPlanner` → `TacticalExecutor`. Goal-seeking, A* with danger-weighted costs, real combat evaluation, task-force composition.
3. **`LLMAI`** — `StrategicAI` with the top `Strategist` swapped for an LLM-driven planner. **Targets small locally-run models first** (CPU-runnable, no GPU required); larger hosted models are an optional upgrade, not the design center. LLM only sets goals; deterministic layers handle pathfinding and combat. Strict JSON schema with fallback to deterministic strategist on timeout/parse failure. Fine-tuning a small model on gameplay traces is an explicit option if base models underperform.

**Date:** 2026-05-22

**Rationale:**
- A pure per-unit greedy AI is a sound baseline but has no strategic layer. A layered OO design fixes this.
- The three personalities give us: a controlled baseline (`Baseline`), the actual default upgrade (`Strategic`), and an experimental high-ceiling opponent (`LLM`).
- Sharing the interface means engine has no idea which AI is playing — clean separation, easy to A/B test.

**Implications:**
- AI must respect fog of war by default. `fog_cheat` toggle exists but is OFF at every difficulty level unless the user explicitly enables it. "Beat the player without cheating" is an explicit design goal.
- `LLMAI` runtime is pluggable: default backend is a small local model (llama.cpp / Ollama / similar, CPU-runnable); optional hosted backend (Claude API) for users who want it. No GPU assumed.
- Build order defers LLM work last (see §8 of `03-ai-design.md`) so it ships against a known-good deterministic baseline.

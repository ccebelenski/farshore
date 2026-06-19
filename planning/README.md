# Empire — Planning Documents

Planning artifacts. This directory captures design decisions made *before* writing code so that the implementation phase has a clear target.

**Status:** Planning complete for v1. Phase 0 (project skeleton) landed; substantive implementation has not yet started.

## Index

| Doc | Contents |
|---|---|
| [`00-project-notes.md`](00-project-notes.md) | Short workflow notes. |
| [`01-game-rules-spec.md`](01-game-rules-spec.md) | Design specification for the game's rules — terrain, units, movement, combat, fog of war, victory. |
| [`02-design-decisions.md`](02-design-decisions.md) | Decision log (D-001 through D-007): language, scope, rules policy, TUI library, persistence, RNG, AI architecture. Each entry has decision, rationale, and implications. |
| [`03-ai-design.md`](03-ai-design.md) | AI architecture: three personalities (`BaselineAI`, `StrategicAI`, `LLMAI`) sharing one interface. Layered design (Intel → Strategist → Operational → Tactical). LLM-as-strategist boundary. Difficulty knobs. Build order. §9 `SearchAI` (plan-space lookahead — the decisive line). §10 strategy & value foundation (campaigns as first-class: strategy=how / value=why, horizon-as-progress-probe, principled strategy-level commitment). |
| [`04-class-hierarchy.md`](04-class-hierarchy.md) | Module/package layout. Core domain (`Game`, `Map`, `Unit` hierarchy, `City`, `RuleSet`). Combat, mapgen, pathfinding, AI, persistence, TUI layers. Dependency direction. Testing strategy. |
| [`05-implementation-plan.md`](05-implementation-plan.md) | Phased build order (18 phases) with per-phase deliverables, canary tests, and exit gates. Continuous quality gates (lint, type-check, import discipline, test). Validation milestones at Phase 10 (engine) and Phase 15 (StrategicAI win-rate). |

## Quick summary

**Goal:** A turn-based wargame in Python.

**Stack:**
- **Language:** Python 3.11+, strictly object-oriented.
- **TUI:** [Textual](https://textual.textualize.io/).
- **AI:** Custom hierarchical strategic AI as default; greedy baseline AI for regression testing; optional LLM-driven AI (small local model preferred).
- **Persistence:** JSON saves with explicit schema version + migration chain.
- **Map size:** Configurable (default profile 100×60).

**Scope (v1):** Single-player vs one AI opponent.

**Design tenets:**
1. **Standard ruleset by default.** Rule variations ship as named `RuleSet` presets, not à la carte toggles.
2. **OO discipline.** No procedural fallback. Everything is a class.
3. **No AI cheating.** The AI plays under the same fog of war as the player by default. Cheats are opt-in difficulty knobs.
4. **`core` depends on nothing.** Domain model isolated from UI and AI. Testable on its own.
5. **Determinism via explicit RNG.** Seeded `random.Random` instance, state persisted with saves.

## Next steps (when implementation begins)

Per `05-implementation-plan.md`, build order in brief:

1. Core domain + engine turn loop + rules. Hotseat-playable for validation.
2. `BaselineAI` (greedy per-unit). Validates engine correctness.
3. `IntelService` (used by both AI and player HUD).
4. `StrategicAI` (deterministic). Benchmark vs `BaselineAI` (target: >60% win rate).
5. `LLMAI`. Optional opponent; layered on top of the deterministic baseline.
6. Save format hardening, schema migrations, replay support if there's demand.

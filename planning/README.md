# Empire — Planning Documents

Planning artifacts for the modernized TUI Empire reimplementation. This directory captures design decisions made *before* writing code so that the implementation phase has a clear target.

**Status:** Planning complete for v1. Implementation has not yet started.

## Index

| Doc | Contents |
|---|---|
| [`01-classic-rules-reference.md`](01-classic-rules-reference.md) | Canonical rules of Walter Bright's VMS Empire, extracted from the C source with `file:line` citations. The authoritative reference for default ("classic") game behavior. |
| [`02-design-decisions.md`](02-design-decisions.md) | Decision log (D-001 through D-007): language, scope, rules policy, TUI library, persistence, RNG, AI architecture. Each entry has decision, rationale, and implications. |
| [`03-ai-design.md`](03-ai-design.md) | AI architecture: three personalities (`ClassicAI`, `StrategicAI`, `LLMAI`) sharing one interface. Layered design (Intel → Strategist → Operational → Tactical). LLM-as-strategist boundary. Difficulty knobs. Build order. |
| [`04-class-hierarchy.md`](04-class-hierarchy.md) | Module/package layout. Core domain (`Game`, `Map`, `Unit` hierarchy, `City`, `RuleSet`). Combat, mapgen, pathfinding, AI, persistence, TUI layers. Dependency direction. Testing strategy. |
| [`05-implementation-plan.md`](05-implementation-plan.md) | Phased build order (18 phases) with per-phase deliverables, canary tests, and exit gates. Continuous quality gates (lint, type-check, import discipline, test). Validation milestones at Phase 10 (engine) and Phase 15 (StrategicAI win-rate). |

## Quick summary

**Goal:** A modernized TUI reimplementation of Walter Bright's *Empire: Wargame of the Century*.

**Stack:**
- **Language:** Python 3.11+, strictly object-oriented.
- **TUI:** [Textual](https://textual.textualize.io/).
- **AI:** Custom hierarchical strategic AI as default; faithful classic AI for baseline; optional LLM-driven AI using the Claude API.
- **Persistence:** JSON saves with explicit schema version + migration chain.
- **Map size:** Configurable (default profile matches classic 100×60).

**Scope (v1):** Single-player vs one AI opponent.

**Design tenets:**
1. **Classic rules first.** Every rule deviation is gated behind a `RuleSet` toggle, off by default.
2. **OO discipline.** No procedural fallback. Everything is a class.
3. **No AI cheating.** The AI plays under the same fog of war as the player by default. Cheats are opt-in difficulty knobs.
4. **`core` depends on nothing.** Domain model isolated from UI and AI. Testable on its own.
5. **Determinism via explicit RNG.** Seeded `random.Random` instance, state persisted with saves.

## Reference material

The original VMS C source — the authoritative reference for default behavior; all citations in `01-classic-rules-reference.md` point into it — lives in a separate upstream repo: [DigitalMars/Empire-for-VMS](https://github.com/DigitalMars/Empire-for-VMS). It is not tracked in this project. Clone it alongside if you want to follow citations:

```sh
git clone https://github.com/DigitalMars/Empire-for-VMS.git
```

## Next steps (when implementation begins)

Per `03-ai-design.md` §8, implementation order:

1. Core domain + engine turn loop + classic rules. Hotseat-playable for validation.
2. `ClassicAI` (direct port from C). Validates engine correctness.
3. `IntelService` (used by both AI and player HUD).
4. `StrategicAI` (deterministic). Benchmark vs `ClassicAI` (target: >60% win rate).
5. `LLMAI`. Optional opponent; layered on top of the deterministic baseline.
6. Save format hardening, schema migrations, replay/movie support if there's demand.

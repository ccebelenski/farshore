# FARSHORE — Terra Incognita

A turn-based wargame in Python.

Gameplay: a turn-based, single-player-vs-AI wargame on a grid map. Cities
produce military units; players capture each other's cities until one side
controls the world. Fog of war is in effect: the world starts unknown, and
the enemy is always on the far shore.

> Inspired by Walter Bright's *EMPIRE: Wargame of the Century*.
> FARSHORE is an independent, from-scratch implementation — it contains no
> original Empire code and is not affiliated with or endorsed by Walter
> Bright.

**Status:** playable — classic ruleset, TUI, two AI opponents (greedy horde
and the search-based portfolio commander).

See [`planning/`](planning/) for design docs and the phased implementation plan.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
make install   # sync dev environment
make check     # run all quality gates: lint, typecheck, imports, test
```

## Documents

- [`planning/README.md`](planning/README.md) — index of design docs
- [`planning/00-project-notes.md`](planning/00-project-notes.md) — short workflow notes
- [`planning/01-game-rules-spec.md`](planning/01-game-rules-spec.md) — design spec for the game's rules
- [`planning/02-design-decisions.md`](planning/02-design-decisions.md) — cross-cutting design decisions
- [`planning/03-ai-design.md`](planning/03-ai-design.md) — AI architecture (baseline / strategic / LLM)
- [`planning/04-class-hierarchy.md`](planning/04-class-hierarchy.md) — module layout + class skeletons
- [`planning/05-implementation-plan.md`](planning/05-implementation-plan.md) — phased build with exit gates

## License

[MIT](LICENSE).

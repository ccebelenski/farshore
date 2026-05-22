# Empire

A modernized TUI reimplementation of Walter Bright's *Empire: Wargame of the Century*.

**Status:** Phase 0 — project skeleton. Implementation in progress.

See [`planning/`](planning/) for design docs and the phased implementation plan.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
make install   # sync dev environment
make check     # run all quality gates: lint, typecheck, imports, test
```

## Documents

- [`planning/README.md`](planning/README.md) — index of design docs
- [`planning/01-classic-rules-reference.md`](planning/01-classic-rules-reference.md) — canonical VMS rules with `file:line` citations
- [`planning/02-design-decisions.md`](planning/02-design-decisions.md) — cross-cutting design decisions
- [`planning/03-ai-design.md`](planning/03-ai-design.md) — AI architecture (classic / strategic / LLM)
- [`planning/04-class-hierarchy.md`](planning/04-class-hierarchy.md) — module layout + class skeletons
- [`planning/05-implementation-plan.md`](planning/05-implementation-plan.md) — phased build with exit gates

## Reference

The original VMS C source — used as the canonical rules reference during planning and porting — lives in a separate upstream repo: [DigitalMars/Empire-for-VMS](https://github.com/DigitalMars/Empire-for-VMS). It is **not** tracked here. If you want to follow the `file:line` citations in `planning/01-classic-rules-reference.md`, clone it alongside this repo:

```sh
git clone https://github.com/DigitalMars/Empire-for-VMS.git
```

It is gitignored at `Empire-for-VMS/` for convenience if you clone it into the working tree.

## License

GPL-3.0-or-later, matching the original.

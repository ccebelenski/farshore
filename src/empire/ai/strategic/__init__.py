"""The deterministic strategic AI (see `planning/03-ai-design.md` §3).

Pipeline: `IntelService` (intel/) → `DeterministicStrategist` (goals) →
operational planner → tactical executor (later phases).

Import the concrete pieces from their submodules (`.strategist`,
`.feasibility`, `.memory`, `.goals`, `.intel`) — this package `__init__` is
kept import-free so the submodules can reference each other without a
package-initialization cycle.
"""

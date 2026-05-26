"""Phase-0 smoke test: every package is importable and the entrypoint runs."""

import importlib

import pytest

PACKAGES = [
    "empire",
    "empire.core",
    "empire.contracts",
    "empire.events",
    "empire.combat",
    "empire.mapgen",
    "empire.pathfinding",
    "empire.persistence",
    "empire.ai",
    "empire.ai.strategic",
    "empire.ai.llm",
    "empire.tui",
    "empire.tui.widgets",
    "empire.tui.screens",
    "empire.tui.modals",
]


@pytest.mark.parametrize("pkg", PACKAGES)
def test_package_importable(pkg: str) -> None:
    importlib.import_module(pkg)


# `test_entrypoint_runs` lived here; it duplicated
# `test_cli.py::test_main_with_empty_argv_prints_help_and_returns_zero` which
# also asserts the captured output. Dropped.

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


def test_entrypoint_runs() -> None:
    from empire.__main__ import main

    assert main() == 0

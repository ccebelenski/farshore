"""Shared pytest fixtures across the test suite."""

from __future__ import annotations

import pytest

from empire.contracts.controller import AIController, NullController


@pytest.fixture()
def null_controller() -> AIController:
    """A no-op `AIController` instance; safe to pass anywhere an AI is required."""
    return NullController()

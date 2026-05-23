"""Phase-2 canary tests for `Player`."""

from empire.core.identity import PlayerId
from empire.core.map import ViewMap
from empire.core.player import Player


def test_player_construction() -> None:
    view = ViewMap()
    p = Player(id=PlayerId(1), name="Alice", is_ai=False, view=view, color="red")
    assert p.id == 1
    assert p.name == "Alice"
    assert p.is_ai is False
    assert p.view is view
    assert p.color == "red"


def test_player_default_color_is_default() -> None:
    p = Player(id=PlayerId(2), name="AI", is_ai=True, view=ViewMap())
    assert p.color == "default"


def test_player_does_not_hold_controller_reference() -> None:
    """Per planning/04 §2: controllers live on Game, not on Player."""
    p = Player(id=PlayerId(1), name="P", is_ai=True, view=ViewMap())
    assert not hasattr(p, "controller")

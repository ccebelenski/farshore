"""Phase-2 canary tests for `Player`.

Player is a thin dataclass — most of its surface is `@dataclass` machinery,
not Player behavior. Tests focus on the two things that *are* contracts:
the default color, and the discipline rule that Player has no controller
field (controllers live on Game per planning/04 §2).
"""

from empire.core.identity import PlayerId
from empire.core.map import ViewMap
from empire.core.player import Player


def test_player_default_color_is_default() -> None:
    p = Player(id=PlayerId(2), name="AI", is_ai=True, view=ViewMap())
    assert p.color == "default"


def test_player_does_not_hold_controller_reference() -> None:
    """Per planning/04 §2: controllers live on Game, not on Player."""
    p = Player(id=PlayerId(1), name="P", is_ai=True, view=ViewMap())
    assert not hasattr(p, "controller")

"""`PlayoutModel` fidelity: a clone replays the original bit-for-bit and is
fully independent of it (Phase 15.8 Step 0)."""

from empire._arena import ARENA_PROFILE, build_land_brawl
from empire.ai.baseline import BaselineAI
from empire.ai.search.playout import PlayoutModel
from empire.contracts.controller import AIController
from empire.core.game import Game
from empire.core.identity import PlayerId
from empire.core.ruleset import FORTIFIED_CITIES
from empire.persistence.schema import Serializer


def _land_brawl_at_turn(turn: int) -> Game:
    built = build_land_brawl(ARENA_PROFILE, seed=3, rules=FORTIFIED_CITIES)
    assert built is not None
    game, players = built
    for p in players:
        game.attach_controller(p.id, BaselineAI())
    while game.turn < turn and not game.is_over():
        game.run_turn()
    assert not game.is_over()
    return game


def _controllers(game: Game) -> dict[PlayerId, AIController]:
    return {p.id: BaselineAI() for p in game.players}


def test_clone_replays_original_bit_for_bit() -> None:
    """Same RNG state + same (stateless) controller types ⇒ the clone's next
    N turns produce exactly the original's next N turns."""
    game = _land_brawl_at_turn(12)
    clone = PlayoutModel().clone(game, _controllers(game))

    for _ in range(6):
        game.run_turn()
        clone.run_turn()

    serializer = Serializer()
    assert serializer.to_dict(clone) == serializer.to_dict(game)


def test_clone_is_independent_of_original() -> None:
    """Running the clone must not move a single bit of the original."""
    game = _land_brawl_at_turn(12)
    serializer = Serializer()
    before = serializer.to_dict(game)

    clone = PlayoutModel().clone(game, _controllers(game))
    for _ in range(6):
        clone.run_turn()
        if clone.is_over():
            break

    assert serializer.to_dict(game) == before


def test_clone_combat_resolver_is_wired() -> None:
    """The clone carries a real CombatResolver, not the null resolver whose
    resolve() would raise mid-playout. (Combat itself is exercised bit-for-bit
    by the replay tests above, which run six turns of a land brawl.)"""
    game = _land_brawl_at_turn(12)
    clone = PlayoutModel().clone(game, _controllers(game))
    from empire.combat.resolver import CombatResolver

    assert isinstance(clone.combat_resolver, CombatResolver)

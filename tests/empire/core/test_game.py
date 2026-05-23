"""Phase-4 canary tests for `Game` and `TurnManager`."""

from __future__ import annotations

import pytest

from empire.contracts.controller import NullController
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.events import GameEndedEvent, TurnAdvancedEvent
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.events.bus import EventBus


def _empty_map(width: int = 4, height: int = 4) -> Map:
    tiles: dict[Coord, Tile] = {}
    for x in range(width):
        for y in range(height):
            c = Coord(x, y)
            tiles[c] = Tile(coord=c, terrain=TerrainKind.LAND)
    return Map(width=width, height=height, tiles=tiles)


@pytest.fixture()
def players() -> list[Player]:
    return [
        Player(id=PlayerId(1), name="P1", is_ai=False, view=ViewMap()),
        Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap()),
    ]


# --- Construction -----------------------------------------------------------


def test_game_construction_defaults(players: list[Player]) -> None:
    g = Game(rules=STANDARD, real_map=_empty_map(), players=players)
    assert g.rules is STANDARD
    assert g.turn == 0
    assert g.controllers == {}


def test_game_seed_is_deterministic(players: list[Player]) -> None:
    g1 = Game(rules=STANDARD, real_map=_empty_map(), players=players, seed=42)
    g2 = Game(rules=STANDARD, real_map=_empty_map(), players=players, seed=42)
    assert g1.rng.random() == g2.rng.random()


# --- Controllers ------------------------------------------------------------


def test_attach_controller_replaces_existing(players: list[Player]) -> None:
    """attach_controller stores its argument AND replaces any prior controller
    at the same PlayerId. Both behaviors in one test since the second case
    subsumes the first."""
    g = Game(rules=STANDARD, real_map=_empty_map(), players=players)
    first = NullController()
    g.attach_controller(PlayerId(2), first)
    assert g.controllers[PlayerId(2)] is first
    second = NullController()
    g.attach_controller(PlayerId(2), second)
    assert g.controllers[PlayerId(2)] is second


# --- player_by_id -----------------------------------------------------------


def test_player_by_id_finds_known_player(players: list[Player]) -> None:
    g = Game(rules=STANDARD, real_map=_empty_map(), players=players)
    assert g.player_by_id(PlayerId(1)) is players[0]
    assert g.player_by_id(PlayerId(99)) is None


# --- Turn loop --------------------------------------------------------------


def test_run_turn_increments_turn_counter(players: list[Player]) -> None:
    g = Game(rules=STANDARD, real_map=_empty_map(), players=players)
    g.run_turn()
    assert g.turn == 1


def test_ten_empty_turns_complete_without_crashing(players: list[Player]) -> None:
    """Phase-4 exit-gate canary: a Game with empty phase methods survives
    repeated `run_turn()` calls. Real production / movement / combat lands
    in Phase 8; here we're just verifying the plumbing.
    """
    g = Game(rules=STANDARD, real_map=_empty_map(), players=players)
    for _ in range(10):
        g.run_turn()
    assert g.turn == 10


def test_run_turn_publishes_turn_advanced_event(players: list[Player]) -> None:
    bus = EventBus()
    seen: list[TurnAdvancedEvent] = []
    bus.subscribe(TurnAdvancedEvent, seen.append)
    g = Game(rules=STANDARD, real_map=_empty_map(), players=players, event_bus=bus)
    g.run_turn()
    g.run_turn()
    assert [e.turn for e in seen] == [1, 2]


# --- Endgame predicate ------------------------------------------------------


def test_game_with_no_cities_is_not_over(players: list[Player]) -> None:
    g = Game(rules=STANDARD, real_map=_empty_map(), players=players)
    assert g.is_over() is False
    assert g.winner() is None


def _map_with_owned_cities(owners: list[Player | None]) -> Map:
    """Build a 4x4 map with one city per supplied owner; cities at increasing
    x-coords on row 0. Pass `None` for neutral.
    """
    tiles: dict[Coord, Tile] = {}
    for x in range(4):
        for y in range(4):
            c = Coord(x, y)
            tiles[c] = Tile(coord=c, terrain=TerrainKind.LAND)
    next_id = 1
    for x, owner in enumerate(owners):
        c = Coord(x, 0)
        city = City(id=CityId(next_id), coord=c, owner=owner)
        tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=city)
        next_id += 1
    return Map(width=4, height=4, tiles=tiles)


def test_game_with_both_players_having_cities_is_not_over(players: list[Player]) -> None:
    m = _map_with_owned_cities([players[0], players[1]])
    g = Game(rules=STANDARD, real_map=m, players=players)
    assert g.is_over() is False


def test_game_is_over_when_only_one_player_has_cities(players: list[Player]) -> None:
    m = _map_with_owned_cities([players[0], players[0]])  # P1 owns both; P2 has none
    g = Game(rules=STANDARD, real_map=m, players=players)
    assert g.is_over() is True
    assert g.winner() is players[0]


def test_neutral_cities_alone_do_not_end_the_game(players: list[Player]) -> None:
    m = _map_with_owned_cities([None, None])  # both neutral; no winner yet
    g = Game(rules=STANDARD, real_map=m, players=players)
    assert g.is_over() is False
    assert g.winner() is None


def test_run_turn_publishes_game_ended_when_finished(players: list[Player]) -> None:
    bus = EventBus()
    ended: list[GameEndedEvent] = []
    bus.subscribe(GameEndedEvent, ended.append)
    m = _map_with_owned_cities([players[0]])  # P1 alone has cities
    g = Game(rules=STANDARD, real_map=m, players=players, event_bus=bus)
    g.run_turn()
    assert len(ended) == 1
    assert ended[0].winner_id == PlayerId(1)
    assert ended[0].final_turn == 1

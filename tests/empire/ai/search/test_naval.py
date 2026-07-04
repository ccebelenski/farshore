"""Naval orchestration: an `INVADE` objective ferries armies across water
and storms an overseas city (Phase 15.9).

These drive the real engine via a `PlanFollower`, so they exercise the
whole load → sail → amphibious-unload pipeline end to end."""

from __future__ import annotations

import dataclasses

from empire.ai.search.follower import PlanFollower
from empire.ai.search.plan import Objective, Plan, Role
from empire.combat.resolver import CombatResolver
from empire.contracts.controller import NullController
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Transport

# Two landmasses {x=0,1} and {x=4,5} split by a water channel {x=2,3}.
# Capitals at (1,0) and (4,0) are both coastal (touch the channel).
_ROWS = [
    "LCWWCL",
    "LLWWLL",
    "LLWWLL",
]


def _build() -> tuple[Game, Player, Player, City]:
    p1 = Player(id=PlayerId(1), name="P1", is_ai=True, view=ViewMap(), color="red")
    p2 = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap(), color="blue")
    terrain = {"L": TerrainKind.LAND, "W": TerrainKind.WATER, "C": TerrainKind.CITY}
    p1_city = City(id=CityId(1), coord=Coord(1, 0), owner=p1)
    p2_city = City(id=CityId(2), coord=Coord(4, 0), owner=p2)
    cities = {Coord(1, 0): p1_city, Coord(4, 0): p2_city}
    tiles: dict[Coord, Tile] = {}
    for y, row in enumerate(_ROWS):
        for x, ch in enumerate(row):
            c = Coord(x, y)
            if c in cities:
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=cities[c])
            else:
                tiles[c] = Tile(coord=c, terrain=terrain[ch])
    real_map = Map(width=6, height=len(_ROWS), tiles=tiles)
    rules = dataclasses.replace(STANDARD, army_capture_city_deterministic=True)
    game = Game(rules=rules, real_map=real_map, players=[p1, p2], seed=1,
                combat_resolver=CombatResolver())
    # Full visibility for P1 so the test is fog-independent.
    p1.view.visible = {Coord(x, y) for x in range(6) for y in range(len(_ROWS))}
    return game, p1, p2, p2_city


def test_invade_objective_ferries_and_captures_overseas_city() -> None:
    game, p1, p2, target = _build()
    # P1: a transport afloat next to its coast + three armies on the landmass.
    transport = Transport(UnitId(10), p1, Coord(2, 1))  # water beside the coast
    game.map.place_unit(transport, Coord(2, 1))
    for i, spot in enumerate([Coord(0, 0), Coord(0, 1), Coord(1, 1)]):
        game.map.place_unit(Army(UnitId(20 + i), p1, spot), spot)

    game.attach_controller(p1.id, PlanFollower(
        Plan(objectives=(Objective(target.coord, Role.INVADE, 3),))
    ))
    game.attach_controller(p2.id, NullController())

    captured_turn = None
    for _ in range(40):
        game.run_turn()
        if target.owner is p1:
            captured_turn = game.turn
            break
        if game.is_over():
            break

    assert target.owner is p1, "invasion never captured the overseas city"
    assert captured_turn is not None and captured_turn <= 40


def test_generator_switches_to_naval_when_home_is_won() -> None:
    """With no land-reachable target left and a coastal enemy city overseas,
    the generator drops the land/army baselines and emits an INVADE plan."""
    from empire.ai.search.generator import CandidateGenerator
    from empire.contracts.world_view import WorldView
    from empire.core.unit import UnitKind

    game, p1, _p2, target = _build()  # P1 owns the only city on its landmass
    view = WorldView(real_map=game.map, player=p1, turn=game.turn, rules=game.rules)
    plans = CandidateGenerator().generate(view)

    invade = [
        o for plan in plans for o in plan.objectives if o.role is Role.INVADE
    ]
    assert invade, "expected an INVADE objective once home land is exhausted"
    assert any(o.target == target.coord for o in invade)
    # No transport yet → at least one invade plan builds one.
    assert any(
        p.production is UnitKind.TRANSPORT
        for p in plans
        if any(o.role is Role.INVADE for o in p.objectives)
    )


def test_generator_recons_when_no_overseas_target_known() -> None:
    """Home won, nothing seen overseas, but unexplored ocean → build patrols
    and scout the sea."""
    from empire.ai.search.generator import CandidateGenerator
    from empire.contracts.world_view import WorldView
    from empire.core.unit import UnitKind

    game, p1, _p2, target = _build()
    # Hide the enemy city: P1 sees only its own landmass + near water.
    p1.view.visible = {
        Coord(x, y) for x in range(3) for y in range(len(_ROWS))
    }
    view = WorldView(real_map=game.map, player=p1, turn=game.turn, rules=game.rules)
    plans = CandidateGenerator().generate(view)
    del target
    assert any(p.production is UnitKind.PATROL for p in plans), (
        "expected a patrol-recon plan when ocean is unexplored"
    )

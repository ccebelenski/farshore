"""Tests for `BriefingRenderer`: a small real `Game` in, a fog-honest,
cache-native `Briefing` out.

The fixture board (7x5, all tiles on-board like the persistence tests):

- P1 (us): capital (1,1) building ARMY (work 3 of 5); armies #1 (in the
  capital), #2 at (2,2), #5 at (0,0) UNASSIGNED; transport #3 at (4,2)
  carrying army #4.
- Neutral city (3,0), currently visible.
- P2 (enemy): city (6,0) known only from memory; destroyer #10 at (5,3)
  in sight NOW; army #11 at (3,3) that P1 has NEVER seen (the fog-honesty
  canary); a stale army sighting at (6,4) remembered from t12.
"""

from __future__ import annotations

import re

import pytest

from empire.ai.general.briefing import BriefingRenderer
from empire.contracts.doctrine import Objective, TaskForce, TaskForceId, Verb
from empire.contracts.world_view import WorldView
from empire.core.city import City, ProductionState
from empire.core.coord import Coord
from empire.core.game import Game
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map, RememberedTile, UnitSnapshot, ViewMap
from empire.core.player import Player
from empire.core.ruleset import STANDARD
from empire.core.tile import TerrainKind, Tile
from empire.core.unit import Army, Destroyer, Transport, UnitKind
from empire.persistence.schema import Serializer

# --- fixture -------------------------------------------------------------------

TURN = 20


def _build_game() -> Game:
    p1 = Player(id=PlayerId(1), name="Us", is_ai=True, view=ViewMap(), color="red")
    p2 = Player(id=PlayerId(2), name="Them", is_ai=True, view=ViewMap(), color="blue")

    own_city = City(
        id=CityId(1),
        coord=Coord(1, 1),
        owner=p1,
        production=ProductionState(building=UnitKind.ARMY, work=3),
    )
    neutral_city = City(id=CityId(2), coord=Coord(3, 0), owner=None)
    enemy_city = City(id=CityId(3), coord=Coord(6, 0), owner=p2)

    tiles: dict[Coord, Tile] = {}
    for x in range(7):
        for y in range(5):
            c = Coord(x, y)
            terrain = TerrainKind.LAND if x <= 3 else TerrainKind.WATER
            if c == Coord(6, 4):
                terrain = TerrainKind.LAND  # eastern island corner
            tiles[c] = Tile(coord=c, terrain=terrain)
    for city in (own_city, neutral_city, enemy_city):
        tiles[city.coord] = Tile(coord=city.coord, terrain=TerrainKind.CITY, city=city)
    real_map = Map(width=7, height=5, tiles=tiles)

    real_map.place_unit(Army(UnitId(1), p1, Coord(1, 1)), Coord(1, 1))
    real_map.place_unit(Army(UnitId(2), p1, Coord(2, 2)), Coord(2, 2))
    transport = Transport(UnitId(3), p1, Coord(4, 2))
    real_map.place_unit(transport, Coord(4, 2))
    cargo = Army(UnitId(4), p1, Coord(4, 2))
    real_map.place_unit(cargo, Coord(4, 2))
    real_map.load_cargo(transport, cargo)
    real_map.place_unit(Army(UnitId(5), p1, Coord(0, 0)), Coord(0, 0))

    real_map.place_unit(Destroyer(UnitId(10), p2, Coord(5, 3)), Coord(5, 3))
    # The fog canary: a real enemy army P1 has never seen. It must leave no
    # trace anywhere in the briefing.
    real_map.place_unit(Army(UnitId(11), p2, Coord(3, 3)), Coord(3, 3))

    p1.view.visible = {
        Coord(0, 0),
        Coord(1, 1),
        Coord(2, 2),
        Coord(3, 0),
        Coord(4, 2),
        Coord(5, 3),
    }
    p1.view.remembered[Coord(6, 0)] = RememberedTile(
        coord=Coord(6, 0),
        terrain=TerrainKind.CITY,
        remembered_at=10,
        last_city_owner=PlayerId(2),
    )
    p1.view.remembered[Coord(6, 4)] = RememberedTile(
        coord=Coord(6, 4),
        terrain=TerrainKind.LAND,
        remembered_at=12,
        last_units=[
            UnitSnapshot(
                unit_id=UnitId(12),
                kind=UnitKind.ARMY,
                owner_id=PlayerId(2),
                coord=Coord(6, 4),
                hits=1,
            ),
        ],
    )

    game = Game(rules=STANDARD, real_map=real_map, players=[p1, p2], seed=7)
    game.turn = TURN
    return game


def _registry() -> dict[TaskForceId, TaskForce]:
    return {
        "1": TaskForce(
            tf_id="1",
            members=frozenset({UnitId(1), UnitId(2)}),
            objective=Objective(verb=Verb.DEFEND, target=Coord(1, 1)),
            why="keep the capital garrisoned",
            formed_turn=10,
        ),
        "2": TaskForce(
            tf_id="2",
            members=frozenset({UnitId(3), UnitId(4)}),
            objective=Objective(verb=Verb.CAPTURE, target=Coord(6, 0)),
            why="strike east with the loaded transport",
            formed_turn=15,
        ),
    }


def _events() -> dict[TaskForceId, list[str]]:
    return {
        "1": ["garrisoned; no contact; no losses"],
        "2": [
            "loaded #4 at t16; crossing since t17",
            "t19: enemy destroyer sighted at (5,3)",
        ],
    }


@pytest.fixture()
def briefing_text() -> str:
    game = _build_game()
    view = WorldView(game.map, game.players[0], TURN, STANDARD)
    return BriefingRenderer().render(view, _registry(), _events(), TURN).text


# --- parsing helpers -----------------------------------------------------------


def _map_cells(text: str) -> dict[tuple[int, int], str]:
    """Parse the MAP rows into {(x, y): glyph}."""
    cells: dict[tuple[int, int], str] = {}
    for line in text.splitlines():
        match = re.match(r"^ r(\d+)  (.+)$", line)
        if match:
            y = int(match.group(1))
            for x, cell in enumerate(match.group(2).split()):
                cells[(x, y)] = cell
    return cells


def _unit_rows(text: str) -> dict[int, tuple[str, str, str]]:
    """Parse the UNITS table into {unit id: (marker, full line, tasking)}."""
    rows: dict[int, tuple[str, str, str]] = {}
    lines = text.splitlines()
    start = lines.index("ENTIRE force; you have NOTHING else") + 1
    for line in lines[start:]:
        match = re.match(r"^  (\S)  #(\d+)\s", line)
        if match is None:
            break
        rows[int(match.group(2))] = (match.group(1), line, line.split()[-1])
    return rows


# --- (c) cache-native section order ---------------------------------------------


def test_section_order_is_cache_native(briefing_text: str) -> None:
    anchors = [
        "=== ORDERS CONTRACT ===",
        "CURRENT TASKINGS",
        "MAP  legend:",
        "UNITS  (map marker",
        "\nMY CITIES\n",
        "\nNEUTRAL CITIES",
        "\nKNOWN ENEMY\n",  # the section header, not the map legend's cross-reference
        f"It is TURN {TURN}.",
    ]
    positions = [briefing_text.index(anchor) for anchor in anchors]
    assert positions == sorted(positions)
    assert briefing_text.startswith("=== ORDERS CONTRACT ===")


def test_turn_cue_is_last(briefing_text: str) -> None:
    assert briefing_text.endswith(
        f"It is TURN {TURN}. Issue your amendment orders now — ONLY the line\n"
        "forms defined in the ORDERS CONTRACT above.\n"
    )


def test_contract_text_is_v7(briefing_text: str) -> None:
    # ADDING is shown as its own example line: bracket optionality notation
    # got copied literally by a live model (handshake (b), seed 2).
    assert "TF <id>: RETASK <VERB> <target> | <one line>" in briefing_text
    assert "TF <id>: RETASK <VERB> <target> ADDING <ids> | <one line>" in briefing_text
    assert "[ADDING" not in briefing_text
    # The v5/v6 two-line REINFORCE+RETASK exception is gone.
    assert "ONE exception" not in briefing_text
    assert "Every standing TF gets exactly one line." in briefing_text
    assert "TF <id>: REINFORCE UNITS <ids> | <one line>" in briefing_text
    assert "Officers execute the" in briefing_text
    assert "VERB, not your reasons" in briefing_text
    assert "A BUILD line is optional per city" in briefing_text


# --- (a) markers: map and table agree --------------------------------------------


def test_unit_markers_match_units_table(briefing_text: str) -> None:
    cells = _map_cells(briefing_text)
    rows = _unit_rows(briefing_text)
    assert set(rows) == {1, 2, 3, 4, 5}
    # Markers are a, b, c... in table (id) order.
    assert [rows[i][0] for i in (1, 2, 3, 4, 5)] == ["a", "b", "c", "d", "e"]
    for unit_id, (marker, line, _tasking) in rows.items():
        if "aboard" in line:
            assert marker not in cells.values(), f"#{unit_id} is cargo but drawn on the map"
        elif "in city" in line:
            match = re.search(r"\((\d+),(\d+)\)", line)
            assert match is not None
            x, y = int(match.group(1)), int(match.group(2))
            assert cells[(x, y)] == "O", f"#{unit_id} should show as its city glyph"
            assert marker not in cells.values()
        else:
            match = re.search(r"\((\d+),(\d+)\)", line)
            assert match is not None
            x, y = int(match.group(1)), int(match.group(2))
            assert cells[(x, y)] == marker


def test_taskings_column_covers_every_unit(briefing_text: str) -> None:
    rows = _unit_rows(briefing_text)
    assert rows[1][2] == "TF-1"
    assert rows[2][2] == "TF-1"
    assert rows[3][2] == "TF-2"
    assert rows[4][2] == "TF-2"
    # UNASSIGNED units appear in the table's tasking column — no separate section.
    assert rows[5][2] == "UNASSIGNED"
    assert "UNASSIGNED UNITS" not in briefing_text


# --- (b) fog honesty --------------------------------------------------------------


def test_never_seen_tiles_render_fog(briefing_text: str) -> None:
    cells = _map_cells(briefing_text)
    for coord in [(1, 0), (0, 4), (3, 4), (5, 0)]:
        assert cells[coord] == "?", f"{coord} was never seen, must render as fog"


def test_unseen_enemy_unit_leaves_no_trace(briefing_text: str) -> None:
    # Enemy army #11 really is at (3,3) but P1 never saw it: fog on the map,
    # no mention anywhere in the text.
    cells = _map_cells(briefing_text)
    assert cells[(3, 3)] == "?"
    assert "(3,3)" not in briefing_text
    assert "#11" not in briefing_text


def test_visible_enemy_gets_marker_and_in_sight_line(briefing_text: str) -> None:
    cells = _map_cells(briefing_text)
    assert cells[(5, 3)] == "X"
    assert "  X  enemy destroyer at (5,3), in sight now" in briefing_text
    assert "X = enemy units in sight NOW (see KNOWN ENEMY)" in briefing_text


def test_remembered_enemy_renders_with_age_never_on_map(briefing_text: str) -> None:
    # Stale sighting from t12 at t20 = 8 turns ago; remembered tiles keep
    # their last-seen terrain (land, not fog), but stale units get no marker.
    assert "enemy army at (6,4) seen 8 turns ago" in briefing_text
    cells = _map_cells(briefing_text)
    assert cells[(6, 4)] == "."


def test_remembered_enemy_city_renders_glyph_and_intel_line(briefing_text: str) -> None:
    cells = _map_cells(briefing_text)
    assert cells[(6, 0)] == "E"
    assert "city (6,0)" in briefing_text


def test_city_glyphs_and_city_sections(briefing_text: str) -> None:
    cells = _map_cells(briefing_text)
    assert cells[(3, 0)] == "N"
    assert "NEUTRAL CITIES  (3,0)" in briefing_text
    # ARMY takes 5 work, 3 accumulated -> 2 turns left.
    assert "  (1,1) building ARMY, 2 turns left" in briefing_text


# --- (d) why replayed verbatim ----------------------------------------------------


def test_why_replayed_verbatim_in_quotes(briefing_text: str) -> None:
    assert '"keep the capital garrisoned"' in briefing_text
    assert '"strike east with the loaded transport"' in briefing_text
    assert 'TF-1  formed t10 · DEFEND (1,1) — "keep the capital garrisoned"' in briefing_text
    assert 'TF-2  formed t15 · CAPTURE (6,0) — "strike east with the loaded transport"' in (
        briefing_text
    )


def test_event_ledger_lines_replayed_under_since(briefing_text: str) -> None:
    assert "    since: garrisoned; no contact; no losses" in briefing_text
    assert "    since: loaded #4 at t16; crossing since t17" in briefing_text
    assert "      t19: enemy destroyer sighted at (5,3)" in briefing_text


# --- FLEET DISPATCHES: the general/fleet section --------------------------------------


def test_fleet_dispatches_omitted_when_no_general_events(briefing_text: str) -> None:
    # The fixture passes no general events (4-arg render): the block, and any
    # "none" filler, must be entirely absent.
    assert "FLEET DISPATCHES" not in briefing_text


def test_fleet_dispatches_renders_turn_stamped_lines_after_taskings() -> None:
    game = _build_game()
    view = WorldView(game.map, game.players[0], TURN, STANDARD)
    general = [
        "t18: transport #16 produced at (1,2)",
        "t19: lost #16 at (4,2)",
    ]
    text = BriefingRenderer().render(view, _registry(), _events(), TURN, general).text
    assert "FLEET DISPATCHES  (events since your last briefing; forces outside" in text
    assert "  t18: transport #16 produced at (1,2)" in text
    assert "  t19: lost #16 at (4,2)" in text
    # Placed after CURRENT TASKINGS and before the MAP (cache-native zone).
    assert text.index("CURRENT TASKINGS") < text.index("FLEET DISPATCHES")
    assert text.index("FLEET DISPATCHES") < text.index("MAP  legend:")


def test_fleet_dispatches_reproduces_the_unassigned_loss_visibility() -> None:
    """The playtest bug's fix at the render seam: an unassigned unit's loss,
    booked in the general section, now reaches the general — it used to fall
    into `report.general`, which the briefing never rendered."""
    game = _build_game()
    view = WorldView(game.map, game.players[0], TURN, STANDARD)
    text = BriefingRenderer().render(
        view, {}, {}, TURN, ["t19: lost #16 at (4,2)"]
    ).text
    assert "FLEET DISPATCHES" in text
    assert "t19: lost #16 at (4,2)" in text


# --- (e) cargo aboard transports ---------------------------------------------------


def test_aboard_unit_listed_as_aboard_and_carrier_as_carrying(briefing_text: str) -> None:
    rows = _unit_rows(briefing_text)
    assert "aboard #3" in rows[4][1]
    assert "carrying #4" in rows[3][1]
    # The transport itself IS on the map at its sea cell.
    assert _map_cells(briefing_text)[(4, 2)] == rows[3][0]


# --- (f) unit inside a city ---------------------------------------------------------


def test_unit_in_city_shows_city_glyph_with_note(briefing_text: str) -> None:
    rows = _unit_rows(briefing_text)
    assert "in city" in rows[1][1]
    assert _map_cells(briefing_text)[(1, 1)] == "O"


# --- misc: empty registry + read-only guarantee ---------------------------------------


def test_empty_registry_renders_none_block(briefing_text: str) -> None:
    game = _build_game()
    view = WorldView(game.map, game.players[0], TURN, STANDARD)
    text = BriefingRenderer().render(view, {}, {}, TURN).text
    assert "(none — the war has just begun; you have issued no orders yet." in text
    # With no registry, every unit is UNASSIGNED in the table.
    assert all(row[2] == "UNASSIGNED" for row in _unit_rows(text).values())
    # And the fixture briefing (standing TFs) does NOT contain that block.
    assert "the war has just begun" not in briefing_text


def test_render_is_read_only_over_game_state() -> None:
    game = _build_game()
    before = Serializer().to_dict(game)
    view = WorldView(game.map, game.players[0], TURN, STANDARD)
    registry = _registry()
    events = _events()
    BriefingRenderer().render(view, registry, events, TURN)
    assert Serializer().to_dict(game) == before
    assert registry == _registry()
    assert events == _events()

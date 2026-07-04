"""Canary tests for the `empire.cli` map-dump utility."""

from __future__ import annotations

import pytest

from empire.cli import dump_map, main, render_ascii
from empire.core.coord import Coord
from empire.core.map import Map
from empire.core.tile import TerrainKind, Tile


def _toy_map() -> Map:
    """3x3 map: top-left land, center water, bottom-right border."""
    tiles: dict[Coord, Tile] = {}
    for x in range(3):
        for y in range(3):
            c = Coord(x, y)
            on_board = not (x == 2 and y == 2)
            terrain = TerrainKind.WATER if (x, y) == (1, 1) else TerrainKind.LAND
            tiles[c] = Tile(coord=c, terrain=terrain, on_board=on_board)
    return Map(width=3, height=3, tiles=tiles)


# --- render_ascii ------------------------------------------------------------


def test_render_ascii_dimensions_match_map() -> None:
    m = _toy_map()
    rendered = render_ascii(m)
    lines = rendered.split("\n")
    assert len(lines) == m.height
    for line in lines:
        assert len(line) == m.width


def test_render_ascii_uses_expected_chars() -> None:
    m = _toy_map()
    rendered = render_ascii(m)
    lines = rendered.split("\n")
    # Top-left corner is land.
    assert lines[0][0] == "."
    # Center is water.
    assert lines[1][1] == "~"
    # Bottom-right is off-board border.
    assert lines[2][2] == "#"


def test_render_ascii_marks_cities() -> None:
    from empire.core.city import City
    from empire.core.identity import CityId

    city = City(id=CityId(1), coord=Coord(0, 0), owner=None)
    tile = Tile(coord=Coord(0, 0), terrain=TerrainKind.CITY, city=city)
    tiles = {Coord(0, 0): tile, Coord(1, 0): Tile(coord=Coord(1, 0), terrain=TerrainKind.WATER)}
    m = Map(width=2, height=1, tiles=tiles)
    assert render_ascii(m) == "*~"


# --- dump_map ----------------------------------------------------------------


def test_dump_map_header_includes_profile_and_seed() -> None:
    text = dump_map("SMALL", seed=0)
    assert "SMALL" in text
    assert "seed=0" in text
    assert "cities placed" in text


def test_dump_map_includes_legend_by_default() -> None:
    text = dump_map("SMALL", seed=0)
    assert "Legend" in text


def test_dump_map_can_suppress_legend() -> None:
    text = dump_map("SMALL", seed=0, show_legend=False)
    assert "Legend" not in text


def test_dump_map_renders_each_profile() -> None:
    """SMALL/STANDARD/LARGE all work."""
    for profile in ("SMALL", "STANDARD", "LARGE"):
        text = dump_map(profile, seed=0)
        assert profile in text


# --- main entry point --------------------------------------------------------


def test_main_with_empty_argv_launches_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare invocation (no argv) launches the TUI menu — not help.

    `EmpireApp.run` is patched to a no-op: the real one enters a blocking
    asyncio event loop that never returns without a terminal, which would
    hang the suite. We assert dispatch, not the live UI.
    """
    import empire.tui

    launched: list[bool] = []
    monkeypatch.setattr(
        empire.tui.EmpireApp, "run", lambda self: launched.append(True)
    )
    assert main([]) == 0
    assert launched == [True]


def test_main_dump_map_subcommand_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dump-map", "--profile", "SMALL", "--seed", "1"]) == 0
    captured = capsys.readouterr()
    assert "SMALL" in captured.out
    assert "Legend" in captured.out


def test_main_dump_map_no_legend_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dump-map", "--profile", "SMALL", "--no-legend"]) == 0
    captured = capsys.readouterr()
    assert "Legend" not in captured.out


def test_main_rejects_unknown_profile() -> None:
    with pytest.raises(SystemExit):
        main(["dump-map", "--profile", "GIGANTIC"])

"""Phase-1 canary tests for `Coord` and `Direction`."""

from empire.core.coord import Coord, Direction


def test_step_then_reverse_returns_original() -> None:
    start = Coord(5, 5)
    for d in Direction:
        moved = start.step(d)
        reverse = Coord(moved.x - d.dx, moved.y - d.dy)
        assert reverse == start


def test_direction_offsets_are_eight_unit_vectors() -> None:
    offsets = list(Direction.offsets())
    assert len(offsets) == 8
    assert len(set(offsets)) == 8
    for dx, dy in offsets:
        assert dx in (-1, 0, 1)
        assert dy in (-1, 0, 1)
        assert (dx, dy) != (0, 0)


def test_chebyshev_is_symmetric() -> None:
    a = Coord(2, 3)
    b = Coord(7, 11)
    assert a.chebyshev_to(b) == b.chebyshev_to(a)


def test_chebyshev_diagonal_equals_orthogonal() -> None:
    """A 3-cell diagonal move is the same Chebyshev distance as a 3-cell straight move."""
    origin = Coord(0, 0)
    assert origin.chebyshev_to(Coord(3, 0)) == 3
    assert origin.chebyshev_to(Coord(0, 3)) == 3
    assert origin.chebyshev_to(Coord(3, 3)) == 3


def test_chebyshev_self_is_zero() -> None:
    c = Coord(4, 7)
    assert c.chebyshev_to(c) == 0


def test_neighbors_are_eight_unique_and_exclude_self() -> None:
    c = Coord(5, 5)
    neighbors = list(c.neighbors())
    assert len(neighbors) == 8
    assert len(set(neighbors)) == 8
    assert c not in neighbors
    for n in neighbors:
        assert c.chebyshev_to(n) == 1


def test_coord_is_hashable_and_value_equal() -> None:
    s = {Coord(1, 2), Coord(1, 2), Coord(3, 4)}
    assert len(s) == 2

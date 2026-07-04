"""Phase-1 canary tests for `Coord` and `Direction`."""

from empire.core.coord import Coord, Direction


def test_step_moves_to_a_distinct_adjacent_cell_for_each_direction() -> None:
    """Each Direction steps to a distinct immediate neighbor (chebyshev 1) —
    exercising `step` without re-deriving it from the direction's offsets."""
    start = Coord(5, 5)
    stepped = [start.step(d) for d in Direction]
    assert len(set(stepped)) == 8
    for s in stepped:
        assert s != start
        assert start.chebyshev_to(s) == 1


def test_direction_offsets_are_eight_unit_vectors() -> None:
    offsets = list(Direction.offsets())
    assert len(offsets) == 8
    assert len(set(offsets)) == 8
    for dx, dy in offsets:
        assert dx in (-1, 0, 1)
        assert dy in (-1, 0, 1)
        assert (dx, dy) != (0, 0)


def test_chebyshev_distance() -> None:
    """Symmetric; a diagonal span equals the orthogonal span; self is zero."""
    a, b = Coord(2, 3), Coord(7, 11)
    assert a.chebyshev_to(b) == b.chebyshev_to(a)
    origin = Coord(0, 0)
    assert origin.chebyshev_to(Coord(3, 0)) == 3
    assert origin.chebyshev_to(Coord(0, 3)) == 3
    assert origin.chebyshev_to(Coord(3, 3)) == 3
    assert origin.chebyshev_to(origin) == 0


def test_neighbors_are_eight_unique_and_exclude_self() -> None:
    c = Coord(5, 5)
    neighbors = list(c.neighbors())
    assert len(neighbors) == 8
    assert len(set(neighbors)) == 8
    assert c not in neighbors
    for n in neighbors:
        assert c.chebyshev_to(n) == 1

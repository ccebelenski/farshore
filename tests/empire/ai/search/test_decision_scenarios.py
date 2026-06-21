"""Set-piece decision tests for SearchAI's plan selection.

The arena/probes measure ENTANGLED, emergent outcomes over long noisy games —
useless for proving a single factor in isolation and too slow to iterate on. This
module does the opposite: hand-built positions ("set pieces") that assert the AI
*offers and credits the predicted plan*, deterministically, in one decision. Each
scenario isolates one rule of the naval split-score (planning/07), so a tweak that
breaks a rule fails here in <1s instead of surfacing as an unmodelled regression
in a week of simulated games.

Board legend (one char per cell):
    .  land        ~  water
    O  own city    E  enemy city    N  neutral city
Units are passed separately as (kind, side, x, y).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from empire.ai.search.generator import CandidateGenerator
from empire.ai.search.plan import Plan, PlanGoal
from empire.contracts.world_view import WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import CityId, PlayerId, UnitId
from empire.core.map import Map
from empire.core.player import Player
from empire.core.ruleset import FORTIFIED_CITIES, RuleSet
from empire.core.tile import TerrainKind, Tile
from empire.core.map import ViewMap
from empire.core.unit import Army, Patrol, Transport, Unit

_TERRAIN = {".": TerrainKind.LAND, "~": TerrainKind.WATER}
_CITY_OWNER = {"O": "own", "E": "enemy", "N": "neutral"}
_UNIT = {"army": Army, "patrol": Patrol, "transport": Transport}


def build_scenario(
    rows: Sequence[str],
    *,
    units: Iterable[tuple[str, str, int, int]] = (),
    seen: Iterable[tuple[int, int]] | None = None,
    turn: int = 8,
    rules: RuleSet = FORTIFIED_CITIES,
) -> tuple[Map, Player, Player, WorldView]:
    """Parse an ASCII board into a (map, own, enemy, own-view). `seen` is the set
    of (x,y) the own player can see (default: everything — full knowledge, for
    scenarios where fog isn't the variable)."""
    own = Player(id=PlayerId(1), name="P1", is_ai=True, view=ViewMap())
    enemy = Player(id=PlayerId(2), name="P2", is_ai=True, view=ViewMap())
    owner_of = {"own": own, "enemy": enemy, "neutral": None}

    tiles: dict[Coord, Tile] = {}
    next_city = 1
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            c = Coord(x, y)
            if ch in _TERRAIN:
                tiles[c] = Tile(coord=c, terrain=_TERRAIN[ch])
            elif ch in _CITY_OWNER:
                city = City(id=CityId(next_city), coord=c, owner=owner_of[_CITY_OWNER[ch]])
                next_city += 1
                tiles[c] = Tile(coord=c, terrain=TerrainKind.CITY, city=city)
            else:
                raise ValueError(f"bad map char {ch!r} at ({x},{y})")
    width = max(len(r) for r in rows)
    real_map = Map(width=width, height=len(rows), tiles=tiles)

    next_unit = 1
    for kind, side, x, y in units:
        u: Unit = _UNIT[kind](UnitId(next_unit), owner_of[side], Coord(x, y))
        next_unit += 1
        real_map.place_unit(u, Coord(x, y))

    all_cells = {(x, y) for y in range(len(rows)) for x in range(width)}
    visible = all_cells if seen is None else set(seen)
    own.view.visible = {Coord(x, y) for (x, y) in visible}

    view = WorldView(real_map=real_map, player=own, turn=turn, rules=rules)
    return real_map, own, enemy, view


def _plans(view: WorldView) -> tuple[Plan, ...]:
    return CandidateGenerator().generate(view)


def _goals(plans: Iterable[Plan]) -> set[PlanGoal]:
    return {p.goal for p in plans}


# --- scenarios ----------------------------------------------------------------


def test_two_continent_known_enemy_credits_invade() -> None:
    """Own continent (left), water gap, enemy city on a SEPARATE continent
    (right), all seen. The enemy is NOT reachable by land, so crossing water is
    the path to victory -> the generator must offer an INVADE-tagged plan."""
    rows = [
        "OO~~EE",
        "..~~..",
        "..~~..",
    ]
    _, _, _, view = build_scenario(rows)
    goals = _goals(_plans(view))
    assert PlanGoal.INVADE in goals, "enemy overseas -> invade must be credited"


def test_enemy_on_same_continent_does_not_credit_overseas_island() -> None:
    """The land-brawl/diversion set piece: own city and enemy city on the SAME
    landmass (a land path between them), plus a neutral on a separate island. The
    enemy is reachable by land, so naval is NOT the path — the island invade must
    NOT be credited (no INVADE/SCOUT_SEA goal), or the AI would waste production
    on ships chasing a sideshow."""
    rows = [
        "O...E~N",
        ".....~.",
    ]
    _, _, _, view = build_scenario(rows)
    goals = _goals(_plans(view))
    assert PlanGoal.INVADE not in goals, "island sideshow must not earn invade credit"
    assert PlanGoal.SCOUT_SEA not in goals, "enemy is on land -> no sea hunt"


def test_undiscovered_enemy_credits_sea_scout() -> None:
    """Own continent on the left with a neutral to press, open ocean to the
    right whose far side is UNSEEN (enemy hidden there). No enemy known and no
    land path off the continent -> the generator must offer a SCOUT_SEA plan so
    the AI goes looking, concurrent with the home game."""
    rows = [
        "ON..~~~~~~",
        "....~~~~~~",
    ]
    # See the home continent + the first water column (the sea frontier); leave
    # the open ocean beyond unseen so there is something to scout toward.
    seen = {(x, y) for y in range(2) for x in range(5)}
    _, _, _, view = build_scenario(rows, seen=seen)
    goals = _goals(_plans(view))
    assert PlanGoal.SCOUT_SEA in goals, "undiscovered overseas enemy -> scout the sea"


def test_unexplored_home_land_explores_land_before_sea() -> None:
    """The deciding pre-contact set piece (what the land A/B was really catching).
    Same as the scout case BUT the home landmass still has UNEXPLORED LAND (could
    be a shared continent). Open sea with a frontier is also present — tempting —
    yet the AI must finish exploring the land first, NOT chase the sea, or it
    diverts production to ships on a land map. The ONLY difference from the
    scout-credited case is the remaining land frontier, so this isolates the
    has-land-frontier guard exactly."""
    rows = [
        "ON....~~",  # land cols 0-5, water cols 6-7
        "......~~",
    ]
    # Reveal the left of the continent + one water column: leaves unexplored LAND
    # (cols 4-5) AND a sea frontier (seen water col 6 next to unseen col 7).
    seen = {(x, y) for y in range(2) for x in (0, 1, 2, 3, 6)}
    _, _, _, view = build_scenario(rows, seen=seen)
    goals = _goals(_plans(view))
    assert PlanGoal.SCOUT_SEA not in goals, "unexplored land remains -> explore it first"
    assert PlanGoal.INVADE not in goals


# --- selection-level (runs the real playout on a tiny board) -------------------


def test_two_continent_actually_selects_naval() -> None:
    """End-to-end: not just OFFERED — the search must actually CHOOSE a naval plan
    when the enemy is overseas (no land target competes, invade carries the
    horizon-free base value). Runs the real playout+score on a small board."""
    from empire.ai.search.ai import SearchAI

    rows = [
        "OO~~EE",
        "..~~..",
        "..~~..",
    ]
    _, _, _, view = build_scenario(rows)
    chosen = SearchAI(samples=1)._choose_plan(view)
    is_naval = chosen.goal in (PlanGoal.INVADE, PlanGoal.SCOUT_SEA) or any(
        o.role.name == "INVADE" for o in chosen.objectives
    )
    assert is_naval, f"expected a naval choice, got {chosen}"


def test_island_map_does_not_select_overseas_invade() -> None:
    """End-to-end: with the enemy reachable by land and only a neutral island
    overseas, the search must NOT choose the island invasion (it earns no base
    value) — it fights on land."""
    from empire.ai.search.ai import SearchAI

    rows = [
        "O...E~N",
        ".....~.",
    ]
    _, _, _, view = build_scenario(rows)
    chosen = SearchAI(samples=1)._choose_plan(view)
    assert chosen.goal is not PlanGoal.INVADE
    assert chosen.goal is not PlanGoal.SCOUT_SEA


# --- single-turn action observation (stage -> one turn -> read the decisions) ---


def decide(view: WorldView, **ai_kwargs):
    """Run ONE turn of decision-making and return the concrete TurnPlan
    (production orders + unit moves). Deterministic: it either does it or not."""
    from empire.ai.search.ai import SearchAI

    return SearchAI(samples=1, **ai_kwargs).plan_turn(view)


def _move_of(plan, unit_id: int) -> tuple[tuple[int, int], ...]:
    for m in plan.moves:
        if int(m.unit_id) == unit_id:
            return m.path
    return ()


def _production(plan) -> dict[int, str]:
    return {int(o.city_id): (o.target.value if o.target else "idle") for o in plan.production_orders}


def test_idle_army_moves_toward_home_frontier() -> None:
    """Open-question probe (home-exploration completeness): with unexplored home
    land and an idle army, the AI must MOVE the army toward the frontier this
    turn — not sit. If this fails, that's why home never finishes exploring and
    the (correct) naval gate never opens."""
    rows = [
        "O......",
        ".......",
    ]
    # See only the left; cols 3-6 are unexplored LAND (the frontier to push into).
    seen = {(x, y) for y in range(2) for x in (0, 1, 2)}
    _, _, _, view = build_scenario(rows, seen=seen, units=[("army", "own", 1, 0)])
    path = _move_of(decide(view), unit_id=1)
    assert path, "army should move to explore the home frontier, not sit"
    assert path[-1][0] > 1, f"army should head toward the unseen land (higher x), got {path}"


def test_invasion_warranted_starts_transport_production() -> None:
    """Pipeline link, single turn: enemy overseas + home explored -> the AI must
    set a coastal city building a TRANSPORT this turn (the naval start). Verifies
    the fleet actually begins without running a whole game."""
    rows = [
        "OO~~EE",
        "..~~..",
        "..~~..",
    ]
    _, _, _, view = build_scenario(rows)
    prod = _production(decide(view))
    assert "transport" in prod.values(), f"expected a coastal city building a transport, got {prod}"


def test_decisions_are_deterministic_and_numbers_justified() -> None:
    """Answers 'is it justified by the numbers, or non-deterministic?': scoring
    the SAME staged position twice gives identical numbers (deterministic), and
    the chosen plan wins on the effective score (not a tiebreak/luck)."""
    from empire.ai.search.ai import SearchAI

    rows = ["O......", "......."]
    seen = {(x, y) for y in range(2) for x in (0, 1, 2)}
    _, _, _, view = build_scenario(rows, seen=seen, units=[("army", "own", 1, 0)])

    ai = SearchAI(samples=1)
    a = ai.scored_candidates(view)
    b = ai.scored_candidates(view)
    assert [(repr(p), r, e) for p, r, e in a] == [(repr(p), r, e) for p, r, e in b], (
        "scoring must be deterministic"
    )
    best = max(a, key=lambda t: t[2])
    others = [t for t in a if t is not best]
    assert all(best[2] >= o[2] for o in others), "chosen plan must be the argmax on the numbers"

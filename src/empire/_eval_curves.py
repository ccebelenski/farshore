"""Evaluator response-curve simulator.

Feeds the SearchAI `Evaluator` a sequence of controlled positions — built by
mutating one real two-continent game — and measures how the static score
responds. Each curve isolates one expectation about how value SHOULD move:

  A. Home exploration self-flattens   (marginal value -> 0 as fog -> 0)
  B. Discovering an overseas landmass  (should spike; does it?)
  C. Approaching a discovered target   (should ramp; does it?)
  D. Capturing it                      (the payoff — for contrast)

Not a game; a probe of the value function. Run: python _eval_curves.py
"""
from __future__ import annotations

from empire._naval_arena import NAVAL_PROFILE, build_two_continent, _home_continent
from empire.ai.search.evaluator import Evaluator, EvalWeights
from empire.core.coord import Coord
from empire.core.tile import TerrainKind
from empire.core.unit import UNIT_REGISTRY, UnitKind

# OLD = the pre-§10.2 surface (perimeter penalty, no opportunity/exploration).
# NEW = the real Evaluator defaults (opportunity + seen-area, intel disabled).
OLD_W = EvalWeights(
    intel=0.15, opportunity=0.0, explore_land=0.0, explore_sea=0.0
)
W = EvalWeights()
EV = Evaluator(OLD_W)
PROTO = Evaluator()  # real new-default evaluator


def fresh():
    game, players = build_two_continent(NAVAL_PROFILE, seed=0)
    return game, players[0], players[1]


def reveal(view, cells, real_map):
    """Add `cells` to the player's seen (visible) set, cumulatively."""
    view.visible = set(view.visible) | {c for c in cells if real_map.in_bounds(c)}


CUR_EV = EV  # set by __main__ to sweep baseline vs prototype


def score(game, me, ev=None):
    return (ev or CUR_EV).evaluate(game, me.id)


def frontier(game, me):
    return Evaluator._frontier_penalty(game, me.id)


def add_army(game, me, c):
    u = UNIT_REGISTRY[UnitKind.ARMY](game.allocate_unit_id(), me, c)
    game.map.place_unit(u, c)
    return u


def bar(v, lo, hi, width=40):
    if hi <= lo:
        return ""
    n = int(round((v - lo) / (hi - lo) * width))
    return "#" * max(0, min(width, n))


def curve(title, rows, valcol):
    print(f"\n=== {title} ===")
    vals = [r[valcol] for r in rows]
    lo, hi = min(vals), max(vals)
    for r in rows:
        label, v = r[0], r[valcol]
        print(f"  {label:<28} {v:>10.2f} | {bar(v, lo, hi)}")
    print(f"  range: {lo:.2f} .. {hi:.2f}  (span {hi-lo:.2f})")


# ---------------------------------------------------------------------------
# Curve A: home exploration — reveal the home continent in chunks.
# ---------------------------------------------------------------------------
def curve_a():
    game, me, _ = fresh()
    home = sorted(_home_continent(game, me), key=lambda t: (t[1], t[0]))
    rm = game.map
    me.view.visible = set()  # start blind
    rows = []
    prev = None
    chunks = 8
    for i in range(chunks + 1):
        k = int(len(home) * i / chunks)
        cells = [Coord(x, y) for (x, y) in home[:k]]
        # also reveal a 1-ring so coastal frontier closes like real scans
        ring = [n for c in cells for n in c.neighbors()]
        me.view.visible = set(cells) | {n for n in ring if rm.in_bounds(n)}
        s, f = score(game, me), frontier(game, me)
        marg = "" if prev is None else f"{s - prev:+.2f}"
        rows.append((f"{int(100*i/chunks):>3}% home seen (f={f:.0f})", s, marg))
        prev = s
    curve("A. Home exploration (does marginal value flatten?)", rows, 1)
    print("   marginal score per chunk:", [r[2] for r in rows[1:]])


# ---------------------------------------------------------------------------
# Curve B: discover the overseas landmass (reveal it + its cities, in chunks).
# ---------------------------------------------------------------------------
def curve_b():
    game, me, other = fresh()
    rm = game.map
    home = _home_continent(game, me)
    enemy_home = sorted(_home_continent(game, other), key=lambda t: (t[1], t[0]))
    # Home fully explored first (so we're at the "land is exhausted" moment).
    hcells = [Coord(x, y) for (x, y) in home]
    me.view.visible = set(hcells) | {n for c in hcells for n in c.neighbors() if rm.in_bounds(n)}
    base = score(game, me)
    n_cities = sum(1 for c in rm.cities() if (c.coord.x, c.coord.y) in set(enemy_home))
    rows = [("0% overseas seen", base, "")]
    chunks = 6
    for i in range(1, chunks + 1):
        k = int(len(enemy_home) * i / chunks)
        cells = [Coord(x, y) for (x, y) in enemy_home[:k]]
        reveal(me.view, cells, rm)
        s = score(game, me)
        seen_cities = sum(1 for c in rm.cities()
                          if (c.coord.x, c.coord.y) in set(enemy_home[:k]))
        rows.append((f"{int(100*i/chunks):>3}% overseas seen ({seen_cities}c)", s, f"{s-base:+.2f}"))
    curve(f"B. Discover overseas landmass ({n_cities} cities) — should it spike?", rows, 1)
    print("   delta vs pre-discovery:", [r[2] for r in rows[1:]])


# ---------------------------------------------------------------------------
# Curve C: approach a discovered overseas city with a 3-army force.
# ---------------------------------------------------------------------------
def curve_c():
    game, me, other = fresh()
    rm = game.map
    home = _home_continent(game, me)
    enemy_home = set(_home_continent(game, other))
    target = next(c for c in rm.cities() if (c.coord.x, c.coord.y) in enemy_home)
    # Reveal home + the target's landmass (we've discovered it).
    hcells = [Coord(x, y) for (x, y) in home] + [Coord(x, y) for (x, y) in enemy_home]
    me.view.visible = set(hcells) | {n for c in hcells for n in c.neighbors() if rm.in_bounds(n)}
    base = score(game, me)
    rows = [("no force committed", base, "")]
    # March a 3-army force along the segment from our nearest home city toward
    # the target, on-map, and report the ACTUAL distance reached (no clamping
    # artifact). t=0 is at home, t=1 is on the target.
    anchor = min((c.coord for c in rm.cities()
                  if c.owner is not None and c.owner.id == me.id),
                 key=lambda c: c.chebyshev_to(target.coord))
    tx, ty = target.coord.x, target.coord.y
    for t in (0.1, 0.3, 0.5, 0.7, 0.85, 0.95, 1.0):
        x = round(anchor.x + (tx - anchor.x) * t)
        y = round(anchor.y + (ty - anchor.y) * t)
        spot = Coord(max(0, min(rm.width - 1, x)), max(0, min(rm.height - 1, y)))
        d = spot.chebyshev_to(target.coord)
        armies = [add_army(game, me, spot) for _ in range(3)]
        s = score(game, me)
        rows.append((f"force at d={d:<2} from target", s, f"{s-base:+.2f}"))
        for u in armies:
            game.map.remove_unit(u)
    curve("C. Approach a discovered target (is there a ramp, or only a step?)", rows, 1)
    print("   note: +3 armies = +30 baseline material; opportunity adds the ramp")


# ---------------------------------------------------------------------------
# Curve D: the capture payoff — flip the overseas city to us.
# ---------------------------------------------------------------------------
def curve_d():
    game, me, other = fresh()
    rm = game.map
    enemy_home = set(_home_continent(game, other))
    target = next(c for c in rm.cities() if (c.coord.x, c.coord.y) in enemy_home)
    before = score(game, me)
    owner_before = "neutral" if target.owner is None else "enemy"
    target.owner = me
    after = score(game, me)
    print("\n=== D. Capture payoff (where ALL the value lands) ===")
    print(f"  overseas city was {owner_before}")
    print(f"  score before capture: {before:>10.2f}")
    print(f"  score after  capture: {after:>10.2f}")
    print(f"  jump: {after-before:+.2f}   (city term={W.city}, +/- the zero-sum flip)")


if __name__ == "__main__":
    import sys
    this = sys.modules[__name__]
    for tag, ev in (("BASELINE", EV), ("PROTOTYPE", PROTO)):
        print("\n" + "=" * 70)
        print(f"################  {tag}  ################")
        print("=" * 70)
        this.CUR_EV = ev
        curve_a()
        curve_b()
        curve_c()
        curve_d()

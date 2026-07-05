"""Generate the stability-arc boards (lab/prompts/stability/) from specs.

Three arcs, seven epochs, one evolving campaign world. Specs are data; the
MAP rows, marker assignment, and UNITS table are derived mechanically with
sanity asserts (kind vs terrain, tile collisions), so the seven boards can't
drift out of sync with themselves the way hand-edited ones can. This is also
the first prototype of the real briefing renderer.

Canonical-history convention: each epoch's ledger assumes the textbook
response to the previous epoch (recorded in the spec), NOT the model's
actual prior output — every seed sees identical prompts, so runs stay
comparable.

Usage: uv run python lab/gen_stability_boards.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

OUT = Path("lab/prompts/stability")

BASE_TERRAIN = [
    ". . O . . . ~ ~ ~ ~ ? ? ? ?",
    ". . . . N . ~ ~ ~ ~ ? E ? ?",
    ". O . . . . ~ ~ ~ ~ . E . ?",
    ". . . . O . ~ ~ ~ ~ . . . ?",
    ". . . . . . ~ ~ ~ ~ ? ? ? ?",
    "~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ? ? ? ?",
]

# Contract v5: REINFORCE added (membership additions no longer require
# disbanding a mid-mission TF). Deliberately ABSENT: any "CONTINUE is the
# default" statement — that is doctrine maxim #1, a measured lever, not
# contract mechanics.
CONTRACT_TEXT = """\
=== ORDERS CONTRACT ===

Your orders are AMENDMENTS to the standing task forces. Output ONLY lines in
these forms — no other prose, headers, or commentary:

  TF <id>: CONTINUE | <one line>
  TF <id>: REINFORCE UNITS <ids> | <one line>
  TF <id>: RETASK <VERB> <target> | <one line>
  TF <id>: DISBAND | <one line>
  FORM TF <new id>: UNITS <ids> | <VERB> <target> | <one line>
  BUILD (x,y): <UNIT KIND> | <one line>

Every standing TF gets exactly one line; a REINFORCE or DISBAND line IS that
TF's one line. REINFORCE keeps the TF's objective and adds the listed
UNASSIGNED units to it. To change a TF's objective, RETASK it (members kept).
To rebuild membership from scratch, DISBAND (survivors return to UNASSIGNED)
and FORM anew; FORM lines are additional lines creating new TFs. UNASSIGNED
units enter play only through FORM or REINFORCE. A BUILD line is optional per
city — no BUILD line means the city keeps its current build (changing
discards accumulated work). VERB is one of:
  CAPTURE <city (x,y)> · DEFEND <city (x,y)> · SCOUT <(x,y) or compass> ·
  PATROL <(x,y) or compass> · STAGE <(x,y)>
A warship grouped with a transport escorts it — there is no ESCORT order.
"""

SEA_KINDS = {"transport", "destroyer"}


@dataclass(frozen=True, slots=True)
class U:
    uid: int
    kind: str
    x: int
    y: int
    tasking: str
    note: str = ""
    aboard: bool = False  # cargo: listed in the table, never on the map


@dataclass(frozen=True, slots=True)
class Enemy:
    marker: str
    desc: str  # e.g. "enemy army (3,4) — landed t52"
    x: int = -1
    y: int = -1  # on-map only if x >= 0 (currently visible)


@dataclass(frozen=True, slots=True)
class Epoch:
    name: str
    turn: int
    units: list[U]
    cities: list[str]
    neutral: str
    enemies: list[Enemy]
    taskings: str  # CURRENT TASKINGS body, pre-indented
    terrain_overrides: dict[tuple[int, int], str] = field(default_factory=dict)


def render(e: Epoch) -> str:
    grid = [row.split() for row in BASE_TERRAIN]
    for (x, y), cell in e.terrain_overrides.items():
        grid[y][x] = cell

    # Assign markers in table order; place on map with sanity checks.
    markers: dict[int, str] = {}
    occupied: dict[tuple[int, int], str] = {}
    for i, u in enumerate(e.units):
        m = chr(ord("a") + i)
        markers[u.uid] = m
        if u.aboard:
            continue
        cell = grid[u.y][u.x]
        assert cell != "?", f"{e.name}: #{u.uid} on fog ({u.x},{u.y})"
        if cell == "~":
            assert u.kind in SEA_KINDS, f"{e.name}: #{u.uid} {u.kind} on water"
            grid[u.y][u.x] = m
        elif cell in {"O", "N", "E"}:
            assert "in city" in u.note, f"{e.name}: #{u.uid} on city w/o note"
        else:
            key = (u.x, u.y)
            assert key not in occupied, f"{e.name}: tile clash {key}"
            occupied[key] = m
            grid[u.y][u.x] = m
    for en in e.enemies:
        if en.x >= 0:
            cell = grid[en.y][en.x]
            assert cell in {".", "~", "O", "N", "E"}, (
                f"{e.name}: enemy {en.marker} clash at ({en.x},{en.y}): {cell}"
            )
            if cell not in {"O", "N", "E"}:
                grid[en.y][en.x] = en.marker

    # Cache-native layout: static first (contract), then the semi-stable
    # ledger, then the volatile board state, with the turn cue LAST.
    lines = [CONTRACT_TEXT]
    lines += ["CURRENT TASKINGS  (standing orders; your stated reason in quotes;"]
    lines += ["  events since, as reported; members are in UNITS below)"]
    lines.append(e.taskings.rstrip())
    lines.append("")
    hi = chr(ord("a") + len(e.units) - 1)
    lines += [
        "MAP  legend: . land  ~ water  ? fog   O my city  E enemy city  N neutral city",
        f"     a-{hi} = your units, see UNITS (a unit inside a city shows as the city)",
    ]
    if any(en.x >= 0 for en in e.enemies):
        vis = " ".join(en.marker for en in e.enemies if en.x >= 0)
        lines.append(f"     {vis} = enemy units in sight NOW (see KNOWN ENEMY)")
    lines += [f" r{y}  {' '.join(row)}" for y, row in enumerate(grid)]
    lines += [
        "",
        "UNITS  (map marker · id · kind · position · tasking) — this is your",
        "ENTIRE force; you have NOTHING else",
    ]
    for u in e.units:
        pos = f"({u.x},{u.y})" + (f" {u.note}" if u.note else "")
        lines.append(
            f"  {markers[u.uid]}  #{u.uid:<3} {u.kind:<10} {pos:<28} {u.tasking}"
        )
    lines += ["MY CITIES"] + [f"  {c}" for c in e.cities]
    lines.append(f"NEUTRAL CITIES  {e.neutral}")
    lines.append("KNOWN ENEMY")
    for en in e.enemies:
        tag = f"  {en.marker}  " if en.x >= 0 else "     "
        lines.append(f"{tag}{en.desc}")
    lines += [
        "",
        f"It is TURN {e.turn}. Issue your amendment orders now — ONLY the line",
        "forms defined in the ORDERS CONTRACT above.",
    ]
    return "\n".join(lines) + "\n"


# ---- the canonical WHYs ------------------------------------------------------

WHY_TF1 = '"awaiting second transport before striking east at the enemy cities"'
WHY_TF2 = '"keep the capital garrisoned"'
WHY_TF3 = '"screen the crossing lane"'
WHY_TF4 = '"repel the enemy landing on the south coast"'
WHY_TF5 = '"strike east and take the enemy cities with full lift and escort"'

HOME_TF12 = [
    U(1, "army", 2, 0, "TF-2", "in city"),
    U(2, "army", 2, 1, "TF-2"),
]
STAGED_TF1 = [
    U(3, "army", 5, 0, "TF-1"),
    U(4, "army", 5, 1, "TF-1"),
    U(5, "army", 5, 2, "TF-1"),
    U(6, "army", 5, 3, "TF-1"),
    U(7, "army", 4, 2, "TF-1"),
    U(8, "army", 4, 4, "TF-1"),
]

EPOCHS = [
    # ---- ARC A: buildup -> trigger -> mid-crossing --------------------------
    Epoch(
        name="A1",
        turn=50,
        units=[*HOME_TF12, *STAGED_TF1,
            U(11, "army", 1, 0, "UNASSIGNED"),
            U(12, "army", 3, 0, "UNASSIGNED"),
            U(13, "army", 2, 0, "UNASSIGNED", "in city"),
            U(14, "army", 3, 3, "UNASSIGNED"),
            U(15, "army", 4, 3, "UNASSIGNED", "in city"),
            U(9, "transport", 7, 2, "TF-3", "empty"),
            U(10, "destroyer", 7, 3, "TF-3"),
        ],
        cities=[
            "(2,0) building ARMY, 5 turns left",
            "(1,2) building TRANSPORT, 4 turns left",
            "(4,3) building ARMY, 2 turns left",
        ],
        neutral="(4,1) on my continent",
        enemies=[
            Enemy("", "city (11,1); city (11,2)"),
            Enemy("", "destroyer at (8,3) seen at t49; army (11,1) seen 12 turns ago"),
        ],
        taskings=f"""\
  TF-1  formed t38 · STAGE (5,2) — {WHY_TF1}
    since: holding along column 5 since t44; no contact; no losses
  TF-2  formed t38 · DEFEND (2,0) — {WHY_TF2}
    since: garrisoned; no contact; no losses
  TF-3  formed t38 · PATROL (7,2) — {WHY_TF3}
    since: on station; sighted enemy destroyer at (8,3) at t49; no losses""",
    ),
    Epoch(
        name="A2",
        turn=54,
        units=[*HOME_TF12, *STAGED_TF1,
            U(11, "army", 1, 0, "UNASSIGNED"),
            U(12, "army", 3, 0, "UNASSIGNED"),
            U(13, "army", 2, 0, "UNASSIGNED", "in city"),
            U(15, "army", 4, 3, "UNASSIGNED", "in city"),
            U(17, "army", 3, 4, "UNASSIGNED"),
            U(9, "transport", 7, 2, "TF-3", "empty"),
            U(16, "transport", 1, 2, "UNASSIGNED", "in city, empty, NEW t54"),
            U(10, "destroyer", 7, 3, "TF-3"),
        ],
        terrain_overrides={(4, 1): "O"},
        cities=[
            "(2,0) building ARMY, 1 turn left",
            "(1,2) building TRANSPORT, 30 turns left (just delivered transport #16)",
            "(4,3) building ARMY, 3 turns left",
            "(4,1) building TRANSPORT, 27 turns left (captured t51 — #14 spent)",
        ],
        neutral="none known",
        enemies=[
            Enemy("", "city (11,1); city (11,2)"),
            Enemy("", "destroyer at (8,3) seen at t49 (5 turns ago); army (11,1) seen 16 turns ago"),
        ],
        taskings=f"""\
  TF-1  formed t38 · STAGE (5,2) — {WHY_TF1}
    since: holding along column 5; no contact; no losses
    t54: the awaited second transport (#16) has arrived at (1,2)
  TF-2  formed t38 · DEFEND (2,0) — {WHY_TF2}
    since: garrisoned; no contact; no losses
  TF-3  formed t38 · PATROL (7,2) — {WHY_TF3}
    since: on station; enemy destroyer not seen since t49; no losses""",
    ),
    Epoch(
        name="A3",
        turn=58,
        units=[*HOME_TF12,
            U(3, "army", 8, 1, "TF-5", "aboard #9", aboard=True),
            U(4, "army", 8, 1, "TF-5", "aboard #9", aboard=True),
            U(5, "army", 8, 1, "TF-5", "aboard #9", aboard=True),
            U(6, "army", 8, 2, "TF-5", "aboard #16", aboard=True),
            U(7, "army", 8, 2, "TF-5", "aboard #16", aboard=True),
            U(8, "army", 8, 2, "TF-5", "aboard #16", aboard=True),
            U(11, "army", 1, 0, "UNASSIGNED"),
            U(12, "army", 3, 0, "UNASSIGNED"),
            U(13, "army", 1, 1, "UNASSIGNED"),
            U(15, "army", 4, 3, "UNASSIGNED", "in city"),
            U(17, "army", 3, 4, "UNASSIGNED"),
            U(18, "army", 2, 0, "UNASSIGNED", "in city"),
            U(19, "army", 5, 4, "UNASSIGNED"),
            U(9, "transport", 8, 1, "TF-5", "carrying #3 #4 #5"),
            U(16, "transport", 8, 2, "TF-5", "carrying #6 #7 #8"),
            U(10, "destroyer", 8, 3, "TF-5", "escorting"),
        ],
        terrain_overrides={(4, 1): "O"},
        cities=[
            "(2,0) building ARMY, 2 turns left",
            "(1,2) building TRANSPORT, 26 turns left",
            "(4,3) building ARMY, 4 turns left",
            "(4,1) building TRANSPORT, 23 turns left (captured t51)",
        ],
        neutral="none known",
        enemies=[
            Enemy("", "city (11,1); city (11,2); army (11,1) seen 20 turns ago"),
            Enemy("X", "enemy destroyer at (9,4), sighted t57, closing from the southeast", 9, 4),
        ],
        taskings=f"""\
  TF-2  formed t38 · DEFEND (2,0) — {WHY_TF2}
    since: garrisoned; no contact; no losses
  TF-5  formed t54 · CAPTURE (11,1) — {WHY_TF5}
    since: loaded 3 armies on each transport at t55; crossing since t56;
      t57: enemy destroyer sighted at (9,4), closing; no losses""",
    ),
    # ---- ARC B: shock during buildup -> aftermath ----------------------------
    Epoch(
        name="B1",
        turn=52,
        units=[*HOME_TF12, *STAGED_TF1,
            U(11, "army", 1, 0, "UNASSIGNED"),
            U(12, "army", 3, 0, "UNASSIGNED"),
            U(13, "army", 2, 0, "UNASSIGNED", "in city"),
            U(15, "army", 4, 3, "UNASSIGNED", "in city"),
            U(17, "army", 4, 3, "UNASSIGNED", "in city, NEW t52"),
            U(9, "transport", 7, 2, "TF-3", "empty"),
            U(10, "destroyer", 7, 3, "TF-3"),
        ],
        terrain_overrides={(4, 1): "O"},
        cities=[
            "(2,0) building ARMY, 3 turns left",
            "(1,2) building TRANSPORT, 2 turns left",
            "(4,3) building ARMY, 5 turns left",
            "(4,1) building TRANSPORT, 29 turns left (captured t51 — #14 spent)",
        ],
        neutral="none known",
        enemies=[
            Enemy("", "city (11,1); city (11,2); destroyer at (8,3) seen at t49"),
            Enemy("X", "enemy army at (3,4) — LANDED t52 from a transport", 3, 4),
            Enemy("Y", "enemy army at (5,4) — LANDED t52 from a transport", 5, 4),
            Enemy("Z", "enemy transport offshore at (4,5), now empty", 4, 5),
        ],
        taskings=f"""\
  TF-1  formed t38 · STAGE (5,2) — {WHY_TF1}
    since: holding along column 5 since t44; no contact; no losses
  TF-2  formed t38 · DEFEND (2,0) — {WHY_TF2}
    since: garrisoned; no contact; no losses
  TF-3  formed t38 · PATROL (7,2) — {WHY_TF3}
    since: on station; t52: an enemy transport slipped in along the south
      coast and unloaded two armies at (3,4) and (5,4), next to city (4,3)""",
    ),
    Epoch(
        name="B2",
        turn=55,
        units=[*HOME_TF12, *STAGED_TF1,
            U(11, "army", 1, 0, "UNASSIGNED"),
            U(12, "army", 3, 0, "UNASSIGNED"),
            U(15, "army", 4, 3, "TF-4", "in city"),
            U(17, "army", 5, 4, "TF-4"),
            U(18, "army", 2, 0, "UNASSIGNED", "in city, NEW t55"),
            U(9, "transport", 7, 2, "TF-3", "empty"),
            U(16, "transport", 1, 2, "UNASSIGNED", "in city, empty, NEW t54"),
            U(10, "destroyer", 7, 3, "TF-3"),
        ],
        terrain_overrides={(4, 1): "O"},
        cities=[
            "(2,0) building ARMY, 5 turns left",
            "(1,2) building TRANSPORT, 29 turns left (just delivered transport #16)",
            "(4,3) building ARMY, 2 turns left",
            "(4,1) building TRANSPORT, 26 turns left (captured t51)",
        ],
        neutral="none known",
        enemies=[
            Enemy("", "city (11,1); city (11,2)"),
            Enemy("X", "enemy army at (3,3) — the survivor of the t52 landing", 3, 3),
            Enemy("", "enemy transport last seen withdrawing east at (6,5), t54"),
        ],
        taskings=f"""\
  TF-1  formed t38 · STAGE (5,2) — {WHY_TF1}
    since: holding along column 5; no contact; no losses
    t54: the awaited second transport (#16) has arrived at (1,2)
  TF-2  formed t38 · DEFEND (2,0) — {WHY_TF2}
    since: garrisoned; no contact; no losses
  TF-3  formed t38 · PATROL (7,2) — {WHY_TF3}
    since: on station; the enemy transport withdrew east at t54
  TF-4  formed t52 · DEFEND (4,3) — {WHY_TF4}
    since: t53: #17 destroyed the enemy army at (5,4);
      t54: enemy army attacked — #13 lost; the attacker fell back to (3,3);
      city (4,3) held throughout""",
    ),
    # ---- ARC C: beachhead -> first setback -----------------------------------
    Epoch(
        name="C1",
        turn=56,
        terrain_overrides={(10, 1): ".", (4, 1): "O"},
        units=[*HOME_TF12,
            U(3, "army", 10, 1, "TF-5"),
            U(4, "army", 10, 2, "TF-5"),
            U(5, "army", 10, 3, "TF-5"),
            U(6, "army", 11, 3, "TF-5"),
            U(7, "army", 12, 2, "TF-5"),
            U(8, "army", 12, 3, "TF-5"),
            U(11, "army", 1, 0, "UNASSIGNED"),
            U(12, "army", 3, 0, "UNASSIGNED"),
            U(13, "army", 1, 1, "UNASSIGNED"),
            U(15, "army", 4, 3, "UNASSIGNED", "in city"),
            U(17, "army", 3, 4, "UNASSIGNED"),
            U(18, "army", 2, 0, "UNASSIGNED", "in city"),
            U(9, "transport", 9, 1, "TF-5", "empty"),
            U(16, "transport", 9, 2, "TF-5", "empty"),
            U(10, "destroyer", 9, 3, "TF-5", "escorting"),
        ],
        cities=[
            "(2,0) building ARMY, 4 turns left",
            "(1,2) building TRANSPORT, 28 turns left",
            "(4,3) building ARMY, 1 turn left",
            "(4,1) building TRANSPORT, 25 turns left (captured t51)",
        ],
        neutral="none known",
        enemies=[
            Enemy("", "city (11,2)"),
            Enemy("X", "enemy army garrisoning city (11,1), seen t56", 11, 1),
        ],
        taskings=f"""\
  TF-2  formed t38 · DEFEND (2,0) — {WHY_TF2}
    since: garrisoned; no contact; no losses
  TF-5  formed t54 · CAPTURE (11,1) — {WHY_TF5}
    since: loaded t55; crossed without contact; LANDED t56 on the enemy
      shore, six armies ashore (10,1)-(12,3); transports empty offshore;
      an enemy army garrisons (11,1); no losses""",
    ),
    Epoch(
        name="C2",
        turn=58,
        terrain_overrides={(10, 1): ".", (4, 1): "O"},
        units=[*HOME_TF12,
            U(4, "army", 10, 1, "TF-5"),
            U(5, "army", 10, 2, "TF-5"),
            U(6, "army", 10, 3, "TF-5"),
            U(7, "army", 11, 3, "TF-5"),
            U(8, "army", 12, 3, "TF-5"),
            U(11, "army", 1, 0, "UNASSIGNED"),
            U(12, "army", 3, 0, "UNASSIGNED"),
            U(13, "army", 1, 1, "UNASSIGNED"),
            U(15, "army", 4, 3, "UNASSIGNED", "in city"),
            U(17, "army", 3, 4, "UNASSIGNED"),
            U(18, "army", 2, 0, "UNASSIGNED", "in city"),
            U(19, "army", 4, 3, "UNASSIGNED", "in city, NEW t57"),
            U(9, "transport", 9, 1, "TF-5", "empty"),
            U(16, "transport", 9, 2, "TF-5", "empty"),
            U(10, "destroyer", 9, 3, "TF-5", "escorting"),
        ],
        cities=[
            "(2,0) building ARMY, 2 turns left",
            "(1,2) building TRANSPORT, 26 turns left",
            "(4,3) building ARMY, 4 turns left",
            "(4,1) building TRANSPORT, 23 turns left (captured t51)",
        ],
        neutral="none known",
        enemies=[
            Enemy("", "city (11,2)"),
            Enemy("X", "enemy army garrisoning city (11,1)", 11, 1),
            Enemy("Y", "enemy destroyer at (9,4), sighted t58, near my empty transports", 9, 4),
        ],
        taskings=f"""\
  TF-2  formed t38 · DEFEND (2,0) — {WHY_TF2}
    since: garrisoned; no contact; no losses
  TF-5  formed t54 · CAPTURE (11,1) — {WHY_TF5}
    since: landed t56, six armies ashore;
      t57: capture attempted — failed, #3 lost; the enemy army holds (11,1);
      t58: enemy destroyer sighted at (9,4), near my empty transports""",
    ),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for e in EPOCHS:
        path = OUT / f"board_{e.name}.txt"
        path.write_text(render(e))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()

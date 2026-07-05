"""Amendment-mode grader: validate + compile amendment orders vs a board.

Parses the SAME board file the model read (unified UNITS table -> registry,
MAP rows -> terrain/landmasses), then checks each amendment line: coverage
(every standing TF exactly one line; REINFORCE/DISBAND count), roster and
pool legality (REINFORCE/FORM draw from UNASSIGNED plus DISBAND-released
survivors), verb/target grammar, and REINFORCE reachability (an army cannot
join a TF whose forces are at sea or on another landmass). Also derives the
battery metrics: churn (RETASK/DISBAND on standing TFs), launch-ordered
(a TF with armies + lift whose final objective is CAPTURE across water),
and defense-formed.

Usage:
    uv run python lab/grade_amendments.py lab/prompts/stability/board_B2.txt \
        lab/transcripts/stability2 B2
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAP_ROW_RE = re.compile(r"^ r(\d+)\s+(.+)$")
UNIT_ROW_RE = re.compile(
    r"^\s{2}([a-z])\s+#(\d+)\s+(\w+)\s+\((\d+),(\d+)\)(.*?)\s*(TF-\d+|UNASSIGNED)\s*$"
)
CITY_RE = re.compile(r"\((\d+),(\d+)\)")
COMPASS = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
VERBS = {"CAPTURE", "DEFEND", "SCOUT", "PATROL", "STAGE"}
LAND_GLYPHS = {".", "O", "N", "E"}

CONTINUE_RE = re.compile(r"^TF[ -]?(\w+)\s*:\s*CONTINUE\b", re.I)
REINFORCE_RE = re.compile(r"^TF[ -]?(\w+)\s*:\s*REINFORCE\s+UNITS\s+([^|·]+)", re.I)
RETASK_RE = re.compile(
    r"^TF[ -]?(\w+)\s*:\s*(?:RETASK\s+)?([A-Z]+)\s+([^|·]+?)\s*(?:[|·].*)?$"
)
DISBAND_RE = re.compile(r"^(?:TF[ -]?(\w+)\s*:\s*DISBAND|DISBAND\s+TF[ -]?(\w+))", re.I)
FORM_RE = re.compile(
    r"^FORM\s+TF[ -]?(\S+)\s*:\s*UNITS\s+([^|·]+?)\s*[|·]\s*([A-Za-z]+)\s+([^|·]+?)\s*(?:[|·].*)?$",
    re.I,
)
BUILD_RE = re.compile(r"^BUILD\s*\((\d+),(\d+)\)\s*:\s*([A-Za-z]+)", re.I)


@dataclass
class BoardState:
    terrain: dict[tuple[int, int], str]
    landmass: dict[tuple[int, int], int]
    units: dict[str, tuple[str, int, int, str]]  # id -> (kind, x, y, tasking)
    markers: dict[str, str]  # marker letter -> unit id
    my_cities: set[tuple[int, int]]
    standing: dict[str, set[str]]  # TF name -> member ids
    objectives: dict[str, str]  # TF name -> "VERB target" from the ledger
    aboard: set[str] = field(default_factory=set)  # cargo: not ashore anywhere

    @staticmethod
    def parse(text: str) -> BoardState:
        terrain: dict[tuple[int, int], str] = {}
        for line in text.splitlines():
            if m := MAP_ROW_RE.match(line):
                for x, cell in enumerate(m[2].split()):
                    terrain[(x, int(m[1]))] = cell
        units, markers = {}, {}
        standing: dict[str, set[str]] = {}
        aboard: set[str] = set()
        for line in text.splitlines():
            if m := UNIT_ROW_RE.match(line):
                uid = f"#{m[2]}"
                units[uid] = (m[3], int(m[4]), int(m[5]), m[7])
                markers[m[1]] = uid
                if "aboard" in m[6]:
                    aboard.add(uid)
                if m[7] != "UNASSIGNED":
                    standing.setdefault(m[7].removeprefix("TF-"), set()).add(uid)
        my_cities = set()
        in_cities = False
        for line in text.splitlines():
            if line.startswith("MY CITIES"):
                in_cities = True
                continue
            if in_cities:
                if not line.startswith("  "):
                    break
                if c := CITY_RE.search(line):
                    my_cities.add((int(c[1]), int(c[2])))
        objectives = dict(
            re.findall(r"TF-(\d+)\s+formed t\d+ · ([A-Z]+ \(\d+,\d+\))", text)
        )
        # The rendered map shows unit MARKERS over their tiles; restore the
        # underlying terrain by unit kind before flood-filling landmasses.
        for uid, (kind, x, y, _t) in units.items():
            if uid not in aboard and terrain.get((x, y)) not in {"O", "N", "E"}:
                terrain[(x, y)] = "~" if kind in {"transport", "destroyer"} else "."
        ids: dict[tuple[int, int], int] = {}
        nid = 0
        for start, cell in terrain.items():
            if cell in LAND_GLYPHS and start not in ids:
                stack = [start]
                ids[start] = nid
                while stack:
                    cx, cy = stack.pop()
                    for nb in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if terrain.get(nb) in LAND_GLYPHS and nb not in ids:
                            ids[nb] = ids[(cx, cy)]
                            stack.append(nb)
                nid += 1
        return BoardState(
            terrain, ids, units, markers, my_cities, standing, objectives, aboard
        )

    def resolve_ids(self, blob: str) -> list[str]:
        out = []
        for raw_tok in re.split(r"[,\s]+", blob.strip()):
            tok = raw_tok.strip("#<>()")
            if not tok:
                continue
            if tok.isdigit():
                out.append(f"#{tok}")
            elif len(tok) == 1 and tok in self.markers:
                out.append(self.markers[tok])
            elif "-" not in tok:
                out.append(f"?{tok}")
        return out


@dataclass
class Grade:
    run_id: str
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    churn: int = 0  # RETASK/DISBAND on standing TFs
    launch: bool = False
    defense_formed: bool = False

    def render(self) -> str:
        status = "OK" if not self.errors else "REJECT"
        head = (
            f"{self.run_id}: {status} · churn={self.churn} · "
            f"launch={'Y' if self.launch else 'n'} · "
            f"defense={'Y' if self.defense_formed else 'n'}"
        )
        return "\n".join(
            [head]
            + [f"    error: {e}" for e in self.errors]
            + [f"    note:  {n}" for n in self.notes]
        )


class AmendmentGrader:
    def __init__(self, board: BoardState) -> None:
        self._b = board

    def grade(self, run_id: str, answer: str) -> Grade:
        g = Grade(run_id=run_id)
        b = self._b
        seen: dict[str, int] = {}
        pool = {u for u, (_, _, _, t) in b.units.items() if t == "UNASSIGNED"}
        final_obj = dict(b.objectives)  # tf -> "VERB (x,y)"
        members = {tf: set(ms) for tf, ms in b.standing.items()}
        for raw in answer.splitlines():
            line = raw.strip().strip("*").strip()
            if not line:
                continue
            if m := DISBAND_RE.match(line):
                tf = (m[1] or m[2]).upper()
                seen[tf] = seen.get(tf, 0) + 1
                g.churn += 1
                pool |= members.pop(tf, set())
                final_obj.pop(tf, None)
            elif m := CONTINUE_RE.match(line):
                seen[m[1].upper()] = seen.get(m[1].upper(), 0) + 1
            elif m := REINFORCE_RE.match(line):
                tf = m[1].upper()
                seen[tf] = seen.get(tf, 0) + 1
                self._check_reinforce(tf, b.resolve_ids(m[2]), pool, members, g)
            elif m := FORM_RE.match(line):
                ids = b.resolve_ids(m[2])
                bad = [u for u in ids if u not in pool]
                if bad:
                    g.errors.append(f"FORM {m[1]}: not in pool: {', '.join(bad)}")
                pool -= set(ids)
                members[m[1].upper()] = set(ids)
                self._verb_target(m[3].upper(), m[4].strip(), f"FORM {m[1]}", g)
                final_obj[m[1].upper()] = f"{m[3].upper()} {m[4].strip()}"
            elif m := BUILD_RE.match(line):
                if (int(m[1]), int(m[2])) not in b.my_cities:
                    g.errors.append(f"BUILD ({m[1]},{m[2]}): not an owned city")
            elif m := RETASK_RE.match(line):
                tf = m[1].upper()
                seen[tf] = seen.get(tf, 0) + 1
                g.churn += 1
                self._verb_target(m[2].upper(), m[3].strip(), f"TF {tf}", g)
                final_obj[tf] = f"{m[2].upper()} {m[3].strip()}"
            else:
                g.errors.append(f"stray: {line[:70]!r}")
        for tf in b.standing:
            n = seen.get(tf, 0)
            if n != 1:
                g.errors.append(f"TF-{tf}: {n} lines (need exactly 1)")
        self._derive_metrics(final_obj, members, g)
        return g

    def _check_reinforce(
        self,
        tf: str,
        ids: list[str],
        pool: set[str],
        members: dict[str, set[str]],
        g: Grade,
    ) -> None:
        b = self._b
        tf_units = members.get(tf, set())
        tf_ashore = [
            mass
            for u in tf_units
            if u in b.units and b.units[u][0] == "army" and u not in b.aboard
            if (mass := b.landmass.get((b.units[u][1], b.units[u][2]))) is not None
        ]
        for u in ids:
            if u not in pool:
                g.errors.append(f"TF {tf}: REINFORCE {u} not in UNASSIGNED pool")
                continue
            pool.discard(u)
            members.setdefault(tf, set()).add(u)
            kind, x, y, _ = b.units[u]
            if kind == "army":
                mass = b.landmass.get((x, y))
                if tf_ashore and mass not in tf_ashore:
                    g.errors.append(
                        f"TF {tf}: army {u} cannot reach the TF (other landmass)"
                    )
                elif not tf_ashore and tf_units:
                    g.errors.append(f"TF {tf}: army {u} cannot join a force at sea")

    def _verb_target(self, verb: str, target: str, ctx: str, g: Grade) -> None:
        if verb not in VERBS:
            g.errors.append(f"{ctx}: unknown verb {verb!r}")
            return
        coord = CITY_RE.search(target)
        if verb in {"CAPTURE", "DEFEND"} and not coord:
            g.errors.append(f"{ctx}: {verb} target {target!r} is not a city coord")
        elif verb in {"SCOUT", "PATROL"} and not coord and target.upper() not in COMPASS:
            g.errors.append(f"{ctx}: {verb} target {target!r} not coord/compass")
        elif verb == "STAGE" and not coord:
            g.errors.append(f"{ctx}: STAGE target {target!r} is not a coordinate")

    def _derive_metrics(
        self, final_obj: dict[str, str], members: dict[str, set[str]], g: Grade
    ) -> None:
        b = self._b
        for tf, obj in final_obj.items():
            ms = members.get(tf, set())
            kinds = {b.units[u][0] for u in ms if u in b.units}
            if obj.startswith(("DEFEND", "STAGE")) and tf not in b.standing:
                g.defense_formed = True
            if obj.startswith("CAPTURE") and "army" in kinds:
                c = CITY_RE.search(obj)
                tgt = (int(c[1]), int(c[2])) if c else None
                armies = [u for u in ms if u in b.units and b.units[u][0] == "army"]
                home = b.landmass.get((b.units[armies[0]][1], b.units[armies[0]][2]))
                if tgt and b.landmass.get(tgt) is not None:
                    if b.landmass[tgt] == home or "transport" in kinds:
                        g.launch = g.launch or b.landmass[tgt] != home
                        if b.landmass[tgt] == home:
                            g.notes.append(f"TF {tf}: land assault {obj}")
                        else:
                            g.notes.append(f"TF {tf}: amphibious {obj}")
                    else:
                        g.errors.append(f"TF {tf}: {obj} across water with no lift")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    board = BoardState.parse(Path(argv[1]).read_text())
    prefix = argv[3]
    for path in sorted(Path(argv[2]).glob(f"{prefix}-*.json")):
        payload = json.loads(path.read_text())
        choice = payload["response"]["choices"][0]
        if choice["finish_reason"] != "stop":
            print(f"{path.stem}: UNDELIVERED")
            continue
        answer = choice["message"].get("content") or ""
        print(AmendmentGrader(board).grade(path.stem, answer).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

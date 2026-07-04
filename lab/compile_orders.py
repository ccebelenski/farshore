"""Compile parsed doctrine orders into game-term actions — the lab prototype
of the doctrine->executor compile step.

Reads the SAME board file the model was prompted with (terrain from the ASCII
MAP, roster from AVAILABLE UNITS), derives landmasses by flood fill, then for
each TF order in a transcript answers: what would the game do with this?
Which pipeline (land assault / amphibious assault / garrison / patrol /
recon / marshal), is it feasible (lift present, capacity, terrain class,
target ownership), and what actions fall out.

Usage:
    uv run python lab/compile_orders.py lab/prompts/doctrine/board_contract_v2.txt \
        lab/transcripts/doctrine-v2
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from grade_doctrine import BUILD_RE, COMPASS, TF_RE

TRANSPORT_CAPACITY = 6
MAP_ROW_RE = re.compile(r"^r(\d+)\s+(.+)$")
UNIT_RE = re.compile(r"(army|transport|destroyer)?\s*#(\d+)\s*\((\d+),(\d+)\)")
COORD_RE = re.compile(r"\((\d+),(\d+)\)")

LAND_KINDS = {"army"}
SEA_KINDS = {"transport", "destroyer"}


@dataclass(frozen=True, slots=True)
class Unit:
    uid: str
    kind: str
    x: int
    y: int


class Board:
    """The scenario as the model saw it: terrain grid, landmasses, units, cities."""

    def __init__(self, text: str) -> None:
        self.grid: dict[tuple[int, int], str] = {}
        for line in text.splitlines():
            if m := MAP_ROW_RE.match(line.strip()):
                for x, cell in enumerate(m[2].split()):
                    self.grid[(x, int(m[1]))] = cell
        self.units = self._parse_units(text)
        self.my_cities = self._cities_after(text, "MY CITIES")
        self.neutral_cities = self._cities_after(text, "NEUTRAL CITIES")
        self.enemy_cities = self._cities_after(text, "KNOWN ENEMY")
        self.landmass = self._flood_landmasses()

    def _parse_units(self, text: str) -> dict[str, Unit]:
        units: dict[str, Unit] = {}
        in_roster = False
        kind = ""
        for line in text.splitlines():
            if line.startswith(("AVAILABLE UNITS", "MY UNITS")):
                in_roster = True
                continue
            if in_roster and not line.startswith("  "):
                in_roster = False
            if in_roster:
                for m in UNIT_RE.finditer(line):
                    kind = m[1] or kind  # bare "#2 (1,0)" inherits the running kind
                    units[f"#{m[2]}"] = Unit(f"#{m[2]}", kind, int(m[3]), int(m[4]))
        return units

    def _cities_after(self, text: str, header: str) -> set[tuple[int, int]]:
        block = text.split(header, 1)
        if len(block) < 2:
            return set()
        # The block runs to the next ALL-CAPS header line.
        body = re.split(r"\n(?=[A-Z][A-Z ]+\b)", block[1], maxsplit=1)[0]
        return {(int(x), int(y)) for x, y in COORD_RE.findall(body)}

    def _flood_landmasses(self) -> dict[tuple[int, int], int]:
        """Connected components over land+city tiles; fog and water divide."""
        ids: dict[tuple[int, int], int] = {}
        next_id = 0
        for start, cell in self.grid.items():
            if cell in {".", "O", "E", "N"} and start not in ids:
                stack = [start]
                ids[start] = next_id
                while stack:
                    cx, cy = stack.pop()
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if self.grid.get((nx, ny)) in {".", "O", "E", "N"} and (nx, ny) not in ids:
                            ids[(nx, ny)] = ids[(cx, cy)]
                            stack.append((nx, ny))
                next_id += 1
        return ids

    def is_water(self, x: int, y: int) -> bool:
        return self.grid.get((x, y)) == "~"


class OrderCompiler:
    """Turns one parsed TF order into a game-term interpretation."""

    def __init__(self, board: Board) -> None:
        self._b = board

    def compile_tf(self, n: str, unit_ids: list[str], verb: str, target: str) -> str:
        units = [self._b.units[u] for u in unit_ids if u in self._b.units]
        unknown = [u for u in unit_ids if u not in self._b.units]
        notes = [f"unknown unit {u}" for u in unknown]
        coord = COORD_RE.match(target.strip())
        tgt = (int(coord[1]), int(coord[2])) if coord else None
        handler = {
            "CAPTURE": self._capture, "DEFEND": self._defend, "STAGE": self._stage,
            "PATROL": self._patrol, "SCOUT": self._scout,
        }.get(verb)
        if handler is None:
            return f"TF{n}: NOT COMPILABLE — unknown verb {verb!r}"
        verdict = handler(units, tgt, target.strip().upper())
        if notes:
            verdict += f"  [{'; '.join(notes)}]"
        return f"TF{n}: {verdict}"

    # -- verb handlers -------------------------------------------------------

    def _capture(self, units: list[Unit], tgt: tuple[int, int] | None, raw: str) -> str:
        if tgt is None or tgt not in (
            self._b.neutral_cities | self._b.enemy_cities | self._b.my_cities
        ):
            return f"NOT COMPILABLE — CAPTURE target {raw} is not a known city"
        if tgt in self._b.my_cities:
            return f"NOT COMPILABLE — CAPTURE {raw}: already ours"
        armies = [u for u in units if u.kind in LAND_KINDS]
        lift = [u for u in units if u.kind == "transport"]
        escorts = [u for u in units if u.kind == "destroyer"]
        if not armies:
            return f"INFEASIBLE — CAPTURE {raw}: no armies in TF (only armies capture)"
        tgt_mass = self._b.landmass.get(tgt)
        home_mass = self._b.landmass.get((armies[0].x, armies[0].y))
        kind = "neutral" if tgt in self._b.neutral_cities else "enemy"
        if tgt_mass == home_mass:
            return (
                f"LAND ASSAULT on {kind} city {raw}: march {len(armies)} armies "
                f"overland, assault (capture consumes an army per attempt). FEASIBLE"
            )
        if not lift:
            return (
                f"INFEASIBLE — AMPHIBIOUS CAPTURE {raw}: target is on another "
                f"landmass and the TF has no transport"
            )
        over = len(armies) - TRANSPORT_CAPACITY * len(lift)
        cap = f"; OVER CAPACITY by {over} armies" if over > 0 else ""
        esc = f"escorted by {', '.join(e.uid for e in escorts)}" if escorts else "UNESCORTED"
        return (
            f"AMPHIBIOUS ASSAULT on {kind} city {raw}: load {len(armies)} armies "
            f"onto {', '.join(t.uid for t in lift)} ({esc}), cross, unload adjacent, "
            f"assault. FEASIBLE{cap}"
        )

    def _defend(self, units: list[Unit], tgt: tuple[int, int] | None, raw: str) -> str:
        if tgt is None or tgt not in (self._b.my_cities | self._b.neutral_cities):
            return f"NOT COMPILABLE — DEFEND target {raw} is not an owned/neutral city"
        if tgt in self._b.neutral_cities:
            return f"QUESTIONABLE — DEFEND {raw}: city is neutral, not ours (garrison anyway?)"
        return f"GARRISON {raw}: station {len(units)} units at/around the city. FEASIBLE"

    def _stage(self, units: list[Unit], tgt: tuple[int, int] | None, raw: str) -> str:
        if tgt is None:
            return f"NOT COMPILABLE — STAGE target {raw} is not a coordinate"
        if self._b.grid.get(tgt) == "?":
            return f"INFEASIBLE — STAGE {raw}: tile is unexplored fog"
        land_units = [u for u in units if u.kind in LAND_KINDS]
        if land_units and self._b.is_water(*tgt):
            return f"INFEASIBLE — STAGE {raw}: land units cannot mass on water"
        return f"MARSHAL at {raw}: move {len(units)} units to vicinity, hold. FEASIBLE"

    def _patrol(self, units: list[Unit], tgt: tuple[int, int] | None, raw: str) -> str:
        landlubbers = [u.uid for u in units if u.kind in LAND_KINDS]
        if landlubbers:
            return f"INFEASIBLE — PATROL: land units {', '.join(landlubbers)} cannot patrol sea"
        area = raw if raw in COMPASS else (raw if tgt else None)
        if area is None:
            return f"NOT COMPILABLE — PATROL target {raw} is neither coordinate nor compass"
        if tgt and not self._b.is_water(*tgt) and self._b.grid.get(tgt) != "?":
            return f"INFEASIBLE — PATROL {raw}: not a water tile"
        return f"SEA PATROL around {area}: {len(units)} warships interdict. FEASIBLE"

    def _scout(self, units: list[Unit], tgt: tuple[int, int] | None, raw: str) -> str:
        if raw not in COMPASS and tgt is None:
            return f"NOT COMPILABLE — SCOUT target {raw} is neither coordinate nor compass"
        # Any unit can scout toward its own movement domain; flag the mismatch.
        if tgt and self._b.is_water(*tgt):
            landlubbers = [u.uid for u in units if u.kind in LAND_KINDS]
            if landlubbers and len(landlubbers) == len(units):
                return f"INFEASIBLE — SCOUT {raw}: only land units tasked toward water"
        return f"RECON toward {raw}: {len(units)} units probe and report. FEASIBLE"


def compile_transcript(board: Board, answer: str) -> list[str]:
    out: list[str] = []
    compiler = OrderCompiler(board)
    for rawline in answer.splitlines():
        line = rawline.strip().strip("*").strip()
        if m := TF_RE.match(line):
            ids = [f"#{n}" for n in re.findall(r"#?(\d+)", m["units"])]
            out.append(compiler.compile_tf(m["n"].strip(), ids, m["verb"].upper(), m["target"]))
        elif m := BUILD_RE.match(line):
            out.append(f"BUILD {m['city']}: set production to {m['kind'].upper()}")
    return out or ["(no compilable lines)"]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    board = Board(Path(argv[1]).read_text())
    for path in sorted(Path(argv[2]).glob("*.json")):
        payload = json.loads(path.read_text())
        answer = payload["response"]["choices"][0]["message"].get("content") or ""
        print(f"===== {path.stem} =====")
        for line in compile_transcript(board, answer):
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

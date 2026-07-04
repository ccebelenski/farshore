"""Mechanical grader for doctrine-contract transcripts — and the prototype of
the compile step's validator (the total parse-or-reject layer from the BYO
trust boundary in planning/08).

Usage:  uv run python lab/grade_doctrine.py lab/transcripts/doctrine
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROSTER = {f"#{n}" for n in range(1, 11)}
MY_CITIES = {"(2,0)", "(1,2)", "(4,3)"}
BOARD_CITIES = MY_CITIES | {"(4,1)", "(11,1)", "(11,2)"}
REGIONS = {"HOME CONTINENT", "CENTRAL SEA", "SOUTHERN WATER", "EASTERN CONTINENT"}
VERBS = {"CAPTURE", "DEFEND", "SCOUT", "PATROL", "STAGE"}
UNIT_KINDS = {
    "ARMY", "FIGHTER", "PATROL", "DESTROYER", "SUBMARINE",
    "TRANSPORT", "CARRIER", "BATTLESHIP", "SATELLITE",
}

TF_RE = re.compile(
    r"^TF\s*(?P<n>\d+)\s*:\s*UNITS\s+(?P<units>[^|]+)\|\s*(?P<verb>[A-Z]+)\s+"
    r"(?P<target>[^|]+)\|\s*WHY\s+(?P<why>.+)$"
)
BUILD_RE = re.compile(
    r"^BUILD\s*(?P<city>\(\d+,\d+\))\s*:\s*(?P<kind>[A-Z]+)\s*\|\s*WHY\s+(?P<why>.+)$"
)


@dataclass
class Report:
    """Validation outcome for one transcript's answer."""

    run_id: str
    tf_lines: int = 0
    build_lines: int = 0
    stray_lines: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def parsed_clean(self) -> bool:
        return not self.stray_lines and not self.errors and self.tf_lines > 0

    def render(self) -> str:
        status = "PARSE OK" if self.parsed_clean else "REJECT"
        lines = [f"{self.run_id}: {status} ({self.tf_lines} TF, {self.build_lines} BUILD)"]
        lines += [f"    stray: {s!r}" for s in self.stray_lines]
        lines += [f"    error: {e}" for e in self.errors]
        return "\n".join(lines)


class DoctrineValidator:
    """Validates one answer against the contract: grammar, roster, coverage."""

    def validate(self, run_id: str, answer: str) -> Report:
        report = Report(run_id=run_id)
        assigned: dict[str, str] = {}  # unit id -> TF line no
        built: set[str] = set()
        for raw in answer.splitlines():
            line = raw.strip().strip("*").strip()
            if not line:
                continue
            if tf := TF_RE.match(line):
                report.tf_lines += 1
                self._check_tf(tf, assigned, report)
            elif build := BUILD_RE.match(line):
                report.build_lines += 1
                self._check_build(build, built, report)
            else:
                report.stray_lines.append(line[:80])
        for unit in sorted(ROSTER - assigned.keys()):
            report.errors.append(f"unit {unit} in no TF")
        for city in sorted(MY_CITIES - built):
            report.errors.append(f"city {city} has no BUILD line")
        return report

    def _check_tf(self, m: re.Match[str], assigned: dict[str, str], r: Report) -> None:
        ids = re.findall(r"#\d+", m["units"])
        for uid in ids:
            if uid not in ROSTER:
                r.errors.append(f"TF{m['n']}: unknown unit {uid}")
            elif uid in assigned:
                r.errors.append(f"TF{m['n']}: {uid} already in TF{assigned[uid]}")
            else:
                assigned[uid] = m["n"]
        if not ids:
            r.errors.append(f"TF{m['n']}: no unit ids")
        if m["verb"] not in VERBS:
            r.errors.append(f"TF{m['n']}: unknown verb {m['verb']!r}")
        target = m["target"].strip()
        if target.upper() not in REGIONS and target not in BOARD_CITIES:
            r.errors.append(f"TF{m['n']}: unresolvable target {target!r}")

    def _check_build(self, m: re.Match[str], built: set[str], r: Report) -> None:
        if m["city"] not in MY_CITIES:
            r.errors.append(f"BUILD: {m['city']} is not an owned city")
        elif m["city"] in built:
            r.errors.append(f"BUILD: duplicate line for {m['city']}")
        else:
            built.add(m["city"])
        if m["kind"] not in UNIT_KINDS:
            r.errors.append(f"BUILD: unknown unit kind {m['kind']!r}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    validator = DoctrineValidator()
    for path in sorted(Path(argv[1]).glob("*.json")):
        payload = json.loads(path.read_text())
        answer = payload["response"]["choices"][0]["message"].get("content") or ""
        print(validator.validate(path.stem, answer).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""The canonical order validator (contract v7): raw general text in, `Doctrine` out.

This is the parsing half of the compile step — the trust boundary between the
general's free text and the engine — replacing the three overlapping lab
grammars (grade_doctrine, grade_amendments, compile_orders). Policy distilled
from those graders and the transcript corpus: lenient on trivia, strict on
semantics. Every formatting drift observed in the lab is accepted and
normalized (with a note); every registry violation is refused loudly
(`Refusal`); and order-like text riding inside a why is flagged — reasons are
not orders. Board feasibility (reachability, lift, terrain) belongs to the
compiler, not here: the validator checks orders against the REGISTRY, never
against the map.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from empire.contracts.doctrine import (
    Amendment,
    BuildDirective,
    Compass,
    ContinueOrder,
    DisbandOrder,
    Doctrine,
    FormOrder,
    Objective,
    Refusal,
    ReinforceOrder,
    RetaskOrder,
    TaskForceId,
    Verb,
)
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.unit import UnitKind


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """The registry-side facts one epoch's orders are checked against.

    `task_forces` maps each standing task-force id to its member unit ids;
    `unassigned` is the pool FORM/REINFORCE may draw from; `markers` maps the
    briefing's map letters (lowercase) to unit ids so marker leakage can be
    normalized back to canonical ids. All members are treated as immutable.
    """

    turn: int
    board_width: int
    board_height: int
    task_forces: Mapping[TaskForceId, frozenset[UnitId]]
    unassigned: frozenset[UnitId]
    markers: Mapping[str, UnitId]
    owned_cities: frozenset[Coord]

    def owner_of(self, unit_id: UnitId) -> TaskForceId | None:
        """The standing task force holding `unit_id`, if any."""
        for tf_id, members in self.task_forces.items():
            if unit_id in members:
                return tf_id
        return None

    def knows_unit(self, unit_id: UnitId) -> bool:
        return unit_id in self.unassigned or self.owner_of(unit_id) is not None

    def on_board(self, coord: Coord) -> bool:
        return 0 <= coord.x < self.board_width and 0 <= coord.y < self.board_height


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """One epoch's parse outcome.

    `doctrine` carries the accepted amendments (possibly empty); `refusals`
    are rejected orders with reasons, replayed into the next briefing's
    ledger; `warnings` are Refusal-shaped flags that do not reject the
    carrying order (the silent-drift detector reports here); `notes` record
    every normalization applied, so drift stays observable.
    """

    doctrine: Doctrine
    refusals: tuple[Refusal, ...]
    warnings: tuple[Refusal, ...]
    notes: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.refusals


# --- grammar -----------------------------------------------------------------
# Line forms (contract v7); lenient on trivia: case-insensitive, ':' optional,
# freeform TF labels, '·' for '|', WHY keyword optional, ids with/without '#'.
_TF_HEAD = r"TF[-\s#]*(?P<tf>[A-Za-z0-9][A-Za-z0-9-]*)\s*(?P<colon>:)?\s*"
_WHY = r"\s*(?:\|(?P<why>.*))?$"

_FLIP_DISBAND_RE = re.compile(
    r"^DISBAND\s+TF[-\s#]*(?P<tf>[A-Za-z0-9][A-Za-z0-9-]*)" + _WHY, re.IGNORECASE
)
_FORM_RE = re.compile(
    r"^FORM\s+" + _TF_HEAD + r"UNITS\s+(?P<ids>[^|]+?)\s*\|\s*"
    r"(?P<verb>[A-Za-z]+)\s+(?P<target>[^|]+?)" + _WHY,
    re.IGNORECASE,
)
_BUILD_RE = re.compile(
    r"^BUILD\s*\(\s*(?P<x>\d+)\s*,\s*(?P<y>\d+)\s*\)\s*[:\-]?\s*(?P<kind>[A-Za-z]+)" + _WHY,
    re.IGNORECASE,
)
_DISBAND_RE = re.compile("^" + _TF_HEAD + r"DISBAND\b[^|]*?" + _WHY, re.IGNORECASE)
_CONTINUE_RE = re.compile("^" + _TF_HEAD + r"CONTINUE\b(?P<rest>[^|]*?)" + _WHY, re.IGNORECASE)
_REINFORCE_RE = re.compile(
    "^" + _TF_HEAD + r"REINFORCE\s+UNITS\s+(?P<ids>[^|]+?)" + _WHY, re.IGNORECASE
)
# Epoch-1 legacy form "TF n: UNITS <ids> | <VERB> <target>" — read as FORM.
_LEGACY_FORM_RE = re.compile(
    "^" + _TF_HEAD + r"UNITS\s+(?P<ids>[^|]+?)\s*\|\s*"
    r"(?P<verb>[A-Za-z]+)\s+(?P<target>[^|]+?)" + _WHY,
    re.IGNORECASE,
)
_RETASK_RE = re.compile(
    "^" + _TF_HEAD + r"(?:(?P<kw>RETASK)\s+)?(?P<verb>[A-Za-z]+)\s+(?P<target>[^|]+?)"
    r"(?:\s+ADDING\s+(?P<adding>[^|]+?))?" + _WHY,
    re.IGNORECASE,
)

_COORD_RE = re.compile(r"^\(\s*(\d+)\s*,\s*(\d+)\s*\)$")
_MARKER_RANGE_RE = re.compile(r"^([a-z])-([a-z])$", re.IGNORECASE)
_COMPASS_WORDS: dict[str, Compass] = {c.value: c for c in Compass} | {
    "NORTH": Compass.N,
    "NORTHEAST": Compass.NE,
    "EAST": Compass.E,
    "SOUTHEAST": Compass.SE,
    "SOUTH": Compass.S,
    "SOUTHWEST": Compass.SW,
    "WEST": Compass.W,
    "NORTHWEST": Compass.NW,
}
# Reasons-are-not-orders: a verb immediately followed by a coordinate or an
# ALL-CAPS compass token inside a why is order-like content (silent drift).
# Compass words are caps-only here so prose like "capture west flank" in a
# legitimate why does not false-fire; coordinates are unambiguous either way.
_DRIFT_RE = re.compile(
    r"\b(?i:CAPTURE|DEFEND|SCOUT|PATROL|STAGE)\s+"
    r"(?:\(\s*\d+\s*,\s*\d+\s*\)|(?:" + "|".join(_COMPASS_WORDS) + r")\b)"
)
_COORD_TARGET_VERBS = frozenset({Verb.CAPTURE, Verb.DEFEND, Verb.STAGE})


@dataclass(frozen=True, slots=True)
class _Record:
    """One accepted order line, kept in output order until assembly."""

    index: int
    line: str
    kind: str  # continue | reinforce | retask | disband | form | build
    tf_id: TaskForceId | None
    amendment: Amendment


class _Session:
    """Single-owner mutable state for one `validate` call.

    Lines are processed in order: DISBAND releases members into the pool,
    REINFORCE/FORM/ADDING claim from it, so "released by an earlier DISBAND
    in the same order set" falls out of sequencing. Assembly then enforces
    coverage (every standing TF exactly one amendment, a REINFORCE+RETASK
    pair folding into one) and emits the doctrine.
    """

    def __init__(self, ctx: ValidationContext) -> None:
        self._ctx = ctx
        self._pool: set[UnitId] = set(ctx.unassigned)
        self._committed: set[UnitId] = set()
        self._formed: set[TaskForceId] = set()
        self._disbanded: set[TaskForceId] = set()
        self._built: set[Coord] = set()
        self._records: list[_Record] = []
        self._tf_refused: set[TaskForceId] = set()
        self._refusals: list[Refusal] = []
        self._warnings: list[Refusal] = []
        self._notes: list[str] = []

    # --- public entry ---------------------------------------------------

    def run(self, text: str) -> ValidationResult:
        for index, raw in enumerate(text.splitlines()):
            line = raw.strip().strip("*").strip()
            if not line:
                continue
            if "·" in line:
                self._note("read '·' as '|'")
                line = line.replace("·", "|")
            self._dispatch(index, line)
        amendments = self._assemble()
        return ValidationResult(
            doctrine=Doctrine(turn=self._ctx.turn, amendments=amendments),
            refusals=tuple(self._refusals),
            warnings=tuple(self._warnings),
            notes=tuple(self._notes),
        )

    # --- line dispatch ----------------------------------------------------

    def _dispatch(self, index: int, line: str) -> None:
        if m := _FLIP_DISBAND_RE.match(line):
            self._note(f"order-flipped 'DISBAND TF {self._tf(m)}' accepted")
            self._on_disband(index, line, m)
        elif m := _FORM_RE.match(line):
            self._head_notes(m)
            self._on_form(index, line, m, self._tf(m))
        elif m := _BUILD_RE.match(line):
            self._on_build(index, line, m)
        elif m := _DISBAND_RE.match(line):
            self._head_notes(m)
            self._on_disband(index, line, m)
        elif m := _CONTINUE_RE.match(line):
            self._head_notes(m)
            self._on_continue(index, line, m)
        elif m := _REINFORCE_RE.match(line):
            self._head_notes(m)
            self._on_reinforce(index, line, m)
        elif m := _LEGACY_FORM_RE.match(line):
            self._head_notes(m)
            tf = self._tf(m)
            self._note(f"TF {tf}: epoch-1 'TF: UNITS' line read as FORM")
            self._on_form(index, line, m, tf)
        elif (m := _RETASK_RE.match(line)) and (m["kw"] or m["verb"].upper() in Verb.__members__):
            self._head_notes(m)
            self._on_retask(index, line, m)
        else:
            self._refuse(line, ["not an order line"])

    # --- handlers ---------------------------------------------------------

    def _on_continue(self, index: int, line: str, m: re.Match[str]) -> None:
        tf = self._tf(m)
        if not self._require_standing(tf, line):
            return
        if rest := m["rest"].strip():
            self._note(f"TF {tf}: CONTINUE restated objective {rest!r}; restatement ignored")
        why = self._why(m)
        self._accept(_Record(index, line, "continue", tf, ContinueOrder(tf_id=tf, why=why)))

    def _on_reinforce(self, index: int, line: str, m: re.Match[str]) -> None:
        tf = self._tf(m)
        if not self._require_standing(tf, line):
            return
        ids, issues = self._units(m["ids"])
        self._claim(ids, issues)
        if issues:
            self._refuse(line, issues, tf)
            return
        why = self._why(m)
        order = ReinforceOrder(tf_id=tf, unit_ids=ids, why=why)
        self._accept(_Record(index, line, "reinforce", tf, order))

    def _on_retask(self, index: int, line: str, m: re.Match[str]) -> None:
        tf = self._tf(m)
        if not self._require_standing(tf, line):
            return
        if not m["kw"]:
            self._note(f"TF {tf}: bare verb read as RETASK")
        issues: list[str] = []
        objective = self._objective(m["verb"], m["target"], issues)
        adding: tuple[UnitId, ...] = ()
        if m["adding"]:
            adding, id_issues = self._units(m["adding"])
            self._claim(adding, id_issues)
            issues += id_issues
        if issues or objective is None:
            self._refuse(line, issues, tf)
            return
        why = self._why(m)
        order = RetaskOrder(tf_id=tf, objective=objective, adding=adding, why=why)
        self._accept(_Record(index, line, "retask", tf, order))

    def _on_disband(self, index: int, line: str, m: re.Match[str]) -> None:
        tf = self._tf(m)
        if not self._require_standing(tf, line):
            return
        self._disbanded.add(tf)
        self._pool |= self._ctx.task_forces[tf]
        why = self._why(m)
        self._accept(_Record(index, line, "disband", tf, DisbandOrder(tf_id=tf, why=why)))

    def _on_form(self, index: int, line: str, m: re.Match[str], tf: TaskForceId) -> None:
        issues: list[str] = []
        if tf in self._formed:
            issues.append(f"TF {tf} already formed in this order set")
        elif tf in self._ctx.task_forces and tf not in self._disbanded:
            issues.append(f"TF {tf} already stands; DISBAND it before re-forming")
        ids, id_issues = self._units(m["ids"])
        self._claim(ids, id_issues)
        issues += id_issues
        objective = self._objective(m["verb"], m["target"], issues)
        if issues or objective is None:
            self._refuse(line, issues, tf if tf in self._ctx.task_forces else None)
            return
        self._formed.add(tf)
        why = self._why(m)
        order = FormOrder(tf_id=tf, unit_ids=ids, objective=objective, why=why)
        self._accept(_Record(index, line, "form", None, order))

    def _on_build(self, index: int, line: str, m: re.Match[str]) -> None:
        issues: list[str] = []
        city = Coord(int(m["x"]), int(m["y"]))
        if city not in self._ctx.owned_cities:
            issues.append(f"({city.x},{city.y}) is not an owned city")
        elif city in self._built:
            issues.append(f"duplicate BUILD for city ({city.x},{city.y})")
        kind_name = m["kind"].upper()
        if kind_name not in UnitKind.__members__:
            issues.append(f"unknown unit kind {m['kind']!r}")
        if issues:
            self._refuse(line, issues)
            return
        self._built.add(city)
        why = self._why(m)
        order = BuildDirective(city=city, kind=UnitKind[kind_name], why=why)
        self._accept(_Record(index, line, "build", None, order))

    # --- shared pieces ------------------------------------------------------

    def _tf(self, m: re.Match[str]) -> TaskForceId:
        return m["tf"].upper()

    def _head_notes(self, m: re.Match[str]) -> None:
        if m["colon"] is None:
            self._note(f"TF {self._tf(m)}: missing ':' after the label")

    def _require_standing(self, tf: TaskForceId, line: str) -> bool:
        if tf in self._ctx.task_forces and tf not in self._disbanded:
            return True
        self._refuse(line, [f"TF {tf} is not a standing task force"])
        return False

    def _units(self, blob: str) -> tuple[tuple[UnitId, ...], list[str]]:
        """Resolve an id list: '#7', '7', marker letters, marker ranges."""
        ids: list[UnitId] = []
        issues: list[str] = []
        for raw_tok in re.split(r"[\s,]+", blob.strip()):
            tok = raw_tok.strip("#<>()\"'")
            if not tok:
                continue
            for uid in self._unit_token(tok, issues):
                if uid in ids:
                    self._note(f"duplicate unit tokens collapsed: #{int(uid)}")
                else:
                    ids.append(uid)
        if not ids and not issues:
            issues.append("no unit ids")
        return tuple(ids), issues

    def _unit_token(self, tok: str, issues: list[str]) -> list[UnitId]:
        markers = self._ctx.markers
        if tok.isdigit():
            return [UnitId(int(tok))]
        if len(tok) == 1 and tok.lower() in markers:
            uid = markers[tok.lower()]
            self._note(f"marker '{tok.lower()}' -> #{int(uid)}")
            return [uid]
        if m := _MARKER_RANGE_RE.match(tok):
            lo, hi = m[1].lower(), m[2].lower()
            letters = [chr(o) for o in range(ord(lo), ord(hi) + 1)]
            if lo <= hi and all(ch in markers for ch in letters):
                self._note(f"marker range '{lo}-{hi}' expanded")
                return [markers[ch] for ch in letters]
        issues.append(f"unrecognized unit token {tok!r}")
        return []

    def _claim(self, ids: tuple[UnitId, ...], issues: list[str]) -> None:
        """Check availability and, only if the whole list is clean, consume it."""
        for uid in ids:
            if uid in self._pool:
                continue
            if uid in self._committed:
                issues.append(f"#{int(uid)} already committed in this order set")
            elif (owner := self._ctx.owner_of(uid)) is not None:
                issues.append(f"#{int(uid)} is in TF-{owner}, not UNASSIGNED")
            else:
                issues.append(f"unknown unit #{int(uid)}")
        if not issues:
            self._pool -= set(ids)
            self._committed |= set(ids)

    def _objective(self, verb_raw: str, target_raw: str, issues: list[str]) -> Objective | None:
        if verb_raw.upper() not in Verb.__members__:
            issues.append(f"unknown verb {verb_raw!r}")
            return None
        verb = Verb[verb_raw.upper()]
        target = self._target(target_raw, issues)
        if target is None:
            return None
        if verb in _COORD_TARGET_VERBS and not isinstance(target, Coord):
            issues.append(f"{verb.value} target must be a coordinate, got {target_raw.strip()!r}")
            return None
        return Objective(verb=verb, target=target)

    def _target(self, raw: str, issues: list[str]) -> Coord | Compass | None:
        cleaned = raw.strip().strip("\"'").strip()
        if "<" in cleaned or ">" in cleaned:
            self._note(f"angle brackets stripped from target {cleaned!r}")
            cleaned = cleaned.strip("<>").strip()
        if m := _COORD_RE.match(cleaned):
            coord = Coord(int(m[1]), int(m[2]))
            if not self._ctx.on_board(coord):
                issues.append(f"({coord.x},{coord.y}) is off the board")
                return None
            return coord
        word = cleaned.upper()
        if word in _COMPASS_WORDS:
            compass = _COMPASS_WORDS[word]
            if len(word) > 2:
                self._note(f"compass word '{word}' read as {compass.value}")
            return compass
        issues.append(f"unresolvable target {raw.strip()!r}")
        return None

    def _why(self, m: re.Match[str]) -> str:
        raw = (m["why"] or "").strip()
        raw = re.sub(r"^WHY\s+", "", raw, flags=re.IGNORECASE)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        return raw.strip()

    def _accept(self, record: _Record) -> None:
        self._records.append(record)
        why = record.amendment.why
        if why and (m := _DRIFT_RE.search(why)):
            self._warnings.append(
                Refusal(
                    order_text=record.line,
                    reason=f"reasons are not orders: {m.group(0)!r} rides inside a why",
                )
            )

    def _refuse(self, line: str, issues: list[str], tf: TaskForceId | None = None) -> None:
        self._refusals.append(Refusal(order_text=line, reason="; ".join(issues)))
        if tf is not None:
            self._tf_refused.add(tf)

    def _note(self, note: str) -> None:
        if note not in self._notes:
            self._notes.append(note)

    # --- assembly -----------------------------------------------------------

    def _assemble(self) -> tuple[Amendment, ...]:
        """Enforce per-TF coverage, fold REINFORCE+RETASK pairs, emit in order."""
        by_tf: dict[TaskForceId, list[_Record]] = {}
        for record in self._records:
            if record.tf_id is not None:
                by_tf.setdefault(record.tf_id, []).append(record)
        dropped: set[int] = set()
        replaced: dict[int, Amendment] = {}
        for tf in self._ctx.task_forces:
            recs = by_tf.get(tf, [])
            if not recs:
                if tf not in self._tf_refused:
                    self._refuse(f"TF {tf}", ["no amendment (every standing TF needs one line)"])
                continue
            if len(recs) == 1:
                continue
            kinds = sorted(r.kind for r in recs)
            if kinds == ["reinforce", "retask"]:
                self._fold(tf, recs, dropped, replaced)
            else:
                self._refuse(
                    f"TF {tf}",
                    [f"{len(recs)} amendment lines ({', '.join(kinds)}); "
                     "need exactly one, or a REINFORCE+RETASK pair"],
                    tf,
                )
                dropped.update(r.index for r in recs)
        return tuple(
            replaced.get(r.index, r.amendment)
            for r in self._records
            if r.index not in dropped
        )

    def _fold(
        self,
        tf: TaskForceId,
        recs: list[_Record],
        dropped: set[int],
        replaced: dict[int, Amendment],
    ) -> None:
        """The legacy two-line launch pair becomes one RETASK ... ADDING."""
        rein, ret = sorted(recs, key=lambda r: r.kind)
        assert isinstance(rein.amendment, ReinforceOrder)
        assert isinstance(ret.amendment, RetaskOrder)
        folded = RetaskOrder(
            tf_id=tf,
            objective=ret.amendment.objective,
            adding=rein.amendment.unit_ids + ret.amendment.adding,
            why=ret.amendment.why or rein.amendment.why,
        )
        first, second = sorted(recs, key=lambda r: r.index)
        replaced[first.index] = folded
        dropped.add(second.index)
        self._note(f"TF {tf}: folded separate REINFORCE + RETASK lines into one RETASK ADDING")


class DoctrineValidator:
    """Validates one epoch's raw model output against the task-force registry.

    Stateless and reusable: each `validate` call opens a fresh `_Session`
    against the supplied context. The result always carries a `Doctrine` of
    the accepted amendments — a partially-refused answer still yields its
    legal orders, with the rejects reported in `refusals`.
    """

    def validate(self, text: str, context: ValidationContext) -> ValidationResult:
        return _Session(context).run(text)

"""`BriefingRenderer`: real engine state in, `Briefing` out.

Renders the strategic picture the general reads each epoch, in the frozen
cache-native layout (planning/08-llm-general.md, wrinkle #5): the static
ORDERS CONTRACT first, then the semi-stable CURRENT TASKINGS ledger, then
the volatile board sections (MAP / UNITS / MY CITIES / NEUTRAL CITIES /
KNOWN ENEMY), with the turn cue LAST. Fog-honest by construction: every
tile and every enemy fact is drawn from the player's `WorldView` — current
vision or remembered snapshots with their age — never from real map truth.
The renderer is strictly read-only over game state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from empire.contracts.doctrine import Briefing, Compass, TaskForce, TaskForceId
from empire.contracts.world_view import KnownEnemyUnit, WorldView
from empire.core.city import City
from empire.core.coord import Coord
from empire.core.identity import UnitId
from empire.core.map import RememberedTile
from empire.core.tile import TerrainKind
from empire.core.unit import UNIT_REGISTRY, Unit

# Contract v7: RETASK carries an optional ADDING clause (the launch pair as
# ONE line), so the v5/v6 two-line REINFORCE+RETASK exception is gone.
# REINFORCE remains for pure membership additions. Deliberately ABSENT: any
# "CONTINUE is the default" statement — that is doctrine maxim #1, a
# measured lever, not contract mechanics.
ORDERS_CONTRACT_V7 = """\
=== ORDERS CONTRACT ===

Your orders are AMENDMENTS to the standing task forces. Output ONLY lines in
these forms — no other prose, headers, or commentary:

  TF <id>: CONTINUE | <one line>
  TF <id>: REINFORCE UNITS <ids> | <one line>
  TF <id>: RETASK <VERB> <target> | <one line>
  TF <id>: RETASK <VERB> <target> ADDING <ids> | <one line>
  TF <id>: DISBAND | <one line>
  FORM TF <new id>: UNITS <ids> | <VERB> <target> | <one line>
  BUILD (x,y): <UNIT KIND> | <one line>

Every standing TF gets exactly one line. A DISBAND line is that TF's only
line. REINFORCE keeps the TF's objective and adds the listed UNASSIGNED
units to it. To change a TF's objective, RETASK it (members kept) — ADDING
also commits the listed UNASSIGNED units to it in the same act. To rebuild
membership from scratch, DISBAND (survivors return to UNASSIGNED) and FORM
anew; FORM lines are additional lines creating new TFs. UNASSIGNED units
enter play only through FORM, REINFORCE, or ADDING. Officers execute the
VERB, not your reasons: a TF ordered STAGE stays put no matter what its
stated reason says. A BUILD line is optional per city — no BUILD line means
the city keeps its current build (changing discards accumulated work).
VERB is one of:
  CAPTURE <city (x,y)> · DEFEND <city (x,y)> · SCOUT <(x,y) or compass> ·
  PATROL <(x,y) or compass> · STAGE <(x,y)>
A warship grouped with a transport escorts it — there is no ESCORT order."""

# Own-unit map markers, assigned in UNITS-table order. Units past the
# alphabet all share the overflow marker on the map; the table stays exact.
_OWN_MARKERS = "abcdefghijklmnopqrstuvwxyz"
_OVERFLOW_MARKER = "+"
# Markers for enemy units in sight NOW, assigned in KNOWN ENEMY order.
# Chosen to collide with no city glyph (O/E/N) or fog/terrain char; visible
# enemies past this supply are still listed, just not drawn on the map.
_ENEMY_MARKERS = "XYZWVU"

_FOG = "?"
_TERRAIN_GLYPHS = {TerrainKind.LAND: ".", TerrainKind.WATER: "~"}
_PLAIN_CELLS = frozenset({".", "~"})


@dataclass(frozen=True, slots=True)
class _RosterRow:
    """One UNITS-table row: a unit with its assigned marker and derived
    position/tasking text. Internal to the renderer."""

    marker: str
    unit: Unit
    position: str
    tasking: str
    aboard: bool


@dataclass(frozen=True, slots=True)
class _EnemySighting:
    """One KNOWN ENEMY entry: a deduplicated sighting with its map marker
    ("" when not currently in sight, or when the marker supply ran out)."""

    marker: str
    known: KnownEnemyUnit


class BriefingRenderer:
    """Renders a `Briefing` from one player's fog-of-war view.

    Stateless and read-only: one instance can serve every epoch, and no
    call mutates the view, the registry, or anything they reference. The
    task-force registry supplies membership/objective/why; per-TF event
    lines come from the event-sourcing workstream and are replayed as
    given, one bullet per line under "since:".
    """

    def render(
        self,
        view: WorldView,
        task_forces: Mapping[TaskForceId, TaskForce],
        events: Mapping[TaskForceId, Sequence[str]],
        turn: int,
    ) -> Briefing:
        roster = self._roster(view, task_forces)
        visible, stale = self._enemy_sightings(view, turn)

        lines: list[str] = [ORDERS_CONTRACT_V7, ""]
        lines += self._taskings_lines(task_forces, events)
        lines.append("")
        lines += self._map_lines(view, roster, visible)
        lines.append("")
        lines += self._units_lines(roster)
        lines += self._my_cities_lines(view)
        lines.append(self._neutral_cities_line(view))
        lines += self._known_enemy_lines(view, visible, stale, turn)
        lines += [
            "",
            f"It is TURN {turn}. Issue your amendment orders now — ONLY the line",
            "forms defined in the ORDERS CONTRACT above.",
        ]
        markers = {
            row.marker: row.unit.id
            for row in roster
            if row.marker != _OVERFLOW_MARKER  # shared glyph, not addressable
        }
        return Briefing(turn=turn, text="\n".join(lines) + "\n", markers=markers)

    # ---- CURRENT TASKINGS ---------------------------------------------------

    def _taskings_lines(
        self,
        task_forces: Mapping[TaskForceId, TaskForce],
        events: Mapping[TaskForceId, Sequence[str]],
    ) -> list[str]:
        lines = [
            "CURRENT TASKINGS  (standing orders; your stated reason in quotes;",
            "  events since, as reported; members are in UNITS below)",
        ]
        if not task_forces:
            lines += [
                "  (none — the war has just begun; you have issued no orders yet.",
                "   Every unit is UNASSIGNED.)",
            ]
            return lines
        for tf_id in sorted(task_forces, key=self._tf_sort_key):
            tf = task_forces[tf_id]
            objective = f"{tf.objective.verb.value} {self._target_text(tf.objective.target)}"
            lines.append(f'  TF-{tf.tf_id}  formed t{tf.formed_turn} · {objective} — "{tf.why}"')
            reported = list(events.get(tf_id, ()))
            if not reported:
                lines.append("    since: (nothing reported)")
            else:
                lines.append(f"    since: {reported[0]}")
                lines += [f"      {line}" for line in reported[1:]]
        return lines

    @staticmethod
    def _tf_sort_key(tf_id: TaskForceId) -> tuple[int, int, str]:
        """Numeric ids in numeric order, then free-form labels lexically."""
        if tf_id.isdigit():
            return (0, int(tf_id), "")
        return (1, 0, tf_id)

    @staticmethod
    def _target_text(target: Coord | Compass) -> str:
        if isinstance(target, Compass):
            return target.value
        return f"({target.x},{target.y})"

    # ---- UNITS roster ---------------------------------------------------------

    def _roster(
        self,
        view: WorldView,
        task_forces: Mapping[TaskForceId, TaskForce],
    ) -> list[_RosterRow]:
        tasking_by_unit: dict[UnitId, str] = {}
        for tf_id in sorted(task_forces, key=self._tf_sort_key):
            tf = task_forces[tf_id]
            for member in tf.members:
                tasking_by_unit.setdefault(member, f"TF-{tf.tf_id}")

        rows: list[_RosterRow] = []
        for index, unit in enumerate(sorted(view.own_units, key=lambda u: int(u.id))):
            marker = _OWN_MARKERS[index] if index < len(_OWN_MARKERS) else _OVERFLOW_MARKER
            aboard = unit.carried_by is not None
            rows.append(
                _RosterRow(
                    marker=marker,
                    unit=unit,
                    position=self._position_text(view, unit, aboard),
                    tasking=tasking_by_unit.get(unit.id, "UNASSIGNED"),
                    aboard=aboard,
                )
            )
        return rows

    def _position_text(self, view: WorldView, unit: Unit, aboard: bool) -> str:
        notes: list[str] = []
        if aboard and unit.carried_by is not None:
            notes.append(f"aboard #{int(unit.carried_by)}")
        else:
            tile = view.terrain_at(unit.coord)
            if tile is not None and tile.city is not None:
                notes.append("in city")
        if unit.cargo:
            notes.append("carrying " + " ".join(f"#{int(cargo)}" for cargo in unit.cargo))
        elif type(unit).cargo_kind is not None:
            notes.append("empty")
        position = f"({unit.coord.x},{unit.coord.y})"
        if notes:
            position += " " + ", ".join(notes)
        return position

    def _units_lines(self, roster: list[_RosterRow]) -> list[str]:
        lines = [
            "UNITS  (map marker · id · kind · position · tasking) — this is your",
            "ENTIRE force; you have NOTHING else",
        ]
        if not roster:
            lines.append("  (none — you have no units)")
            return lines
        for row in roster:
            unit = row.unit
            lines.append(
                f"  {row.marker}  #{int(unit.id):<3} {unit.kind.value:<10}"
                f" {row.position:<28} {row.tasking}"
            )
        return lines

    # ---- MAP ------------------------------------------------------------------

    def _map_lines(
        self,
        view: WorldView,
        roster: list[_RosterRow],
        visible: list[_EnemySighting],
    ) -> list[str]:
        # Board DIMENSIONS are the only real-map fact used — fixed public
        # structure, not fog information. Every cell's content comes from
        # the view: live vision, remembered snapshot, or fog.
        real = view.real_map()
        remembered = view.remembered_tiles()
        grid = [
            [self._cell_glyph(view, remembered, Coord(x, y)) for x in range(real.width)]
            for y in range(real.height)
        ]
        for row in roster:
            if row.aboard:
                continue  # cargo is listed in the table, never on the map
            coord = row.unit.coord
            if grid[coord.y][coord.x] in _PLAIN_CELLS:
                grid[coord.y][coord.x] = row.marker
        for sighting in visible:
            coord = sighting.known.snapshot.coord
            if sighting.marker and grid[coord.y][coord.x] in _PLAIN_CELLS:
                grid[coord.y][coord.x] = sighting.marker

        lines = [
            "MAP  legend: . land  ~ water  ? fog"
            "   O my city  E enemy city  N neutral city",
        ]
        if roster:
            span = f"a-{roster[-1].marker}" if len(roster) <= len(_OWN_MARKERS) else "a-z +"
            lines.append(
                f"     {span} = your units, see UNITS"
                " (a unit inside a city shows as the city)"
            )
        drawn = " ".join(s.marker for s in visible if s.marker)
        if drawn:
            lines.append(f"     {drawn} = enemy units in sight NOW (see KNOWN ENEMY)")
        lines += [f" r{y}  {' '.join(cells)}" for y, cells in enumerate(grid)]
        return lines

    def _cell_glyph(
        self,
        view: WorldView,
        remembered: Mapping[Coord, RememberedTile],
        coord: Coord,
    ) -> str:
        if view.is_visible(coord):
            tile = view.terrain_at(coord)
            if tile is None:  # unreachable for a visible coord; narrows the type
                return _FOG
            if tile.city is not None:
                return self._city_glyph_live(view, tile.city)
            return _TERRAIN_GLYPHS.get(tile.terrain, _FOG)
        snapshot = remembered.get(coord)
        if snapshot is None:
            return _FOG
        if snapshot.terrain is TerrainKind.CITY:
            if snapshot.last_city_owner is None:
                return "N"
            if snapshot.last_city_owner == view.own_player.id:
                return "O"
            return "E"
        return _TERRAIN_GLYPHS.get(snapshot.terrain, _FOG)

    @staticmethod
    def _city_glyph_live(view: WorldView, city: City) -> str:
        if city.owner is None:
            return "N"
        if city.owner is view.own_player:
            return "O"
        return "E"

    # ---- cities -----------------------------------------------------------------

    def _my_cities_lines(self, view: WorldView) -> list[str]:
        lines = ["MY CITIES"]
        cities = sorted(view.own_cities, key=lambda c: (c.coord.y, c.coord.x))
        if not cities:
            lines.append("  (none — you hold no cities)")
            return lines
        for city in cities:
            lines.append(f"  ({city.coord.x},{city.coord.y}) {self._production_text(city)}")
        return lines

    @staticmethod
    def _production_text(city: City) -> str:
        building = city.production.building
        if building is None:
            return "building nothing (idle)"
        remaining = max(UNIT_REGISTRY[building].build_time - city.production.work, 1)
        plural = "" if remaining == 1 else "s"
        return f"building {building.value.upper()}, {remaining} turn{plural} left"

    def _neutral_cities_line(self, view: WorldView) -> str:
        cities = sorted(view.neutral_cities, key=lambda c: (c.coord.y, c.coord.x))
        if not cities:
            return "NEUTRAL CITIES  none known"
        listed = ", ".join(f"({c.coord.x},{c.coord.y})" for c in cities)
        return f"NEUTRAL CITIES  {listed}"

    # ---- KNOWN ENEMY ------------------------------------------------------------

    def _enemy_sightings(
        self,
        view: WorldView,
        turn: int,
    ) -> tuple[list[_EnemySighting], list[KnownEnemyUnit]]:
        """Split known enemy units into in-sight-NOW (marker-bearing) and
        stale sightings, deduplicated to each unit's freshest report."""
        freshest: dict[UnitId, KnownEnemyUnit] = {}
        for known in view.known_enemy_units:
            unit_id = known.snapshot.unit_id
            held = freshest.get(unit_id)
            if held is None or known.seen_at_turn > held.seen_at_turn:
                freshest[unit_id] = known

        in_sight = sorted(
            (k for k in freshest.values() if k.seen_at_turn >= turn),
            key=lambda k: (k.snapshot.coord.y, k.snapshot.coord.x, int(k.snapshot.unit_id)),
        )
        stale = sorted(
            (k for k in freshest.values() if k.seen_at_turn < turn),
            key=lambda k: (
                -k.seen_at_turn,
                k.snapshot.coord.y,
                k.snapshot.coord.x,
                int(k.snapshot.unit_id),
            ),
        )
        visible = [
            _EnemySighting(
                marker=_ENEMY_MARKERS[i] if i < len(_ENEMY_MARKERS) else "",
                known=known,
            )
            for i, known in enumerate(in_sight)
        ]
        return visible, stale

    def _known_enemy_lines(
        self,
        view: WorldView,
        visible: list[_EnemySighting],
        stale: list[KnownEnemyUnit],
        turn: int,
    ) -> list[str]:
        entries: list[tuple[str, str]] = []
        cities = sorted(view.known_enemy_cities, key=lambda c: (c.coord.y, c.coord.x))
        if cities:
            entries.append(("", "; ".join(f"city ({c.coord.x},{c.coord.y})" for c in cities)))
        for sighting in visible:
            snap = sighting.known.snapshot
            entries.append(
                (
                    sighting.marker,
                    f"enemy {snap.kind.value} at ({snap.coord.x},{snap.coord.y}), in sight now",
                )
            )
        for known in stale:
            snap = known.snapshot
            age = turn - known.seen_at_turn
            plural = "" if age == 1 else "s"
            when = "seen this turn" if age <= 0 else f"seen {age} turn{plural} ago"
            entries.append(
                ("", f"enemy {snap.kind.value} at ({snap.coord.x},{snap.coord.y}) {when}")
            )
        if not entries:
            entries.append(("", "none sighted — the world beyond your walls is unexplored"))

        lines = ["KNOWN ENEMY"]
        for marker, text in entries:
            prefix = f"  {marker}  " if marker else "     "
            lines.append(f"{prefix}{text}")
        return lines

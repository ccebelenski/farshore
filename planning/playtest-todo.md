# Playtest TODO / backlog

Feature requests and rough edges surfaced during human playtesting (vs the
`search` AI). Bugs found-and-fixed are in git history, not here — this is the
*not-yet-done* list.

## Features requested

### City production tracker (panel/pop-up)
A single view of all your cities and what they're building, so you don't have to
walk the map city by city. Per city, show:
- city (id / name) and **coordinates**
- what it's **producing** (or idle)
- **ETA** — the turn the unit completes (`current_turn + ceil((build_time -
  work) / work_per_turn)`; show "idle" when nothing is building)

Open question: a transient pop-up (hotkey) vs a persistent side panel. A sortable
list (by ETA, or by city) would help once there are many cities. Likely a new
modal or an extension of the status/side area in `play_screen`.

### Deliberate disband command
No way to scrap a unit on purpose today (only the automatic over-capacity and
capture-consumption disbands exist). Proposed: a hotkey (`x`) on a selected own
unit → confirm modal → remove it. Engine has no deliberate-disband hook yet.
DECISION NEEDED: a loaded transport — **block** (unload first) or **disband with
a warning** that the N aboard are lost too? (No silent cargo drowning.)

## Rough edges / pacing

### Coastal standoff is anticlimactic
Army (land) and patrol (sea) adjacent across a coast can't engage either way —
expected (no ship-vs-land combat), but it makes "found the enemy, can't touch
them" feel dead. **Warship bombardment** (the noted future mechanic — warships
shelling coastal land units, not patrols) is what would make a coastal contest
matter. See `project_stacking_vs_bombardment`.

### Long pre-war buildup on separate-continent maps
First enemy contact came ~turn 125 (separate continents → each side develops in
isolation until someone crosses the water). Inherent to the setup, but if the
solo stretch feels like dead air, consider: smaller maps / closer starts / a
scaled-down quick-play profile, or surfacing exploration progress so it feels
less empty.

### Grid coordinate ruler on the map
We show coordinates (cursor/status) but the map grid itself is unlabeled, so you
can't map a coordinate to a cell by eye. Add axis labels to `MapWidget`:
- left **gutter** with the row (y) number per line
- top **ruler** with column (x) numbers — on a 28-wide grid, likely the units
  digit every column with a full label (or tick) every 5/10
Contained to `map_widget.py` (`render_line` + `on_mount` sizing); keyboard cursor
is logical (coords, not screen pixels) so the offset doesn't touch game logic.
Needs a visual pass to settle the ruler style.

## Watch-items (need a repro)

### Shelled with "no army in range"
Took an artillery hit when no army seemed to be in range. Likely benign —
artillery targets the most dangerous *any* in-range enemy, so a **fighter** (or
other non-army unit) within range 2 of a city gets shelled too. If it recurs,
note WHICH unit was hit: a non-army unit = expected; genuinely nothing in range
= a real targeting/range bug to chase.

## AI behavior

### SearchAI doesn't attack on opportunity
When the AI has an army adjacent to an enemy unit it could profitably hit, it
often doesn't — it slides past toward its plan's objective. Hypothesis: the
PlanFollower executes plan objectives (assault target / scout / ferry) and moves
units toward them, with no OPPORTUNISTIC-ENGAGEMENT pass — so an adjacent free
hit off the current plan is ignored. (BaselineAI is greedy and takes it.) Fix
direction: add an opportunistic-attack step to the follower — before/após a
unit's planned move, if an adjacent enemy is a favorable target (odds/value),
take the attack. Verify first that it's structural (follower never engages
off-plan) vs the search declining unfavorable odds. Meatier than a UI fix.

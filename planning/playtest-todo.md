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
direction: add an opportunistic-attack step to the follower — before/after a
unit's planned move, if an adjacent enemy is a favorable target (odds/value),
take the attack. Verify first that it's structural (follower never engages
off-plan) vs the search declining unfavorable odds. Meatier than a UI fix.

### "Mass map update" on a failed load (need repro detail)
Moving an army onto a FULL transport correctly fails, but the map appeared to do
a "mass update" at that moment. State is correct (load rejected). Two benign
candidates: (1) the army actually moved several cells toward the transport first,
revealing fog en route — only the final load step failed (legit); (2) cosmetic —
every action triggers a full map re-render (for the hint line), repainting the
whole grid even on a no-op. To tell them apart next time: did map CONTENT change
(new terrain/units = case 1) or just flicker with identical content (case 2)? If
case 2 is distracting, we can skip the map repaint when steps_taken == 0.

### HEADLINE: SearchAI is a weak/passive opponent vs a human
Playtest (t193, FORTIFIED-ish brawl): "the enemy puts up nearly no resistance; I
tear through them; every army I lost was to my OWN attacks." => the AI dealt the
human ~zero damage all game. Not merely suboptimal — it barely fights. This is
the honest-fog weakness the arena flagged (search ~even with BaselineAI on
STANDARD once it can't see through fog), now obvious against a competent human
(the arena's BaselineAI opponent is too weak to expose it).
Contributing gaps (compound):
  1. No OPPORTUNISTIC ENGAGEMENT (above) — won't take adjacent free hits; almost
     never initiates combat outside a committed assault plan.
  2. PASSIVE DEFENSE — §5.4 (no garrison) means defense = massed armies near a
     city + artillery; if the AI doesn't position defenders, the human just
     walks in uncontested.
  3. Possibly belief-myopia: fog-honest playouts make the AI plan poorly /
     timidly (the same thing that dropped honest STANDARD to ~40%).
This is the CORE post-playtest effort: "make the AI actually fight." Likely
order: (a) opportunistic engagement (biggest bang — makes it deal damage at all),
(b) defensive positioning, (c) revisit honest-fog planning strength. Each needs
arena re-validation (and a stronger oracle than BaselineAI — e.g. self-play or a
scripted aggressive bot — since BaselineAI can't measure "fights a human").

### Satellite motion is opaque + spec deviation
Playtest: "I don't understand satellite motions." Satellites auto-orbit (hardcoded
EAST from launch), bounce off edges, move 1/turn, 50-turn lifetime then deorbit,
can't be steered (scan 10 = wide recon). Two gaps: (1) UX — nothing in-game
explains the auto-orbit / bounce / countdown, so the motion looks arbitrary (add
a status hint: "orbiting E, N turns left" + a help line); (2) SPEC DEVIATION —
§2.4 says a "chosen direction" but Satellite.__init__ hardcodes Direction.E with
no UI to pick. Fix options: let the player set the orbit direction (e.g. set-
heading on a satellite -> orbit_direction) to match the spec, and/or just surface
the mechanic clearly. Decide steerable-vs-fixed before building.

### (AI headline addendum) under-produced armies
Playtest also: "the enemy didn't build many armies at all." So the passivity is
partly a FORCE-ECONOMY failure — the AI didn't even build the army it needed to
resist, not just mis-positioned what it had. Part of the core "make the AI fight"
work: production/economy must field a real army.

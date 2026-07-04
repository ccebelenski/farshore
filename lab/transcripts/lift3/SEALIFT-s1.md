# SEALIFT-s1

- model (as reported): `unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL`
- seed: 1 · duration: 119s · tokens: 2231+8298 · finish: stop

## Thinking

Thinking Process:

1.  **Analyze the Request:**
    *   **Role:** Command at the strategic level. Form task forces, give objectives. Do NOT move individual units or choose targets directly (officers do).
    *   **Victory:** Enemy owns zero cities. Capture neutral cities for production. Cities build one unit at a time.
    *   **Units:** Specific stats provided (Army, Fighter, Patrol, Destroyer, Submarine, Transport, Carrier, Battleship, Satellite). Key constraint: Armies cannot enter water; Transports carry armies (max 6); Carriers carry fighters. Warships escort transports if in the same task force.
    *   **Combat:** Probabilistic. Str/HP matter. Transport has Str 0 (can be destroyed by warship).
    *   **Movement:** Land units on land/cities only. Sea units on water/cities only. One unit per tile. Regain HP if not moving.
    *   **Transport Mechanics:** Load at city/coast, sail, unload adjacent land. Officers handle loading/unloading/escorting. You order the Task Force to do it (e.g., "STAGE" or move towards objective).
    *   **Vision:** Fog of war. Scan radius varies. Remembered positions may be stale.
    *   **Current State:** Turn 50. Last orders t38. Map provided. Unit locations and assignments provided. City production states provided. Enemy info provided.
    *   **Output Format:** Strict format only. No prose, headers, or commentary. Only valid command lines.

2.  **Analyze Current Situation (Turn 50):**
    *   **Map:** 6 rows (r0-r5). Map is small (looks like a grid up to col ~11 based on enemy location).
    *   **My Cities:**
        *   (2,0) Building ARMY.
        *   (1,2) Building TRANSPORT.
        *   (4,3) Building ARMY.
    *   **Enemy:** City at (11,1), City at (11,2). Destroyer seen near (8,3). Army seen at (11,1).
    *   **Units:** Mostly Armies (a-p, excluding some IDs used in notes).
        *   TF-1: Armies #3, #4, #5, #6, #7, #8. Staged at (5,2). Objective was "awaiting second transport before striking east".
        *   TF-2: Armies #1, #2. Garrisoned (2,0).
        *   TF-3: Transport #9, Destroyer #10. Patrol (7,2). Sighted enemy destroyer at (8,3) at t49.
    *   **Unassigned:**
        *   Army #11 at (1,0).
        *   Army #12 at (3,0).
        *   Army #13 in City (2,0) - Wait, note says "k #13 army (2,0) in city UNASSIGNED". But TF-2 is garrisoned at (2,0). Usually, units in a city count towards the garrison or are part of the city's defense. The prompt says "One unit per tile (exception: cargo)". City (2,0) has Unit #13 Army and Unit #1 Army? No, map legend says `a  #1 army (2,0) in city`. Wait, let's reconcile.
        *   Map Legend vs Unit List:
            *   Map: r0, c2 is `. i O j .`. O = my city (2,0). `i` and `j` are units. `k` is #13 army at (2,0) UNASSIGNED? This contradicts TF-2 garrisoning (2,0).
            *   Let's look at Unit List: `a  #1 army (2,0) in city TF-2`. `k  #13 army (2,0) in city UNASSIGNED`.
            *   Okay, there are two armies at City (2,0). One is part of TF-2 (garrisoning), one is unassigned. This implies I have excess strength in the capital.
    *   **Task Force Status:**
        *   TF-1 is Staging East at (5,2). Waiting for transport. Needs to cross to enemy cities at col 11. Distance: 6 units of water?
        *   Map shows `~ ~` from r0 to r5 in some cols. Water seems to be columns >4 or so. Let's check the map string provided.
            *   Row 0: `. i O j . c ~ ~ ~ ~ ? ? ? ?` (c is at 5,0)
            *   Row 1: `~ ~ ~ ~ ? E ? ?` (E is at 11,2? No, map says (11,1), (11,2). Let's parse the string properly.)
            *   The map provided in the text block is small. It shows columns up to ??.
            *   However, Enemy Cities are listed as (11,1) and (11,2).
            *   My Army #1-2 at (2,0). TF-1 Armies at Col 5.
            *   Gap between Col 5 and Col 11 is significant water.
    *   **Immediate Needs:**
        *   TF-1 needs transport to cross the sea to enemy cities. They have #3,4,5,6,7,8 (6 armies). Transport capacity is 6. Perfect fit.
        *   TF-1 currently Staged at (5,2). Is there a transport? Unit #16 (Transport) was delivered to (1,2) NEW this turn (from city production). But (1,2) is far from (5,2).
        *   I have a Transport (#9/n) with TF-3. It's empty and patrolling. TF-3 can carry armies? No, Transport #n is in TF-3. TF-1 needs a transport to move 6 armies.
        *   Wait, look at Unit #n: `#9 transport (7,2) empty TF-3`.
        *   Can TF-1 use this transport? If I disband TF-1 and form a new one with the Armies + Transport, they need to go there. But Transport is at (7,2). Armies are at (5,x). Distance: 2 tiles water.
        *   Wait, TF-1 is "awaiting second transport". TF-3 has #9 transport. Is #9 available? It says empty. Yes.
        *   TF-1 needs to move East towards Enemy Cities (Col 11).
        *   Strategy: Combine TF-1 Armies with Transport #9 (TF-3) or create a new Task Force with the Armies and Transport #9.
        *   Better strategy: Consolidate the transport capability. If I keep TF-3 patrolling, it might not move quickly enough to meet them. But I need to cross water.
        *   Actually, TF-1 is "awaiting second transport". There was likely a first one earlier or planned. Now we have #9 available.
        *   Wait, the prompt says: `TF-3 ... formed t38 ... PATROL (7,2)`. It has Transport #9.
        *   TF-1 needs to strike East at Enemy Cities (Col 11).
        *   I need to move armies from (5,x) to (11,x). They must load onto a transport.
        *   Best action: Move TF-3 and TF-1 together or Form a new TF combining them?
        *   Constraint: "A warship grouped with a transport escorts it". TF-3 has Destroyer #10. Transport #9 is in TF-3.
        *   TF-1 Armies are 6 units (IDs 3,4,5,6,7,8). Capacity of Transport #n is 6.
        *   So I can load all 6 armies into Transport #9.
        *   Current location: Transport at (7,2). Armies at (5,x). They need to rendezvous.
        *   But TF-1 is "STAGE (5,2)".
        *   If I Form a new Task Force with TF-3's transport and TF-1's armies, the objective should be the enemy cities or staging point for attack.
        *   Actually, simplest command: `FORM TF <new id>`. Combine all relevant units.
        *   Units to move: Army #3-8 (TF-1), Transport #9 (TF-3). Destroyer #10 is also in TF-3.
        *   Wait, Do I want to keep TF-2 garrison? Yes (Defend (2,0)).
        *   Do I want to use TF-1's current location as staging? No, they are already staged.
        *   So, Action: Create a Task Force that carries the armies and transport to the enemy.
        *   Command: `FORM TF 4`.
        *   Units: Army #3, #4, #5, #6, #7, #8 (TF-1 members), Transport #9, Destroyer #10 (TF-3 members).
        *   Wait, can I disband a TF? Yes. `DISBAND`.
        *   Command Plan:
            1.  Keep TF-2 Defending Capital.
            2.  Keep City Production.
            3.  Create Task Force 4 with Transport #9 and all Armies from TF-1 (IDs 3-8) + Destroyer #10.
            4.  Objective: Move East to cross water. `CAPTURE` is the goal, but need to get there first. Usually, move towards target. But I can't order movement directly on individual units. I give Task Force an objective. "CAPTURE" works if adjacent? Or just `STAGE` at destination?
            5.  Rule: `TASK FORCES DO ALL OF THIS`. "If a task force contains armies and a transport and you give it an objective across water... the officers march the armies to the transport, load, sail, escort, unload...". So I can name the objective as the enemy city (e.g., `CAPTURE (11,2)`).
            6.  However, TF-3 is currently at (7,2). Armies are at Col 5. Transport is at Col 7.
            7.  If I combine them immediately into a new TF, they need to move together. The objective dictates the route.
            8.  Do I have time for transport production? City (1,2) building Transport. That's far. Transport #9 exists and is empty.
            9.  Wait, if I combine them, does TF-3 leave its patrol role? Yes. Is that okay? The Destroyer has Str 4. It can escort.
            10. So: `FORM TF 4`. Combine all Armies + Transport + Destroyer. Objective: Capture enemy cities at (11,1) or (11,2).
            11. What about Unassigned Army #11 and #12? They are on land near capital. Maybe keep them for defense in case of attack or to reinforce if needed later. Or just leave them UNASSIGNED. I can only command assigned TFs.
            12. Do I need to change production? (4,3) building Army, 2 turns left. That's good for reinforcement.
            13. What about the existing TF-1 and TF-3 lines? The prompt says "Every standing TF gets exactly one line". If I Disband and Form new ones, that works. Or I can change objectives.
            14. Best efficiency: Combine all offensive power.
            15. `TF 2`: `DEFEND (2,0)` (Keep Capital).
            16. `TF 3`: (Wait, if TF-3 has the transport and destroyer, I should use that for the invasion). But what about the Transport at (1,2)? It's being built.
            17. Wait, Unit #n is a transport. Unit #o is another transport delivered "NEW this turn". `#16 transport (1,2) in city...`.
            18. So I have two transports. #9 and #16.
            19. TF-1 needs transport capacity. Transport #9 has Str 0, can carry 6 armies. Perfect for Armies 3-8.
            20. Do I want to keep TF-3 patrolling? It saw the destroyer at (8,3). Maybe better to use Destroyer with the army loadout to escort.
            21. If I disband TF-1 and TF-3:
                *   `DISBAND TF 1`
                *   `DISBAND TF 3`
                *   `FORM TF 4`: Units #3, #4, #5, #6, #7, #8 (Armies), #9 (Transport), #10 (Destroyer). Verb: `PATROL (8,3)`? Or `STAGE (5,2)`? No, they are moving towards enemy. Let's aim for the objective. `CAPTURE (11,1)` or just move them there. Since I cannot command movement directly on tiles but only objectives... The rule says "You give each an objective".
            22. Wait, can I order `PATROL` across water? No, you order a Task Force.
            23. Best action for the invasion: `FORM TF <new id>`. Combine Armies + Transport + Destroyer. Objective: `CAPTURE (11,1)` or `STAGE (5,2)` -> `CAPTURE` is better if it implies moving to target. But usually "Strike east at enemy cities" was the previous tasking. Now I have a transport available.
            24. So I will form a new TF with the attack force and give it the goal to capture the nearest enemy city.
            25. Enemy Cities: (11,1) and (11,2).
            26. Is there any risk? Transport is at (7,2). Armies at (5,x). Distance 2 cols. Safe enough.
            27. What about TF-2? Keep it defending the capital (2,0). `DEFEND (2,0)`.
            28. Any other TFs? No.
            29. Production: Do I change it? (4,3) Army, 2 turns left. Good for defense.
            30. Wait, looking at the Unit list again.
                *   `TF-1` has Armies #3-8 (6 armies). Transport capacity is 6.
                *   `TF-3` has Transport #9 and Destroyer #10.
                *   I can simply merge these into one Task Force to maximize the escorting capability for the crossing.
            31. Wait, TF-2 is at (2,0). It's garrisoned. Keep it.
            32. Do I need to move Transport #16? No, it's in city (1,2) being built? Or "delivered". Prompt says `NEW this turn`. City (1,2) building TRANSPORT, 30 turns left... then "just delivered transport #16". So it might not be ready or is sitting there. It has HP but is likely too far for immediate use compared to using #9 which is already at sea/shoreline.
            33. Wait, look at TF-3 again. `TF-3 ... formed t38 · PATROL (7,2)`. Since t49 it sighted destroyer. No losses.
            34. If I Disband TF-1 and TF-3:
                *   `DISBAND TF 1`
                *   `DISBAND TF 3`
                *   `FORM TF 4`: Units #3, #4, #5, #6, #7, #8, #9, #10. Objective `CAPTURE (11,1)`.
                *   But wait, do I have time for transport to sail? It takes turns.
            35. Is it better to keep TF-3 patrolling and move TF-1 alone? No, Transport needs escorting against the Enemy Destroyer seen at (8,3).
            36. So `TF 4` is the best plan.

            *   Wait, I need to check if I can issue multiple command lines. "Every standing TF gets exactly one line." + "One BUILD line per city". + "Output ONLY lines...". This implies a list of commands for *all* entities (Existing TFs that persist, or new ones formed, plus production).
            *   If I Disband TF-1 and TF-3, they are no longer standing. So I don't issue a line for them after disbanding? Yes, `DISBAND` removes the line.
            *   The prompt asks to "amend your standing orders". This usually implies keeping existing ones valid if unchanged, or issuing commands to replace/disband them.
            *   If I Disband, they are gone. So I won't list a command for them anymore.
            *   So:
                *   `TF 2`: DEFEND (2,0) | Keep defending capital.
                *   `DISBAND TF 1` | Consolidate forces.
                *   `DISBAND TF 3` | Consolidate forces.
                *   `FORM TF 4`: Units #3 #4 #5 #6 #7 #8 #9 #10 | CAPTURE (11,1) | Launch amphibious assault on enemy capital at col 11 row 1.
                *   Wait, format: `TF <id>: ...` for standing TFs. `FORM` starts a new line.
            *   Actually, the prompt says "Every standing TF gets exactly one line." This implies I only list commands for TFs that exist at the end of the turn. If I Disband an old TF, I don't give it a line in this round's output (I just Disband). Then I Form a new one (which counts as a new standing TF).
            *   So structure:
                1.  `TF 2`: DEFEND (2,0) | Maintain garrison at capital to prevent enemy breakthrough.
                2.  `DISBAND TF 1` | Relocate units to strike force.
                3.  `DISBAND TF 3` | Integrate patrol assets into invasion task force.
                4.  `FORM TF 4`: Units #3 #4 #5 #6 #7 #8 #9 #10 | CAPTURE (11,1) | Conduct amphibious landing at enemy city (11,1) using escort.
            *   Wait, the `TF <id>: CONTINUE | <one line>` format is for "amending". The `DISBAND` and `FORM` are also valid lines to output in this block?
            *   Prompt: "Output ONLY lines in these forms — no other prose... TF <id>: CONTINUE... FORM..."
            *   This means I can include DISBAND and FORM commands alongside TF lines for existing units.
            *   Wait, "Every standing TF gets exactly one line." This implies the output is the set of commands. If a TF doesn't exist anymore (disbanded), I don't give it a command.
            *   Okay. So I will list:
                1.  TF 2: DEFEND...
                2.  DISBAND TF 1...
                3.  DISBAND TF 3...
                4.  FORM TF 4...
                5.  BUILD lines (if changing). Do I need to change production?
                    *   (2,0) Building ARMY. 5 turns left.
                    *   (1,2) Building TRANSPORT. 30 turns left.
                    *   (4,3) Building ARMY. 2 turns left.
                    *   Should I change anything? No immediate need. Keep production.
            *   Wait, check the map again for Fog of War. Enemy Destroyer at (8,3). If I sail through there, risk is high. But Transport #9 and Destroyer #10 are together now. The Destroyer can fight.
            *   Check if I need to move the Transport from (7,2) or just say "CAPTURE (11,1)". The rule says: "If a task force contains armies and a transport and you give it an objective across water, the officers march...". So giving the Capture objective handles the loading/sailing automatically.
            *   Wait, does Transport #9 have enough capacity? 6 Armies. TF-1 has 6 Armies (#3,4,5,6,7,8). Yes.
            *   Do I need to worry about the other Transport #16? No, it's being built/delivered but far away (1,2). Better to focus on current assets.

    *   Wait, I missed something. "Every standing TF gets exactly one line."
        If I Disband TF-3 and Form TF-4. At the end of this turn, TF-3 is NOT standing. So I don't output a line for it (only DISBAND). I DO output a line for TF-4 (FORM creates it).
        So:
        `TF 2: ...`
        `DISBAND TF 1: ...`
        `DISBAND TF 3: ...`
        `FORM TF 4: ...`

    *   Wait, is "CAPTURE" a valid VERB? Yes.
    *   Is there any better target? (11,1) is closest.
    *   Are there any units left in UNASSIGNED that I should use? #11 at (1,0), #12 at (3,0). These are on land. TF-2 is defending (2,0). Moving them to the coast might help secure a beachhead but they need transport or crossing. Maybe leave them for defense in case enemy comes back? No, priority is capture cities.
    *   I will keep UNASSIGNED units alone as `TF 1` or similar? No, I Disbanded TF-1. If I don't form a new task force for #11 and #12, they remain UNASSIGNED. "UNASSIGNED units only enter play through FORM."
    *   Wait, if I Disband TF-1, the units become UNASSIGNED. I can Form a new TF with them too? But they don't have a transport. They cannot move across water without it. So I should stick to the Armies + Transport group. What about #11 and #12? They are on land (Col 1-3). If I keep them unassigned, they can defend if enemy approaches my coast? No, map shows `~` (water) is to the right of col 4/5. My cities are at cols 1,2,4.
    *   So I don't need #11 and #12 for the current attack. They can just sit or be defended by TF-2?
    *   Wait, (2,0) has a city. If I move Armies #3-8 to sea, they are gone from land.
    *   Okay, Plan:
        1.  Keep TF-2 defending capital.
        2.  Disband TF-1 and TF-3 to consolidate assets into the attack force.
        3.  Form TF-4 with Armies (3-8) + Transport #9 + Destroyer #10. Order: CAPTURE (11,1).
        4.  Keep Production as is.

    *   One nuance: "Every standing TF gets exactly one line." This implies I should list all active Task Forces.
    *   If I Disband TF-1 and TF-3, they cease to exist. I output DISBAND for them (as commands). But do I output `TF <id>:` lines for them? No, the instruction says "Every standing TF gets exactly one line". This usually applies to the final set of standing forces. If I change a TF's objectives, I update that line. If I Disband and Form new ones, I issue the commands.
    *   So I will output:
        1.  `TF 2: DEFEND (2,0) | Maintain garrison strength to protect capital.`
        2.  `DISBAND TF 1` | Consolidate forces for cross-water assault.
        3.  `DISBAND TF 3` | Integrate patrol assets into invasion force.
        4.  `FORM TF 4`: Units #3 #4 #5 #6 #7 #8 #9 #10 | CAPTURE (11,1) | Launch amphibious assault on nearest enemy city with escort support.
    *   Wait, is there a production command needed? The prompt says "One BUILD line per city you own (keep or change its build; changing discards accumulated work)." I should check if my current builds are optimal.
        *   (2,0) Army. Capital needs defense? Yes. Keep it. No line needed? Prompt says "One BUILD line per city...". It doesn't say I *must* issue one, but to change orders on production I must. If I don't change production, do I need a line?
        *   Usually, if I don't touch production, I don't list a command for it in the same "orders" block unless I want to track it. But strict format says "Output ONLY lines in these forms". It doesn't explicitly forbid omitting BUILD lines if not changing, but often these primers expect state updates. However, usually "amend orders" implies strategic changes. Production is static unless I change the *type*. I'll check if I should update it. (2,0) needs Army. (4,3) needs Army. (1,2) needs Transport. This seems sufficient. I won't add a BUILD line unless I change it to save space/focus on movement orders. Wait, "One BUILD line per city you own (keep or change...)" implies I might need to acknowledge them? No, it says "to keep or change its build". If I don't change, maybe I don't list it? Let's assume I only list if changing is safer for the format constraint, but standard convention in these games usually requires acknowledging production status. However, the prompt says "Every standing TF gets exactly one line." It doesn't say "Every city". So I can omit BUILD lines if not changing.
        *   Actually, re-reading: "One BUILD line per city you own (keep or change its build; changing discards accumulated work)." This suggests if I don't change, I might not need a line. But to be safe and complete the "amend orders" task, I should probably stick to the TF lines.
        *   Wait, looking at the "TASK: amend your standing orders."
        *   Okay, I will focus on Task Forces.
    *   One detail: `TF <id>: CONTINUE | <one line>` vs `TF <id>: RETASK ...`. The prompt lists valid lines as: `TF <id>: CONTINUE | <one line>`, `TF <id>: RETASK ...`, `TF <id>: DISBAND | <one line>`, `FORM TF <new id>...`, `BUILD (x,y)...`.
    *   Wait, for the DISBAND and FORM lines, does the format allow just those?
        *   Prompt: `TF <id>: CONTINUE | <one line>` ... `DISBAND | <one line>` ... `FORM TF <new id>: UNITS <ids> | <VERB> <target> | <one line>`
        *   Okay, for DISBAND/FORM I use the lines exactly as specified.
        *   For TF 2 (standing), I must output a line in the format `TF <id>: ...`.
        *   Wait, if I Disband TF-1 and TF-3, they are gone. So I only output commands for TF-1 and TF-3 to disband, then a command to form TF-4. TF-2 remains.
    *   Let's check the constraint: "Every standing TF gets exactly one line." This means if I end with TF-2 and TF-4 as standing, I must output 2 lines for them (one each). The DISBAND/FORM are separate actions to transition state.
    *   So the order matters? Or is it a list of commands issued?
    *   "Output ONLY lines in these forms".
    *   Okay, I will issue the commands.

3.  **Refining Command List:**
    *   `TF 2`: Keep Defending Capital. It's at (2,0). Order: `DEFEND (2,0)`. Line: `TF 2: DEFEND (2,0) | Maintain garrison strength to prevent enemy breakthrough.`
    *   `DISBAND TF 1` | Line: `| Consolidate offensive assets for cross-water assault.`
    *   `DISBAND TF 3` | Line: `| Integrate escort and transport assets into primary strike force.`
    *   `FORM TF 4`: Units #3, #4, #5, #6, #7, #8 (TF-1 Armies), #9 (Transport), #10 (Destroyer). Verb: `CAPTURE (11,1)`. Line: `| Launch amphibious assault on enemy city (11,1) with destroyer escort.`
    *   Wait, do I need to check the format of "UNITS <ids>"? `UNITS #3 #4 #5 #6 #7 #8 #9 #10`. Yes.
    *   Do I need to list the ID numbers? The prompt example: `TF <id>: CONTINUE | <one line>`, `DISBAND | <one line>`, `FORM TF <new id>: UNITS <ids> | ...`.
    *   Okay, I will use specific IDs.

4.  **Final Check:**
    *   Did I move units? No (TF command handles intent).
    *   Did I pick targets? Yes, Enemy City (11,1) is the objective of CAPTURE. The rule says "officers march...". So naming the target counts as intent.
    *   Did I handle Transport? Yes, included in TF-4.
    *   Did I handle Production? Not changed.
    *   Did I format correctly? No headers, just lines.

    *   Wait, one more check: "One BUILD line per city you own (keep or change its build; changing discards accumulated work)." This is a constraint on what I *can* do, not necessarily that I must output it if unchanged. But usually in these "Primer" games, production updates are part of the state management. However, since my cities are building units, and I am not cancelling them, I might need to confirm they continue? No, `BUILD` line is only needed if I issue a command *about* the build. Since I'm not issuing one, it's fine.

    *   Wait, "Every standing TF gets exactly one line."
    *   If I Disband TF-1 and TF-3, they are NOT standing. So I don't give them `TF 1: ...` or `TF 3: ...`. I only give `DISBAND`.
    *   Then I Form TF-4. That counts as a new TF. Does it count as "standing" immediately? Yes. So I need to ensure I don't output a line for TF-2 if I don't command it? No, TF-2 is standing. So I must output `TF 2: ...`.
    *   So the list should be:
        1.  `TF 2: ...`
        2.  `DISBAND TF 1 ...`
        3.  `DISBAND TF 3 ...`
        4.  `FORM TF 4 ...`

    *   Wait, is there a risk of the Transport #9 being at (7,2) and Armies at (5,x) not moving together? The instruction says "If a task force contains armies and a transport... the officers march the armies to the transport...". So if I form TF-4 with them all and set the objective, they will meet and sail.
    *   One detail: `TF 2` is at (2,0). `TF 4` starts at (5,x) or wherever it moves to.
    *   Okay, looks good.

    *   Wait, check IDs again.
    *   TF-1 had Armies #3, #4, #5, #6, #7, #8.
    *   TF-3 had Transport #9, Destroyer #10.
    *   My command needs to specify these IDs in `FORM`.

    *   Wait, one more check on the prompt: "Every standing TF gets exactly one line." Does this mean I cannot issue `DISBAND` lines? Or does it mean `TF <id>: ...` is the only line for existing ones, and DISBAND/FORM are separate commands?
    *   "Output ONLY lines in these forms — no other prose... TF <id>: CONTINUE | <one line> ... DISBAND | <one line> ... FORM TF..."
    *   This implies I can output all of them. The constraint applies to the resulting standing TFs. If a TF is disbanded, it's gone. If I create one, I output the `FORM` line.
    *   Okay.

    *   Wait, is there a need to mention City Production? "One BUILD line per city you own (keep or change its build; changing discards accumulated work)." This reads as an instruction for what the BUILD line *does*, not necessarily that I must output one for every city unless I change it. But often players are expected to acknowledge production. Since I am keeping builds, maybe I should just output TFs.
    *   However, "amend your standing orders" implies full state control. I'll omit BUILD lines since they don't need changing.

    *   Wait, one constraint: "A unit moves up to its speed at full HP; damaged units move less." Not relevant for high-level command here.
    *   One constraint: "Vision / Fog". I see my units. I know enemy city locations (11,1), (11,2). I know destroyer is at (8,3).
    *   Okay, the plan stands.

5.  **Final Polish:** Ensure no commentary. Just the lines. Ensure ID formatting is correct (space between id and name in FORM?). `FORM TF <new id>: UNITS <ids> | ...`. Yes.

    Wait, I need to check if `TF 2` needs a `CONTINUE` or `DEFEND`?
    `TF 2` was `DEFEND (2,0)`. It's still defending. So `CONTINUE` is fine, but `RETASK DEFEND` is better if I want to emphasize. The prompt says "Every standing TF gets exactly one line."
    Example: `TF <id>: CONTINUE | <one line>`.
    Or `TF <id>: RETASK <VERB> <target> | <one line>`.
    Since the objective hasn't changed (still defending capital), I can use `CONTINUE` or repeat `DEFEND`. Given the "amend" instruction, repeating `DEFEND` confirms intent. But `CONTINUE` is safer if no change. However, I want to show active management. I'll use `DEFEND (2,0)`.
    Wait, looking at valid verbs: `CAPTURE`, `DEFEND`, `SCOUT`, `PATROL`, `STAGE`.
    So `TF 2: DEFEND (2,0) | ...` is a valid format under `TF <id>: RETASK <VERB> <target> | <one line>`.

    Wait, check the prompt again: "Every standing TF gets exactly one line."
    If I use `DISBAND`, does it count as a line? Yes.
    Okay.
    One more thing: The prompt says "TF <id>: CONTINUE | ...".
    It also lists `TF <id>: RETASK ...`.
    So for existing TFs, I can pick either `CONTINUE` or `RETASK`. Since I am keeping the order effectively but reassigning other TFs, `DEFEND (2,0)` is explicit.

    Let's write it out.


## Answer

TF 2: DEFEND (2,0) | Maintain garrison strength to protect capital from enemy advance.
DISBAND TF 1 | Consolidate offensive assets for cross-water assault.
DISBAND TF 3 | Integrate escort and transport assets into primary strike force.
FORM TF 4: UNITS #3 #4 #5 #6 #7 #8 #9 #10 | CAPTURE (11,1) | Launch amphibious assault on nearest enemy city with destroyer escort support.

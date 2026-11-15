# Telos mechanic coverage

See [beam acquisition](beam-acquisition.md) for P1–P3 captures, the Compatibility Mode camera,
beam targeting for Hold Still's Dive response, and the virus countdown and cleanse observations.

The practice controller has 0% evidence through the phase 3 handoff on Compatibility Mode. The original 951-3 client has observations through the second phase 4 font at 300%. Use the profile and enrage for each observation. Each row describes the signals and result needed to validate a response. Published behavior provides leads for capture. [PVME Telos mechanics](https://pvme.io/pvme-guides/basic-guides/telos-basic/#mechanics-summary) · [P5 movement](https://pvme.io/pvme-guides/rs3-full-boss-guides/telos/p5-movement-and-timing/)

| Mechanic | Detection and expected response | Timing and verification |
| --- | --- | --- |
| Telos autos, P1–P5 | Identify the attack application and style from the actor, phase, and distance. Use the corresponding deflect prayer. `28929` is a P1 melee lead with live observations. `28930` in P2 and P3 needs style attribution. | Measure the earliest and latest protection check for each style and phase. Verify the active overhead and the attributed damage or miss. |
| Tendrils | `28931` begins the bind and `28932` accompanies release in 0% captures. Spend enough available melee damage to meet the enrage-dependent release threshold. **The melee auto after the release has no animation (account owner, 2026-11-14).** At 100% it landed 2 received ticks after `28932` (1,470 and 1,076). The flick holds melee through +2. | Measure the release deadline. Verify damage, release and recovered movement. The practice controller tries Hurricane during the first 3 s when the sampled timer and GCD allow it. |
| Uppercut / charge | `28938` starts the observed sequence. Leave the projected attack area or use an eligible defensive. | Two 0% hitmarks followed the cue by about 4.09 and 4.20 s. At 100% a 2,634 typeless hitmark appeared 7 received ticks after the cue. The 3.6–4.0 s Surge window is a trial. The `--escape` trial sends Escape (displayed `U`) at +4 to +5 received ticks. Verify position outside the affected area and the damage outcome. |
| Hold Still | `28935` starts the sequence. Account for the ordinary hit, stun and heavy melee hit, then leave the landing tile. **Two melee hits follow: a smaller one, then a 4,000+ one that must be blocked tick-perfectly with Resonance, which must not be spent on the first (account owner, 2026-11-14).** At 250% they landed at +2 (about 1,100) and +4 (4,384 to 5,606) received ticks. Resonance is on the GCD. Revolution takes every free slot, and a press made after that waits for the next slot. At 500% two presses with 1,780 ms of GCD left at +2 and +3 never cast, and the +4 hits were 8,612 and 8,265. The `--resonance` trial now presses during the GCD, so that Resonance takes the slot falling in +2 to +4. At 500% the first hit was a blocked 0 at +1. **The late typeless hit is a stomp that only hurts a player standing under Telos, inside his footprint (account owner, 2026-11-14).** After a correct Anticipation the character usually walks out to attack. Staying underneath, or an Escape/Surge cut short by a wall, takes the stomp. Observed sizes: 3,233–5,401 at +5 to +9, and 9,900 (a one-shot from full HP) at +9 at 500%. Avoidance requires being outside Telos's footprint before about +5. The stomp also reached a standing player 2 tiles outside the 5×5 footprint, 4 tiles (Chebyshev) from his centre: 9,322 at 500% P1 and 5,401 at 500% P3. The controller treats 4 tiles from the centre as unsafe and Dives 6 tiles out. It logs `stomp_check` and `stomp_dodge` with distances so the true radius can be calibrated. In one 500% capture Telos moved 5 tiles at +3 and the stomp followed his new position. A Dive aimed by direction alone targeted a tile off the platform and did not move the player (account owner). The controller now checks at +4 to +7 from Telos's current tile. It Dives only to tiles known to be floor: those the player or Telos's body occupied on the current platform. It confirms the tile change, and walks there once if the Dive did not move the player. | The practice script forecasts Anticipation from the second melee auto after Uppercut, or tries Freedom within 3 s of the special cue. Confirm cast, stun immunity or break, per-hit protection and landing avoidance. Server deadlines remain unknown. |
| Green beam, P1 and P5 | Read the beam footprint, actor occupancy and attack cadence. Reposition or use the beam according to the phase. **In P1, Telos attacks faster when the green beam hits him. Standing in the beam blocks it, gives the player extra adrenaline and slows Telos's attacks (account owner, 2026-11-14).** Auto spacing in the P1 logs varies for this reason, so the auto count must not assume a fixed interval. | Verify entry, exit, and the cadence or adrenaline change. Compatibility Mode exposes the observed green scene tiles in P1. Server occupancy checks and active timing still need calibration. |
| Black beam and Magic Onslaught, P2 | Detect beam occupancy and meter changes. Pull Telos clear while mobile. Use Magic protection for the barrage and add an eligible defensive as required. | Measure the last repositioning tick and protection checks for all pulses. Verify occupancy, overhead, pulses and end of barrage. |
| Virus, P2 and P3 | Use raw VARP 6362 and walk to the nearest projected black or red stream tile outside Telos's footprint. If already on or beside the strip, allow two game ticks for the position to trigger the cleanse. | The first recurring P2 damage arrived about three game ticks after onset. Verify a countdown reset and the matching system message. P5 virus detection and the last safe tick before expiry remain uncalibrated. |
| Red beam, golem pressure, drain and shockwaves, P3 | Read beam occupancy, golem count and meter. Remove pressure and handle drain or soft-typeless pulses with the appropriate defense. | Verify per-pulse HP, prayer and adrenaline changes. `28959` is observed in P2 and P3. **The account owner identified it as a magic attack (2026-11-14).** At 250% four `28959` applications in P2 were followed one tick later by type `139` hits of 1,139, 2,210, 3,284 and 3,828 while Deflect Melee was flicked. The flick now uses Deflect Magic for this cue. The meter value has a raw candidate in VARP 6363. |
| Fonts and minions, P4 | At 3/4, 2/4 and 1/4 of P4 health (green, red, black font; account owner) go to the chargeable font, let it drain HP and stay inside for Telos's instakill. Reflect halves the soft-typeless charge and Resonance nullifies one step. Kill the red minion first (random stuns); the green one hits harder over time and the black one strips armour. | *live*, 250%: fonts are actors with base animation `28988` at fixed tiles from P4 start, 1 HP while idle. At 112,500 the green font went config 22893 → 22896 with a 7,500 charge. It drained 1,500 per step (about 1,400 typeless to the player) and became 22899 at 1 HP. Three anima-golems (13,000 HP at 250%, 10,000 at 100%) spawn beside each font. *live*, 100% encounter: fonts charged in the order Pure (green, 22893→22896→22899), Volcanic (red, 22894→22897→22900), Corrupt (black, 22895→22898→22901) at 93,750, 62,500 and 31,250 of P4's 125,000 HP. Each charge was 6,000 at about 1,200 typeless per step. Each charged font despawned 12–14 ticks after charging. Golems: Pure 22905, Corrupt 22906, Volcanic 22907. The controller went to, charged and held all three fonts and the player survived each instakill. It now clicks golems in the order Volcanic, Pure, Corrupt once the font has charged, then clicks Telos again. Golem left-click behaviour is unvalidated. |
| Weak anima bomb and So Much Power, P4 | Distinguish orb, ordinary hit and soft-typeless bomb. Use a timed defensive and cleanse the weak bomb's overload effect at a font. | Measure impact and defensive eligibility per component. Verify debuff onset and cleanse, HP and defensive consumption. |
| Rocks, P4/P5 at high enrage | Identify each hazard footprint and expiry. Move clear or use an eligible defensive. | Verify outside-footprint position at impact or confirmed mitigation. Direct rock observations remain unmapped. |
| P5 golems and final bomb | Confirm P5 actor, golem styles, beam layout, meter and font route. Prioritize protection when a golem or Telos hit overlaps a healing opportunity. Handle the charged bomb using the route appropriate to the enrage. | The archived 100% handoff described below identifies the new actor and golem configs. Per-source protection checks, bomb interception, and the result interface need verification. |
| Phase transitions and victory | Corroborate stage changes with Telos HP and actor lifetime. Move and reacquire once the destination and target are confirmed. **The player can take no action during the P1→P2 switch: prayers, abilities and other inputs all fail (account owner, 2026-11-14).** Set the overhead and HP before the switch and treat inputs as lost until it ends. | In the 100% flick attempt, Telos repeated `28963` four times about 2.4 s apart before the phase field changed to 2. Four Deflect Melee keypresses during that time left the overhead unchanged. The end of the lockout and the first accepted input tick still need measurement. For a clear, capture an explicit result or reward signal. |
| Soul Split | Correlate eligible outgoing damage, active Soul Split and attributable healing. Prioritize protection through an overlapping check window. | Measure activation acknowledgement, latest required protection and earliest safe return in game ticks. Raw HP changes alone do not identify Soul Split healing. |

## Special rotation per phase

**P4 walk validation.** In the [250% follow-up](../../../training-data/telos-20261114T193511.217029Z.jsonl),
the first font became chargeable at tick 3986. Fourteen projected clicks followed while the
player stayed outside charging range. The controller now checks the client's default Walk tile
before clicking and accepts a free tile in the font's charging ring. In the 08:37 run,
the player reached the first two fonts. Configs 22899 and 22900 confirmed the end of
their charges. The fonts disappeared at +14 and +13 received ticks with the player
still in the ring. The controller now records arrival and the observed charged-font
release separately from an actor disappearing early. Use release position and HP with
the nearby hitmarks to assess protection. [08:37 practice log](../../../training-data/telos-20261115T083752.833586Z.jsonl)

**P4 opening bomb.** Nine captures show the sequence `28930`, three `28929` melee
attacks, then `28959` and a type 144 hit 146–151 client logic ticks after `28959`.
In eight captures the follow-up type 144 pulses were about 60 logic ticks apart.
The response uses the whole opening sequence and a 2.35–2.65 s window for single-hit
defences. The separate `28942` cue keeps its 1.75–2.05 s window. Ordinary `28959`
outside this opener keeps its magic prayer response. [300% opening](../../../training-data/telos-20261115T090122.812714Z.jsonl) · [500% opening](../../../training-data/telos-20261114T181758.065826Z.jsonl)

The recognizer matched ten archived phase 4 encounters. The tenth logged a zero-damage
hitmark at +148 logic ticks followed by recurring type 144 pulses starting at +208.
Another 300% practice run opened with Uppercut after the three melee attacks. No opening
bomb defence was triggered in that run. The timing of the new response still needs a live
bomb capture. [Zero-damage opener](../../../training-data/telos-20261115T054049.902492Z.jsonl) · [Uppercut opener](../../../training-data/telos-20261115T091100.770410Z.jsonl)

At 100%, the opening cue was recognized at logic tick 803317. Resonance was pressed
2.374 s after recognition, with the GCD reporting idle. No cooldown acknowledged the
cast. The type 144 hit landed at logic tick 803465 for 3,150 and was followed by 350
damage pulses. The live run confirms the cue and impact window. The defence remains
unverified. [100% opening bomb](../../../training-data/telos-20261115T093004.670325Z.jsonl)

In the 100% follow-up, the ordinary `28942` cue arrived at logic tick 1855080.
The GCD ended at 1855166, about 1.72 s after the cue, and restarted by 1855168.
The current 1.75–2.05 s window missed that slot even though Resonance was ready.
Disruption Shield had been used about 34 s earlier. The bomb hit for 4,399 at
1855199. Use this trace to calibrate the earlier GCD boundary and check for
intervening hits before changing the press window.
[100% follow-up](../../../training-data/telos-20261115T151940.657825Z.jsonl)

**P4 font defence.** In the 300% Uppercut opener, the first font started at game tick
24893. The verified walk reached the charging ring at 24902. Resonance cast on arrival
and a melee hit consumed it at 24904. Type 144 pulses of 1,600, 1,600, 1,566 and 1,477
were spaced 90 client logic ticks apart. The player died at 24913 during the charge.
The separate native-effects probe showed the raw Resonance expiry stamp still present
after the melee hit. Use the font charge decrements and hit timestamps to validate a
pulse-specific defence.
[300% font arrival](../../../training-data/telos-20261115T091100.770410Z.jsonl)

The controller pairs a charge decrement from `rs3_snapshot_npcs` with a single type 144
hitmark from `rs3_player_hit_events_since` within four client logic ticks. It expects the
next pulse 90 logic ticks later. It presses Resonance 16–24 logic ticks before that
prediction if the `rs3_snapshot_spatial` player tile is in the charging ring, the cooldown
is clear and the GCD has at most 200 ms left with queueing off. Replay saw 26 charging
fonts and paired 80 decrements in 19 of them. Of the 61 consecutive paired intervals,
60 were within four ticks of 90. A cooldown acknowledgement confirms the cast. The paired
hit amount shows how much damage the pulse dealt.

At 100%, the corrected controller paired 1,200-point charge drops with type 144 hits
at logic ticks 819099 and 819189. The next pulse landed at 819279. The GCD had already
started at each planned press window and ended after its pulse. The player died on the
third pulse after entering the phase with 2,385 HP. The native cue and cadence are live
verified. The defensive cast remains to be verified with a free GCD slot.
[100% font pulse trace](../../../training-data/telos-20261115T093525.671562Z.jsonl)

In that trace the combat target changed to Telos at logic tick 819160 as the GCD
restarted. The player had just walked into the font. Two planned pulse windows were
then covered by consecutive GCDs. On Compatibility Mode, the auto-retaliate icon and
VARP `462` followed five consecutive staging switches. Raw `0` means enabled and raw
`1` means disabled. The `rs3_snapshot_auto_retaliate` binding samples that state.
With the icon configured, the controller disables auto-retaliate during the charge
and restores it after charging. Verify the toggle, combat target, GCD and actual
pulse damage in the same trace.

In the 100% auto-retaliate trial, native state confirmed both switches for the first
two fonts. At the first font, Telos remained untargeted during the charge. Freedom
started one GCD after a stun, and Resonance was still on cooldown from Hold Still.
At the second font, the controller predicted logic tick 866355 from the preceding
charge decrement, pressed Resonance with an idle GCD 24 logic ticks before impact,
and observed its cooldown. The type 144 hit landed at 866355 for 1 damage and the
font charge fell by 1,200. The same sequence reduced a third-font pulse at 868125 to
1 damage. The first two fonts released with the player alive in the ring. The player
received three ordinary melee hits of 478, 730 and 669 under Soul Split in the last
five ticks of the third charge and died to the final pulse. Native state confirmed
the setting was restored in the attempt cleanup. [100% defensive timing trace](../../../training-data/telos-20261115T095005.120680Z.jsonl)

**P4 golem attack timing.** In archived P4 phases, all three golem configs (`22905`,
`22906`, `22907`) used animation `28998`. With no other `28998` within four logic ticks
and no Telos action near the cue and hit, a lone type 133 mark appeared 25–35 logic
ticks later for 128 cues. Six had a single type 144 mark, one a type 139 mark and five
a type 482 mark. The other 92 cues had overlapping marks or no matching mark. This
provides a candidate melee window for a controlled prayer capture. Hitmarks lack a
source actor field. [250% golem sample](../../../training-data/telos-20261114T171804.691233Z.jsonl) · [100% golem sample](../../../training-data/telos-20261114T173454.045394Z.jsonl)

In the 100% auto-retaliate run, two `28998` applications at logic tick 868245 preceded
the 478 and 730 melee hits at 868275. A third `28998` application at 868305 preceded the
669 melee hit at 868335. The P4 trial uses `rs3_npc_action_events_since` and accepts
same-session, stamped cues from the three golem configs up to 15 logic ticks after
application. It requests Deflect Melee from first observation through +35 logic ticks.
A Telos protection window takes priority when their styles conflict. Verify overhead
and hit timing independently with `rs3_snapshot_combat` and
`rs3_player_hit_events_since`. [Third-font trace](../../../training-data/telos-20261115T095005.120680Z.jsonl)

The 100% golem-flick trial logged 38 windows across all three configs. Of the 25
type 133 hitmarks in those windows, 23 sampled Deflect Melee. One sampled Deflect
Magic while the Telos window was active. One sampled Soul Split after the next
golem cue arrived five logic ticks after a return request. The latter Deflect
Melee press went out after the hit. There were also two type 482 zero-damage
marks under Deflect Melee. [100% golem flick trace](../../../training-data/telos-20261115T100113.908512Z.jsonl)

The same run saw the red font's instance change from charged `22900` to released
`22903` while the player was in its ring. The received counter showed only 11 ticks
since charge completion, so the controller logged `font_lost`. The client clock
showed 360 logic ticks. The controller now recognizes released configs `22902`–`22904`
on the same charged actor. The third font started charging 32 tiles from the player.
All sixteen projected Walk attempts were rejected. The font still had its full
6,000-point charge when the player returned to staging. [100% font trace](../../../training-data/telos-20261115T100113.908512Z.jsonl)

When the charging ring has no usable screen projection, the controller projects
waypoints 10, 7 or 4 tiles toward the ring with lateral alternatives. It checks the
native Walk hover for a closer tile on the same plane before clicking. It measures
progress from the next player tile samples and switches to a direct ring walk as
soon as one is available. The archived 32-tile route is covered by the tests.
In the 100% waypoint trial, four verified intermediate clicks made progress toward
the third font. The player reached its ring at tick 919 after starting 29 tiles away
at 900. All three fonts released while the player was in range. [100% waypoint trace](../../../training-data/telos-20261115T151037.550733Z.jsonl)

**P4 to P5 handoff at 100%.** Actor config `22891` reached zero HP at received tick
6047 and disappeared by 6062. The input controller ended on that disappearance.
The continued recording saw config `22908` with 100,000 HP on plane 1 at 6067,
one tick before VARP `6363` changed to phase 5. Three 1-HP actors used configs
`22912`, `22913` and `22914`. Six golems with config `22911` had 4,500 maximum HP.
The boss used `28950` sixteen times and reached zero HP at 6133, then applied
`28957`. The log later saw dormant config `22892` and ended without a result event.
After a sampled zero-HP `22891` in P4 at 100% enrage or higher, the controller keeps
the attempt open for up to 18 seconds. Config `22908`, positive actor HP and the packed
phase-5 field together start phase 5 observation in the same log. This uses the existing
`rs3_snapshot_npcs`, packed phase and native event queues. Phase 5 combat responses
still need timing and outcome evidence.
[100% handoff trace](../../../training-data/telos-20261114T173454.045394Z.jsonl)

The 100% waypoint trial reached the handoff again. A separate read-only probe saw
config `22908` with 100,000 HP at logic tick 1830745 and the phase 5 field at 1830776.
It saw golem config `22910` with 4,500 HP. The player reached zero HP at 1831918.
The starting controller stopped at the actor gap, so its attempt log ends at that
point. The account owner confirmed to stay in the green beam throughout phase 5.
Use `work/probe_telos_locations.py --phase 5` to calibrate the green location IDs.
[100% waypoint trace](../../../training-data/telos-20261115T151037.550733Z.jsonl)

Guide-derived from the [RuneScape Wiki strategy page](https://runescape.wiki/w/Telos,_the_Warden/Strategies#Phase_2), which warns it may predate the March 2026 combat modernisation. Rows marked *live* match the 2026-11-14 Compatibility Mode captures.

| Phase | Rotation (3 autos between specials unless stated) | Live evidence |
| --- | --- | --- |
| P1 | 7 autos → Tendrils → Uppercut → Hold Still → repeat from Tendrils | *live*: `28931` → `28938` → `28935` order and counts |
| P2 | Onslaught → Hold Still → Black Virus (≥50% enrage) → Uppercut → Tendrils → repeat. The first special continues the P1 order, except that a P1 ending on Tendrils leads to Onslaught. | *live*: P1 ending on Uppercut opened P2 with Hold Still (twice), on Tendrils with Onslaught, on Hold Still with Tendrils. `28930` opens P2. Onslaught is `28959`, repeated per projectile with rising damage. The virus began after the third auto following Hold Still. |
| P3 | 7 magic autos → Red Virus → spec skip (once) → Uppercut → Hold Still → repeat. The opening depends on the P2 ending. | none |
| P4 | 3 autos → Uppercut → Weak Anima Bomb → Hold Still → repeat from Hold Still | *live*, 500%, one capture each: `28930`, three autos, Hold Still; and `28930`, long autos, Tendrils, `28966` (probably the bomb), Hold Still 4 ticks later |
| P5 | Golem spawn → 10 magic autos → random virus → 17 magic autos → instant-kill anima bomb → 2 autos → repeat | none |

Other guide points relevant to automation:
- P1 HP scales with enrage from 400,000 at 0% to 600,000 at 200% and above. *live*: 600,000 at 250% and 500%.
- In P1, Telos attacks faster when the green stream reaches him. Standing in it blocks this and gives adrenaline, but drains prayer. It is on for about 12 s and off for about 17 s.
- In P2, standing in the black stream reduces damage dealt and taken by 30% and cleanses the black virus. An uncleansed virus ends after 30 s with a heavy hit.
- Uppercut can be avoided by moving at least 3 tiles with Surge, Escape or similar abilities, or by using Resonance, which does not heal.
- Hold Still is countered by Anticipation beforehand, or by Freedom plus Resonance on the second hit. At 250% and above, Telos is stun-immune for 6 s after breaking free.

The controller uses these orders to forecast Hold Still for Anticipation. `python work/replay_anticipation.py` replays every log through the forecast with Anticipation's 41-tick cooldown (measured from the logged timer). On 2026-11-14 it scored P1 25/25, P2 10/13, P3 4/5 and P4 2/2.
- A `28959` within 12 received ticks of the P2 opener is a plain magic attack, not Onslaught. Onslaught came at +15 or later.
- The three P2 misses share one cause. Anticipation was pressed for a P1 Hold Still that the phase transition cut off, and it was still on cooldown when the carried-over Hold Still arrived 11–15 ticks after the P2 opener. Telos HP is unmapped, so the transition cannot be foreseen; reactive Freedom covers that stun.
- The P3 miss has a gap in its log.

## Native channels

- `rs3_snapshot_encounter` provides scoped phase and enrage plus raw variables. At 0% on Compatibility Mode it requires a single active Telos, an instance guard and matching HP to publish phases 1–3. The raw lower bits of VARP 6363 vary within phases and reset at transitions. They are a meter lead. Both clients expose the received server-tick counter. Server phase and prayer-check deadlines remain unknown.
- `rs3_snapshot_combat`, `rs3_npc_action_events_since`, `rs3_player_hit_events_since`, and `rs3_events_since` expose overhead state, animation applications and recipient hitmarks through `Reader`. They provide no server protection deadline or attacker ID.
- `rs3_snapshot_position`, `rs3_snapshot_spatial`, `rs3_snapshot_npc_spatial` and `rs3_snapshot_hazards` provide tiles and candidate arena actors. Compatibility Mode also exposes the guarded local-player NPC target reference and explicit clear state. Recent attempts recorded the reference but could not resolve it to a same-world NPC tile. The Compatibility Mode camera and beam terrain heights support tile projection. Facing remains unmapped.
- `rs3_snapshot_abilities` provides cooldowns and equipment requirements. `rs3_snapshot_effects` carries available effects evidence. Complete castability, stun and bind acknowledgement, virus state and Soul Split healing attribution require more observations.

The original-client 300% evidence and the Compatibility Mode 0% evidence do not establish a timed protection rule for either profile. [Attack catalogue](../../../profiles/telos_attacks.json) · [Attempt logs](../../../training-data/README.md)

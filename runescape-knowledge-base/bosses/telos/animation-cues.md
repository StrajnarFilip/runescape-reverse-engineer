# Telos animation cues

The 2026-11-13 capture began with dormant Telos in a 0% Practice Mode instance. The Compatibility Mode reader reports an event when a new action animation is applied, including successive applications of the same animation ID. Keep the active Telos instance ID and event sequence with each application.

| ID | 0% observation | Controller use |
| --- | --- | --- |
| `28929` | Ordinary melee auto in P1. Seven applications before the first Tendrils. Also seen in P2. | Count each application once. |
| `28931` | Tendrils. | Try Hurricane if its sampled cooldown and the global cooldown allow it. |
| `28932` | Tendrils release, followed by a hitmark and two `28929` applications before Uppercut. | End the Tendrils response and count the release as one slot for the next forecast. |
| `28938` | Charge and Uppercut sequence. Two type `144` player hitmarks appeared about 4.09 and 4.20 seconds after separate applications. | Optional Surge trial 3.6–4.0 seconds after the cue. Verify displacement and damage from the resulting trace. |
| `28935` | Hold Still after two separately animated autos. The capture showed a normal hit, a heavier hit and later landing damage within this sequence. | Prepare Anticipation after the second auto following Uppercut. The observed special can trigger Freedom. With Surge configured, try to leave the landing area after the follow-up hit. |
| `28962` | Wake-up animation on dormant and active Telos. | Ignore for the auto count. |
| `28963` | Repeats during phase handoffs, including the P1→P2 input lockout. | Record the raw ID. Use phase bits and boss HP to recognize the transition. This cue offers no protection flick. |
| `28942` | P4 weak anima bomb throw. The account owner says it follows a full charge bar, usually by the second font, and the soft typeless hit can be blocked by Resonance cast on its tick or halved by Reflect. In all four logged bombs (100% and 250%) the typeless hit, 5,228 to 6,102, started 113 to 116 client logic ticks (2.26 to 2.32 s) after the cue was observed: the start of cue tick +4. Tick labels in the logs read +2 or +3 because they lag the boundary. An 853 melee hit at +2 preceded one of them. | Deflect Melee on +1 and +2. Resonance (in its GCD slot) or Disruption Shield (off the GCD, via Spellbook Swap) is pressed 1.75 to 2.05 s after the cue, during tick +3, so it is up exactly on the hit. Reflect is used from 0.6 s when neither is available. Golem hits can still consume a single-hit block first (account owner). |
| `28930` | Phase opener. A replay found no player hitmark within three received ticks of 53 occurrences across P2 to P4. | Use as a rotation cue. Wait for an attack cue before changing overhead protection. |

The next Tendrils after Hold Still also followed two separate `28929` applications. An ordinary hit embedded in Hold Still could account for the shorter visible count. Keep the count for anticipation and use the special's animation ID to confirm the mechanic. If melee distance changes, capture the next auto before expanding the count to spells. [PVME attack pattern and mechanics](https://pvme.io/pvme-guides/basic-guides/telos-basic/#attack-pattern)

The same P1 melee and special IDs were seen in the isolated 300% captures. The 0% client-to-hitmark intervals are observations. The exact safe movement and prayer switching times still need separate trials. [300% calibration report](../../../docs/validation/telos-prayer-calibration-2026-11-10.json)

In the defensive capture, Hold Still began about 1.29 seconds after the second separately animated melee auto following Uppercut. The script expires that Anticipation forecast 2.4 seconds after the second auto. This is a practice trial cutoff for stale counts. The actual Hold Still animation can still trigger Freedom after the forecast expires.

## Replay and practice controller

From the repository root in PowerShell, set `$telosTrace` to an existing local capture made with `rs3_reader.telos_prayer`. To record later phases without input, run the script without `--port`.

Replay also accepts the read-only observation JSONL below. It emits the phase candidate and raw Telos animation applications after the handoff. The longest existing capture contains `28930` about 1.88 seconds after the P2 field change and `28929` about 8.44 seconds after it. The observation mode records nearby NPC identities in P2 and later for matching golem and font intermissions in future captures.

```powershell
$env:PYTHONPATH = 'python'
python work/telos_zero.py --replay $telosTrace
python work/telos_zero.py --duration 120 > (Join-Path $env:TEMP 'telos-observation.jsonl')
```

For input, stage a fresh 0% Practice Mode instance with Telos dormant, bar 18 selected and auto-retaliate active. Capture the foreground client with `game_console.py shot`. Set `$meleeX` and `$meleeY` to the Deflect Melee slot pixel and `$approachX` and `$approachY` to a walkable approach point in that frame. Read the Freedom and Anticipation tooltips and pass their current keys. On 2026-11-14 those labels were `Z` and `T`. With this keyboard layout, `--displayed-keys` resolves `Z` to the physical `Y` key. Set `--hurricane` and `--surge` only after confirming those abilities on the current bar.

```powershell
$telosFrame = Join-Path $env:TEMP 'telos-practice.png'
python work/game_console.py shot $telosFrame
python work/telos_zero.py --port COM8 --practice-0-confirmed --frame $telosFrame --melee $meleeX $meleeY --approach $approachX $approachY --freedom Z --anticipation T --displayed-keys --observe-transition
```

The script confirms Deflect Melee before approaching. It sends no food or brew inputs, but drinks a super restore dose (`L`) at 50 prayer points or fewer. Only F12 or the player dying ends the run. Lost foreground pauses input until RuneScape is focused again, stale samples are skipped, and a combat queue overflow is logged. A cooldown change acknowledges an ability cast, and tile changes let you measure movement. The runner keeps controlling every phase it reaches; only the `--reacquire` trial still stops input at P2. Use the compact attempt log under `training-data/`. Keep screenshots and raw captures local. [Attempt log format and coverage](../../../training-data/README.md)

An optional `--reacquire TARGET_CYCLE BASIC_ATTACK` practice trial sends one target-cycle and melee basic ability at the start of P2. Assign these keys in the client. The script waits for the phase and HP to agree, a melee auto to occur, and Deflect Melee to be active. It skips the sequence if Telos is already taking damage. A subsequent HP decrease is evidence that damage resumed. The selected target and the cause of the damage still need live validation. [PVME target cycling](https://pvme.io/pvme-guides/miscellaneous-information/mechanics/#target-cycling-tc)

The trial records P2 animations and hitmarks from the first sample, including those before any target-cycle input. It waits up to ten seconds for a sample containing both a new melee auto and active Deflect Melee before yielding to read-only capture if requested.

The first scripted P1 clear showed Revolution damaging Telos in P2 without target cycling. The P2 trace contained three `28929` applications followed by `28935` at 279,041 boss HP. The controller always continues through P2 and P3. It starts a fresh count at each handoff, responds to observed specials and keeps running through P4 and P5 until death or F12. The black beam and phase 2 prayer recovery still need dedicated handling and live validation.

The saved 0% trace recorded no active overhead at the P2 transition or at either of the two subsequent animation applications. On that trace, the protected melee cue required by the reacquisition trial never appeared. A fresh run needs to show whether the Hold Still response can carry protection through to P2.

In that same trace, Deflect Melee was still active for the two Hold Still hitmarks at about 1.14 and 2.31 seconds. The overhead first read `none` at 4.71 seconds, followed by the 4,187 hitmark at 5.31 seconds. The sampled Ctrl+F3/F4 press log was empty through that interval. The trace does not establish why the overhead disappeared. The optional Surge trial logs the first tile change within 1.2 seconds of input. Compare it with the cooldown acknowledgement and subsequent hitmarks to evaluate the dodge.

**Observed on 2026-11-14, 0% P2.** With `--timed-freedom` and the translated Freedom binding, the controller sent Freedom four received ticks after `28935`, after the heavy type `133` hit and with the GCD clear. The cooldown acknowledged at cue +5. The overhead still changed to `none` at cue +8, and a 3,985 type `144` hitmark followed at cue +9. The trial waits for GCD zero at offsets +4 through +7. One successful cast confirms the input path. The landing dodge and protection deadline still need calibration. [Attempt log](../../../training-data/telos-20261114T081931.814573Z.jsonl)

**Hold Still melee and Resonance.** The first hit at +2 dealt 1,049 and 1,068 on Soul Split
in two 250% P1 attempts. The old one-tick window requested Soul Split at +1. Request
Deflect Melee on `28935` and hold it through +2 for the hidden melee hit. Request Soul Split
at +3 when no other protection window is active. Queue Resonance for the heavy melee hit at
+4 using the sampled GCD and verify the cooldown, the observed overhead and the hitmark.
At 3522 the logged hit of 1 and healing of 1,136 coincided with Soul Split and followed the
Resonance press. [1,049 hit](../../../training-data/telos-20261115T052849.237600Z.jsonl) · [1,068 hit and Resonance](../../../training-data/telos-20261115T054049.902492Z.jsonl)

The revised rule was exercised at 250%. In P1 the cue arrived at 4697, the 482-damage
small hit at +2 had Deflect Melee, and the Soul Split request and Resonance acknowledgement
were recorded at +3. Around +4, the 1-damage heavy hit and 2,084 healing were sampled in
native frame 1448. The controller next observed Soul Split in frame 1449, so the exact
overhead of the heavy-hit frame was missing from that trace. In P2 the 347-damage small hit
at +2 also had Deflect Melee. Soul Split was requested at +3, but Resonance was on cooldown
through the heavy hit, which dealt 5,124. The cooldown restarted at logic tick 144620
without a corresponding controller ability request. [250% retest](../../../training-data/telos-20261115T055239.542334Z.jsonl)

The first P1 Resonance request went out on `28935` while the GCD had 1,720 ms left.
Its cooldown started at logic tick 143089. The controller also sent its retry against
the first sample at 143089 with a free GCD and no cooldown yet visible. The restart
at 144620 is consistent with a duplicate press remaining in the ability queue.
[250% retest](../../../training-data/telos-20261115T055239.542334Z.jsonl)

**Ability queueing off (2026-11-15).** With queueing disabled in the client, a GCD ability
pressed while the GCD runs is dropped. Anticipation and Resonance presses with 1,140–1,800 ms
left never cast; presses with 360–620 ms left or an idle GCD cast one received tick later.
Escape cast at any GCD. The controller sends Resonance, Anticipation, Freedom and Hurricane
only in the last 200 ms of the GCD (`GCD_PRESS_LEAD_MS`) or while it is idle. It re-checks
the remaining time just before the key goes down and logs it as `gcd_at_press_ms`. A 100 ms
lead was too late: Anticipation pressed with 76 ms left did not cast in the 06:30 run.
[Queueing-off death](../../../training-data/telos-20261115T060159.222253Z.jsonl)

Across 89 Hold Stills from P1 to P3, one small hit lands at +1 (a type `482` zero under
Deflect) or +2, rarely +3, and the heavy type `133` hit lands at +4. The small hit is type
`133` in P1 and P2 and type `139` in P3 (11 of 11 damaging P3 small hits). A press in
received tick T casts at T+1, which matches Deflect on the cue protecting a hit at +1. Resonance
is therefore pressed in the tick after the small hit is seen at +1 or +2, or at +3 regardless,
so that it casts on +2..+4 and only the heavy hit consumes it. If no GCD slot falls in that
window, Revolution takes the slot and Resonance waits for the next Hold Still. The flicker
deflects the small hit from +0 through +2 (Deflect Magic in P3, Deflect Melee otherwise). When
Resonance will take the heavy hit, Soul Split returns from the start of the tick in which
Resonance is due or was pressed (`prayer_window_released`), so Resonance heals the full,
undeflected hit. Waiting for the cooldown acknowledgement had left Soul Split one tick late in
the 06:51 run. If Resonance cannot take the hit (cooling, unbound, refuted by its
acknowledgement, or no usable GCD slot), Deflect Melee stays for it. Devotion is then pressed
when its cooldown and 50% adrenaline allow. With Deflect Melee it turns the heavy hit into 1
(account owner). Deflect halved melee autos (682 against
1,487 median at 250%), so Deflect Melee also halves an unabsorbed heavy hit. The 2,083 heavy
hit at 5743 came under Deflect Melee, against 5,036 on Soul Split.
The +3 cursor preflight is skipped while the Resonance press is still possible.

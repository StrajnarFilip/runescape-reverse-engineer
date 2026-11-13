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
| `28963` | Seen before and through the change from P1 to P2. | Record the raw ID. Use phase bits and boss HP together to recognize the transition. |
| `28930` | One application recorded early in P2. | Candidate for a magic auto. Correlate with visible casting and hitmarks in another run. |

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

For input, stage a fresh 0% Practice Mode instance with Telos dormant, bar 18 selected and auto-retaliate active. Capture the foreground client with `game_console.py shot`. Set `$meleeX` and `$meleeY` to the Deflect Melee slot pixel and `$approachX` and `$approachY` to a walkable approach point in that frame. `U`, `A` and `8` are the default Freedom, Anticipation and Hurricane bindings. Set `--surge` only when a slot with Surge is visible and its binding is known.

```powershell
$telosFrame = Join-Path $env:TEMP 'telos-practice.png'
python work/game_console.py shot $telosFrame
python work/telos_zero.py --port COM8 --practice-0-confirmed --frame $telosFrame --melee $meleeX $meleeY --approach $approachX $approachY --observe-transition
```

The script confirms Deflect Melee before approaching. It sends no food or brew inputs. F12, lost foreground, stale samples and NPC queue overflow stop input. A cooldown change acknowledges an ability cast, and tile changes let you measure movement. After entering P2 the runner stops input and exits with code 2, or continues read-only recording first with `--observe-transition`. A complete clear needs P2–P4 handling and victory evidence. Keep the JSONL and screenshots local.

An optional `--reacquire TARGET_CYCLE BASIC_ATTACK` practice trial sends one target-cycle and melee basic ability at the start of P2. Assign these keys in the client. The script waits for the phase and HP to agree, a melee auto to occur, and Deflect Melee to be active. It skips the sequence if Telos is already taking damage. A subsequent HP decrease is evidence that damage resumed. The selected target and the cause of the damage still need live validation. [PVME target cycling](https://pvme.io/pvme-guides/miscellaneous-information/mechanics/#target-cycling-tc)

The trial records P2 animations and hitmarks from the first sample, including those before any target-cycle input. It waits up to ten seconds for a sample containing both a new melee auto and active Deflect Melee before yielding to read-only capture if requested.

The saved 0% trace recorded no active overhead at the P2 transition or at either of the two subsequent animation applications. On that trace, the protected melee cue required by the reacquisition trial never appeared. A fresh run needs to show whether the Hold Still response can carry protection through to P2.

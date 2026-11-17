# Soul Split and protection calibration

## Current binding source (2026-11-17)

**Observed:** the original-profile native UI accessor resolved the current
account's Ctrl+F1/F2/F3/F4 chords for Magic/Ranged/Soul Split/Melee. It also
resolved Alt+G for Devotion and the P1 rotation's letter and Page Up/Down keys.
These decoded modifier masks differ from opaque VarClient modifier codes.

**Controller integration rule:** use the guarded C UI role/chord label, exact
consumer sample, process lifetime and selected preset. Re-read the role before
input. A failed native label must not fall back to the older Compatibility
Mode account's plain F1/F3/F4 or its guessed defensive and potion keys.
The old unmodified-key calibration remains a separately named opt-in research
source. It does not establish the current account's key or a prayer deadline.

## Earlier timing captures

The existing `rs3_reader.telos_prayer` prototype evaluates a melee-protection switch on sequence `28929` and an experimental return to Soul Split. The 600 ms hold is a replay parameter. The confirmed association for `28929` comes from 300% P1. Validate it at 0% and measure the intervening hits before trusting either transition.

Run a shadow capture before crossing onto the arena in Practice Mode. Keep Deflect Melee active as the P1 baseline. Manually use `Ctrl+F4` and `Ctrl+F3` for controlled switch observations when there is time to confirm the overhead. The shadow trial records hypothetical decisions, manual key edges, actual overheads, raw NPC action applications, and native player hitmarks in one clock domain.

```powershell
$env:PYTHONPATH = 'python'
python -m rs3_reader.telos_prayer --shadow-trial --enrage 0 --duration 300 --output outputs/telos/0-practice-01.jsonl
```

Record the visual phase and expected attack alongside the trace. Compare ordinary attacks and multi-hit specials separately. A hitmark lacks attacker identity, and a client overhead acknowledgement does not identify the server's protection deadline. Require repeatable results, zero event losses, and enough coverage to justify returning to Soul Split. Keep the trace local. [Planner and prior calibration](../../docs/prayer-flick.rst)

**Observed on 2026-11-14, 0% P2.** Both practice attempts entered P2 with Deflect Melee, sent Freedom for `28935`, and observed no cooldown acknowledgement. The overhead read `none` at 6,601 and 8,328 HP respectively, with 873.4 prayer points in both attempts. The traces preserve the overhead transitions and subsequent hitmarks. The cause of the overhead loss and the protection deadline remain open. [First attempt](../../training-data/telos-20261114T075101.613778Z.jsonl) · [Second attempt](../../training-data/telos-20261114T075957.862524Z.jsonl)

**Observed later on 2026-11-14, 0% P1 and P2.** In two tick-indexed Hold Still sequences, type `133` marks appeared two and four received ticks after `28935`. Deflect Melee changed to `none` eight received ticks after the cue, and a large type `144` mark followed one tick later. A Freedom input at the cue landed during an active GCD in both traces. The timing is client-side and the samples still need causal attribution and server protection-check measurements. [P2 sequence](../../training-data/telos-20261114T081053.312859Z.jsonl) · [P1 sequence](../../training-data/telos-20261114T081521.298786Z.jsonl)

**Practice controller logs.** `prayer_flick_outcome` records whether the requested overhead appeared in the client within the 1.2 s acknowledgement window. It refers back to the issued toggle and records both the issue and observation ticks. The acknowledgement window is the toggle retry timeout. The received hitmark and the server protection check require separate evidence. [Log format](../../training-data/README.md)

In the 500% run that ended in phase 2, 62 of 68 requested flicks were acknowledged. Four timed out in P1 and two in P2. The trace preserves the requested overhead, observed overhead and source animation for each. [500% attempt](../../training-data/telos-20261114T194114.813483Z.jsonl)

Five of those six timeouts followed the repeated transition animation `28963`. The last
followed the `28930` opener, four ticks after the phase field changed. The first accepted
request after the switch was issued at +15, on Tendrils. The controller pauses flick dispatch
from the transition cue until the first recognized attack. Other runs put the first observed
accepted request as early as +10, so the phase field alone cannot establish the input deadline.

In the [250% lockout trial](../../training-data/telos-20261114T194717.352574Z.jsonl), the
controller paused at 4995, the phase field changed at 5008, and the first recognized attack
at 5023 resumed flicking. Deflect Magic appeared at 5024. All 66 prayer requests in the run
were acknowledged. The player died to a later phase 2 barrage.

The [250% scaled-projection trial](../../training-data/telos-20261115T050912.459288Z.jsonl)
acknowledged all 103 flicks and recorded no event queue loss. Confirming the Walk destination
can take an extra native sample. The controller carries Telos animation cues from those samples
into its next poll, using the original received tick when available.

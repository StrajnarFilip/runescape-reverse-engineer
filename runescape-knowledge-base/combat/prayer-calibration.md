# Soul Split and protection calibration

The existing `rs3_reader.telos_prayer` prototype evaluates a melee-protection switch on sequence `28929` and an experimental return to Soul Split. The 600 ms hold is a replay parameter. The confirmed association for `28929` comes from 300% P1. Validate it at 0% and measure the intervening hits before trusting either transition.

Run a shadow capture before crossing onto the arena in Practice Mode. Keep Deflect Melee active as the P1 baseline. Manually use `Ctrl+F4` and `Ctrl+F3` for controlled switch observations when there is time to confirm the overhead. The shadow trial records hypothetical decisions, manual key edges, actual overheads, raw NPC action applications, and native player hitmarks in one clock domain.

```powershell
$env:PYTHONPATH = 'python'
python -m rs3_reader.telos_prayer --shadow-trial --enrage 0 --duration 300 --output outputs/telos/0-practice-01.jsonl
```

Record the visual phase and expected attack alongside the trace. Compare ordinary attacks and multi-hit specials separately. A hitmark lacks attacker identity, and a client overhead acknowledgement does not identify the server's protection deadline. Require repeatable results, zero event losses, and enough coverage to justify returning to Soul Split. Keep the trace local. [Planner and prior calibration](../../docs/prayer-flick.rst)

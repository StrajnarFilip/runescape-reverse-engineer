# Telos attempt logs

Use the 0% Practice Mode controller from the repository root. Stage the character across the gap with Telos dormant, the melee action bar selected and auto-retaliate active. Identify the Deflect Melee slot and a walkable approach tile from the fresh client image. `--continue-phase-two` enables the existing special-response trials through phase 2. `--observe-transition` continues recording after input ends at the next phase limit. F12 stops input. [Setup and bindings](../runescape-knowledge-base/bosses/telos/animation-cues.md)

```powershell
$env:PYTHONPATH = 'python'
$frame = Join-Path $env:TEMP 'telos-practice.png'
python work/game_console.py shot $frame
python work/telos_zero.py --port COM8 --practice-0-confirmed --frame $frame `
    --melee $meleeX $meleeY --approach $approachX $approachY `
    --continue-phase-two --observe-transition
```

The practice controller relies on Revolution for normal damage. It uses client samples and deterministic Python for the experimental special responses. The separate `python -m rs3_reader.bot` entry point runs the integrated decision stack in shadow mode. The Python prayer planner requires measured protection windows and complete incoming coverage before offering a return to Soul Split. The attack catalogue currently has zero validated protection windows.

## Inspecting an attempt

Each file starts with `attempt_started` and a `state` row with a `changes` object. Apply subsequent state changes as top-level field replacements. `sequence` orders rows, `timestamp_ms` is Unix time, and `elapsed_ns` and `observed_ns` are relative to the same attempt origin. `client_logic_tick` is the client clock. `game_tick` is null until a server tick source is mapped.

`actors` maps attempt-local instance IDs to `[config_id, current_hp, max_hp, action_animation, movement_animation]`. `actor_tiles` maps the same IDs to `[x, y, plane, same_world_confirmed]` when the reader has a tile. `ability_cooldowns` maps ability struct IDs to `[reason, start_client_tick, end_client_tick, active]`. A missing timer stays unknown. NPC and player hitmark rows retain native timing and recipient evidence. Hitmarks lack an attacker field.

Join an `ability_sent` row to the `npc_animation` row whose `native_sequence` matches `cue_native_sequence`. Use `cooldown_acknowledgement` and `surge_displacement` for later observations. A cooldown acknowledges a cast. Dodge success, damage prevention and healing each require their own evidence. Inspect `queue_loss` and `attempt_ended` before interpreting a run. The first practice trace predates NPC tile and NPC hitmark logging and has no explicit Anticipation acknowledgement at the phase boundary.

```powershell
$attempt = Get-ChildItem training-data/telos-*.jsonl | Sort-Object Name | Select-Object -Last 1
Get-Content $attempt | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object event -In 'ability_sent','cooldown_acknowledgement','queue_loss','attempt_ended'
```

These logs contain decoded encounter fields and local event numbers. Keep images, chat, account names, process-memory dumps and executable files out of this directory.

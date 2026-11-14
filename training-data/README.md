# Telos attempt logs

Use the 0% Practice Mode controller from the repository root. Stage the character across the gap with Telos dormant, the melee action bar selected and auto-retaliate active. Identify the Deflect Melee slot and a walkable approach tile from the fresh client image. The controller handles every phase it reaches through P3: Deflect Melee flicks in P1/P2, Deflect Magic in P3. P4 and P5 keep the flicks, Escape, Anticipation and Resonance (fonts, golems and bombs are not handled yet). `--observe-transition` continues recording after input ends. F12 stops input. [Setup and bindings](../runescape-knowledge-base/bosses/telos/animation-cues.md)

### Running it yourself

Stand on the staging platform with Telos dormant and Revolution on bar 18. Put Freedom,
Anticipation, Escape, Resonance, Dive and the Deflect Magic, Deflect Melee and Soul Split prayers on
any displayed action bar. The controller reads their keys from game memory
(`rs3_reader.action_bar_keys`) and logs them as `keys_resolved`; a key passed on the command line
takes precedence. Start the script, click into RuneScape, and wake Telos yourself. Only death or F12 ends the run: losing focus pauses input until RuneScape is focused again, stale samples are skipped, and a new scene or Telos instance starts a fresh encounter.
The controller turns on Deflect Melee with its key and takes over when the fight starts. If the player is still under Telos 1-4 ticks after Hold Still, it moves the pointer to a projected tile outside his footprint and presses Dive, avoiding the stomp. F12 stops it.

```powershell
$env:PYTHONPATH = 'python'
python work/telos_zero.py --port COM8
```

The enrage is read from memory (low 12 bits of VARP 6359) and logged as `enrage_resolved`;
`--practice-enrage` overrides it. Prayer flicking, timed Freedom, the attempt log and recording
after input ends are always on. Game key codes and slot contents were calibrated
against this account's bars on 2026-11-14. Raw modifier bits are logged but not used: Soul Split
reported a modifier while plain F4 works. An unexpected key shows up in `keys_resolved`.

### Automatic approach

```powershell
$env:PYTHONPATH = 'python'
$frame = Join-Path $env:TEMP 'telos-practice.png'
python work/game_console.py shot $frame
python work/telos_zero.py --port COM8 --practice-0-confirmed --frame $frame `
    --melee $meleeX $meleeY --approach $approachX $approachY `
    --freedom Z --anticipation T --displayed-keys --observe-transition
```

The practice controller relies on Revolution for normal damage. It uses client samples and deterministic Python for the experimental special responses. The separate `python -m rs3_reader.bot` entry point runs the integrated decision stack in shadow mode. The Python prayer planner requires measured protection windows and complete incoming coverage before offering a return to Soul Split. The attack catalogue currently has zero validated protection windows.

## Inspecting an attempt

Confirm the actual ability tooltips before using the example keys. `--displayed-keys` resolves the client window's keyboard layout to physical Arduino keys. `configured_abilities` records the displayed labels and resolved keys.

`--flick-melee` is an unvalidated user-requested trial for phases 1-2. Each new Telos animation except Uppercut (`28938`) sends Deflect Melee (`--flick-melee-key`, default F3), then Soul Split (`--flick-soul-split-key`, default F4) on the next received tick (0.6 s when the counter is invalid). Soul Split is the overhead between cues. A toggle is only resent after the overhead matches or 1.2 s passes. `prayer_flick_cue` and `prayer_flick_sent` rows record each decision. Server protection deadlines remain unmeasured, so a hit can still land during a flick.

`--practice-enrage N` runs above 0%. It locks the first observed Telos maximum HP and assumes the 0% quarter phase split. Special timings remain calibrated at 0% only.

`--timed-freedom` trials the first clear GCD at received-tick offsets +4 through +7 after `28935`. The observed cast acknowledged one tick after dispatch at +4. This measures the cast path and leaves the landing response unverified.

Each file starts with `attempt_started`. The first `state` row includes the initial `changes` object. Apply subsequent state changes as top-level field replacements. `sequence` orders rows, `timestamp_ms` is Unix time, and `elapsed_ns` and `observed_ns` are relative to the same attempt origin. `client_logic_tick` is the client clock. `game_tick` carries the client's received server-tick counter when valid. `received_tick` rows mark observed counter changes and loss of validity. Receipt time has no calibrated relation to the server's protection check.

`actors` maps attempt-local instance IDs to `[config_id, current_hp, max_hp, action_animation, movement_animation]`. `actor_tiles` maps the same IDs to `[x, y, plane, same_world_confirmed]` when the reader has a tile. `ability_cooldowns` maps ability struct IDs to `[reason, start_client_tick, end_client_tick, active]`. A missing timer stays unknown. NPC and player hitmark rows retain native timing and recipient evidence. Hitmarks lack an attacker field.

Join an `ability_sent` row to the `npc_animation` row whose `native_sequence` matches `cue_native_sequence`. Use `cooldown_acknowledgement` and `surge_displacement` for later observations. A cooldown acknowledges a cast. Dodge success, damage prevention and healing each require their own evidence. Inspect `queue_loss` and `attempt_ended` before interpreting a run. The first practice trace predates NPC tile and NPC hitmark logging and has no explicit Anticipation acknowledgement at the phase boundary.

```powershell
$attempt = Get-ChildItem training-data/telos-*.jsonl | Sort-Object Name | Select-Object -Last 1
Get-Content $attempt | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object event -In 'ability_sent','cooldown_acknowledgement','queue_loss','attempt_ended'
```

These logs contain decoded encounter fields and local event numbers. Keep images, chat, account names, process-memory dumps and executable files out of this directory.

# Telos attempt logs

Run the Practice Mode controller from the repository root. Stage the character across the gap
with Telos dormant, the melee action bar selected and auto-retaliate active. The controller
uses Telos's animations and phase for prayer flicks, special responses and P4 font and golem
handling. See the coverage table for the evidence and open timing questions. [Setup and bindings](../runescape-knowledge-base/bosses/telos/animation-cues.md) · [Mechanic coverage](../runescape-knowledge-base/bosses/telos/mechanic-coverage.md)

### Running it yourself

Stand on the staging platform with Telos dormant and Revolution on bar 18. Put Freedom,
Anticipation, Escape, Resonance, Dive and the Deflect Magic, Deflect Melee and Soul Split prayers on
any displayed action bar. The controller reads their keys from game memory
(`rs3_reader.action_bar_keys`) and logs them as `keys_resolved`; a key passed on the command line
takes precedence. Start the script, click into RuneScape, and wake Telos yourself. After engagement,
leaving the encounter closes its attempt log. Losing focus pauses input until RuneScape is focused
again, stale samples are skipped, and F12 stops the script.
The controller turns on Deflect Melee and takes over when the fight starts. It checks for
the Hold Still stomp at +3 to +6 received ticks, after checking the cursor's landing tile
at +2. Up to two targets are tried per tick when a hover is rejected. At +3 the dodge waits
for a Resonance press due before it could finish. If the player needs to dodge, Dive
prefers the green strip in P1 or red strip in P3 within ten tiles and clear of Telos.
The binding `rs3_snapshot_pointer_walk` confirms the current ground tile under the pointer
before the click. `rs3_snapshot_npc_spatial`, `rs3_snapshot_hazards`,
`rs3_hazard_terrain_height` and `rs3_project_tile` supply the footprint, beam location,
terrain height and screen projection.

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

The practice controller flicks by Telos animation and phase. It uses the measured distance
bands for `28959` and holds melee through the unanimated follow-up from Tendrils release.
For Hold Still, the controller deflects the small hit from +0 through +2 after `28935`, using
Deflect Magic in P3 and Deflect Melee otherwise. If Resonance will take the heavy +4 hit,
Soul Split returns from the start of the tick in which Resonance is due or was pressed, so that
Resonance heals the full hit. `prayer_window_released` records this. Otherwise Deflect Melee
stays for the heavy hit. Devotion is pressed when it is bound, ready and affordable (50%
adrenaline), and the GCD slot is one Resonance cannot use. GCD abilities are pressed only in the last 200 ms of the GCD because
ability queueing is off. `ability_sent.gcd_at_press_ms` records the time left, and
`ability_press_skipped` marks a GCD window that passed while deciding.
In P1 and P3 the controller keeps the player on the green or red strip. It uses only strip
tiles in melee reach of Telos and never lands under him. Auto-attack undid every landing out of
reach. It uses Dive when Dive is ready and the tile is within Dive range, except while Hold
Still is the forecast special, and otherwise walks. Dive's cooldown is read from Bladed Dive's
shared timer. Each attempt is a `beam_keep_move` row with the verified
hover. `beam_keep_arrival` and `beam_keep_departure` mark changes in standing on the strip.
Moves wait for a tick with no protection press and yield to Escape, the stomp dodge and the P3
virus walk. In P4 the anima bomb `28942` gets Resonance, or Reflect if bound with `--reflect`
or on a bar. Its `ability_sent` reason is `Anima bomb`.
Uppercut and the phase cues `28963` and `28930` do not schedule protection. A
`prayer_transition_lockout` row marks when prayer inputs pause at a phase handoff or resume
on the first recognized attack. A toggle is only resent after its overhead appears or 1.2 s
passes. Server protection deadlines remain unmeasured.

`--practice-enrage N` runs above 0%. It locks the first observed Telos maximum HP and assumes the
quarter phase split. See the coverage table and individual traces for timing evidence by enrage.

`--timed-freedom` trials the first clear GCD at received-tick offsets +4 through +7 after `28935`. The observed cast acknowledged one tick after dispatch at +4. This measures the cast path and leaves the landing response unverified.

Each file starts with `attempt_started`. The first `state` row includes the initial `changes` object. Apply subsequent state changes as top-level field replacements. `sequence` orders rows, `timestamp_ms` is Unix time, and `elapsed_ns` and `observed_ns` are relative to the same attempt origin. `client_logic_tick` is the client clock. `game_tick` carries the client's received server-tick counter when valid. `received_tick` rows mark observed counter changes and loss of validity. Receipt time has no calibrated relation to the server's protection check.
In captures made before the prayer outcome timestamp fix, use `sequence` to order a prayer
outcome and the immediately following request or attempt end. Their `elapsed_ns` values can
appear a few microseconds out of order.

`actors` maps attempt-local instance IDs to `[config_id, current_hp, max_hp, action_animation, movement_animation]`. `actor_tiles` maps the same IDs to `[x, y, plane, same_world_confirmed]` when the reader has a tile. `ability_cooldowns` maps ability struct IDs to `[reason, start_client_tick, end_client_tick, active]`. A missing timer stays unknown. NPC and player hitmark rows retain native timing and recipient evidence. Hitmarks lack an attacker field.

`beam_tiles` contains `[kind, x, y, plane, config_id]` tuples for the green, black and red
streams. `beam_ground_heights` adds `[x, y, plane, fine_height]` for tiles the reader can project.
`camera_viewport` records the render width and height followed by the physical width and height.
The practice controller reads encounter VARP `6362` in P2 and P3. Join `anima_virus_detected`,
`anima_virus_move`, `anima_virus_beam_arrival` and `anima_virus_outcome` by `onset_tick` in
the same phase. The outcome `cleanse_confirmed` requires the countdown reset and a matching
`anima_stream_cleanse_observed` message ID. `timer_cleared` means the countdown ended without
the message. The nearest logged beam row can be one tile away when a cleanse occurs.
Movement rows include the projected target, the observed default action and tile in `hover`,
and the verified `walk_target` and `dispatch_tick` when a click is sent.
During P2 Onslaught the controller always walks into the black stream while it feeds Telos;
the old `--block-black-stream` switch is accepted as a no-op. Read `beam_feeding` and
`beam_move` alongside player tiles, VARP `6363` and successive `28959` cues to verify the
player blocked the stream and the meter drained.
A log with no `attempt_ended` was interrupted before the writer could record its outcome.

Join `prayer_flick_outcome.request_sequence` to the original `prayer_flick_sent` row. An
`acknowledged` outcome confirms the overhead observed in the client and records the sampling
tick. Compare it with the recipient's hitmark and its client clock to assess the window.
The switch carries the cue of the protection window that selected it, including during an
overlap. `overhead_at_sample` gives the prayer from the hitmark's native frame when it was
available, including intermediate cursor checks sampled by the controller.
For Hold Still, compare `stomp_preflight`, `stomp_dodge` and `stomp_walk_fallback` with
`stomp_dodge_result`. The preflight only records a cursor check at +2.
Join the response to `npc_animation` by `cue_native_sequence`. `hover` records the sampled
Walk tile, MiniMenu cycle, and source frame and client logic tick. In P1 and P3, a verified
beam destination also carries `hover.beam_config`. `clear_of_stomp` records displacement
outside the estimated radius, and `landed_on_beam` confirms that the observed player tile
was on the strip. `observed_tick_delta` gives the position sample's offset from the cue.
Check it against the hitmark. In one live P3 attempt, Dive moved the player at +8 and the
stomp still landed at +9.

Join an `ability_sent` row to the `npc_animation` row whose `native_sequence` matches `cue_native_sequence`. Use `cooldown_acknowledgement` and `surge_displacement` for later observations. A cooldown acknowledges a cast. Dodge success, damage prevention and healing each require their own evidence. Inspect `queue_loss` and `attempt_ended` before interpreting a run. The first practice trace predates NPC tile and NPC hitmark logging and has no explicit Anticipation acknowledgement at the phase boundary.

```powershell
$attempt = Get-ChildItem training-data/telos-*.jsonl | Sort-Object Name | Select-Object -Last 1
Get-Content $attempt | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object event -In 'ability_sent','cooldown_acknowledgement','queue_loss','attempt_ended'
```

These logs contain decoded encounter fields and local event numbers. Keep images, chat, account names, process-memory dumps and executable files out of this directory.

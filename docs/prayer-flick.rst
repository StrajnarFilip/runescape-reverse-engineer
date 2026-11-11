Prayer timing engine and native combat observations
===================================================

Status
------

The C timing planner, native prayer/animation/player-hit observations, Python
observer and C/Python/Java consumers are implemented. The first two live Telos
captures cover 300% enrage, phase 1. Later controlled keyboard experiments
cover opening autos at 300% and 2000%. Production flicking remains disabled;
the explicitly authorized, bounded calibration runner is described below.

The intended account has Soul Split. ``profiles/prayer_flick.json`` enables it
and records the user's bindings: Ctrl+F1 Magic, Ctrl+F2 Ranged, Ctrl+F3 Soul Split,
Ctrl+F4 Melee. All four curses were observed on the intended account on
2026-11-10. Six Soul Split/Melee changes using Ctrl+F3/Ctrl+F4 were matched to
client overhead transitions. Both healing and protection transitions are also
covered by deterministic replay tests. The requested Telos range is 300%-4000%
enrage; other phases and higher enrages still need calibration.

Layout follows the current repository:

* ``src/prayer.c``: deterministic C policy, with no memory access or input.
* ``include/rs3/prayer.h``: language-neutral planner API and documented layouts.
* ``src/combat.c``: C acquisition of the numeric overhead prayer flags.
* ``src/npc_animation.c``: raw NPC animation sequences and NPC action events.
* ``src/player_hits.c``: guarded local-player hitmark snapshots and event queue.
* ``src/input.c``: the explicit, opt-in ``rs3_set_prayer`` key chord.
* ``include/rs3/combat.h``: additive snapshot accessors and explicit prayer input.
* ``python/rs3_reader/prayer.py``: ctypes binding; policy remains in C.
* ``python/rs3_reader/prayer_flick.py``: observer and synthetic replay runner.
* ``python/rs3_reader/telos_prayer.py``: Telos timing capture and the gated trial.
* ``profiles/prayer_flick.json``: intended-account availability, bindings, margins.
* ``work/capture_local_combat.py`` and ``work/analyze_telos_calibration.py``:
  reference-decoder hitmark research and compact calibration reports.
* ``tests/native/test_prayer_planner.c``, ``tests/native/test_combat.c``,
  ``tests/native/test_player_hits.c`` and ``tests/python/test_prayer.py``:
  policy, acquisition and FFI checks.

Running it
----------

Build through the root CMake project. From the repository root, put the Python
package on the import path once per shell (PowerShell shown; use
``set PYTHONPATH=python`` in ``cmd``), then use::

    $env:PYTHONPATH = "python"
    python -m rs3_reader.prayer_flick --duration 60
    python -m rs3_reader.prayer_flick --pid 12345 --duration 60
    python -m rs3_reader.prayer_flick --replay examples/prayer_replay.json

The observer prints changed observations and heartbeats as JSON Lines. It
reports current prayer, raw NPC sequences and the planner's reason for holding
or refusing a switch. ``live_flick_ready`` remains false while the attack
classifier and timing/coverage calibration are missing. No execute option is
provided by this observer. The desktop tracker also shows active prayer and an
NPC Action seq column; full raw fields remain available through Copy current JSON.

For a compact local capture of C-collected observations::

    python work/capture_prayer.py --duration 300 --output work/prayer_research/capture.jsonl

Raw captures remain local. The committed evidence summary is
``docs/validation/prayer-validation.json``. Its five-minute recording contains
1244 changed/heartbeat rows and spans 29994 native sample sequence increments.
The requested acquisition frequency was 100 Hz. The median acquisition time
among those retained rows was 0.5403ms; this is not a benchmark of every poll.
Manual Magic, Ranged and Melee transitions were observed. No automatic key
chords or Telos encounter were recorded.

New captures also contain ``npc_action_events`` and ``npc_action_events_lost``.
Use each event's ``sample_sequence`` and ``observed_ns`` to align it with nearby
snapshots. The worker and capture loop run independently, so an event may
originate from a different sample than its enclosing JSON row. Rows include
player life points, observed overhead, the raw Telos variable when present,
and player position on the profile that supports it.

Telos timing capture
--------------------

``telos_prayer.py`` labels Telos action sequence ``28929`` as a melee auto
using the player's identification. Run it during manual practice::

    python -m rs3_reader.telos_prayer --duration 180 --enrage 300 --record-keys --output work/prayer_research/telos-practice.jsonl

Set ``--enrage`` to the enrage actually played. The optional key sampler
timestamps manual Ctrl+F4 (melee protection) and Ctrl+F3 (Soul Split) down
edges while the attached RuneScape process has focus. The observed overhead
comes from the native prayer flags. An unknown overhead is JSON null, and a
sampled key press is recorded separately from any observed prayer transition.
Very short key presses can fall between samples.

Rows include all observed Telos action applications, current animation, life
points and native sample times. Compare a cue, key press, overhead transition
and resulting hit across multiple attacks. An HP change alone cannot establish
the server's prayer check or distinguish overlapping hits.

The old fixed-600ms ``PrayerTrial`` prototype remains available for replay tests.
The CLI rejects ``--trial`` before opening the reader while either
``attack_coverage_validated`` or ``prayer_check_deadline_validated`` is false.
Both are false in the shipped profile. Changing those flags alone would not
calibrate the prototype. The 600ms delay describes neither a verified protection
window nor a safe Soul Split return deadline. No automatic input was sent during
the 2026-11-10 captures.

First live calibration: 2026-11-10
----------------------------------

``validation/telos-prayer-calibration-2026-11-10.json`` contains compact results
and SHA-256 hashes of the two local captures. Raw gameplay records stay local.
The player labelled both encounters as 300% phase 1, and confirmed the first
special order as tendrils, uppercut, then Hold Still. The observed associations
apply to that context:

=========  ====================================
Sequence   Observed association
=========  ====================================
28929      Ordinary melee auto (25 cues total)
28931      Tendrils
28938      Uppercut
28935      Hold Still composite special
=========  ====================================

Telos NPC forms 22891 (active) and 22892 (dormant) were observed. Sequence 28932
also appeared after tendrils but remains unlabelled. These associations are not
a complete phase/style classifier or a prayer-check schedule.

The second capture contains 22 native player-hit events and no event-queue
losses. Six damage hitmarks paired with ordinary-auto cues appeared roughly
510-600ms later (median 600.125ms); seven miss hitmarks appeared around the cue.
Misses are summarized separately so their near-zero delay does not distort the
damage timing. Tiny negative differences can arise from separate observation
times within one acquisition cycle. The hitmarks do not identify their attacker;
these pairs rely on the isolated phase-1 context. Random damage amounts alone
do not establish successful or failed protection.

The six matched manual Ctrl+F3/Ctrl+F4 changes took 259-760ms from sampled key
down to observed client overhead (median 429.011ms). Key polling intervals were
about 15-16ms. These are client observations, not precise Windows event times or
server-side prayer activation times. The observed variation prevents treating
an animation cue followed by a fixed 600ms delay as a validated flick policy.
Anticipation, acknowledgement and protection deadlines need further measurement.

To reproduce the compact report from the local recordings::

    python work/analyze_telos_calibration.py --capture work/prayer_research/telos-session-2026-11-10.jsonl work/prayer_research/telos-session-native-hits-2026-11-10.jsonl --enrage 300 --phase 1 --session-date 2026-11-10 --output docs/validation/telos-prayer-calibration-2026-11-10.json

Manual Devotion timing experiment
----------------------------------

For the intended account, Devotion is bound to Alt+G. This optional capture
mode records that explicit chord alongside the configured prayer chords::

    python work/capture_prayer.py --pid 12345 --duration 1200 --record-devotion-alt-g --output work/prayer_research/prayer-check.jsonl

The additional key observer reads only G and modifier states in the attached
game's foreground window. It sends no input. ``manual_ability_keys`` retains
the sampled down edge separately from the native ``devotion_cooldown`` timer
(struct 25028). A key press does not prove a successful cast; an observed
cooldown does not establish the buff's active interval. ``buff_active`` stays
null. ``previous_sample_ns`` bounds the preceding native observation, avoiding
an assumption that the last retained heartbeat was the previous poll. New
capture files use exclusive creation to protect earlier recordings.

The controlled opening compares three ordinary phase-1 melee autos at 300%:
hold Melee and cast Devotion at the first swing, switch early before the second
auto, then switch at the third swing. Return to normal play afterward. The
player must confirm the visible Devotion activation and identify any stuns,
other defensives or special interruptions. Guard against expiry and overlapping
hits before treating any outcome as a protection check. A miss alone is
inconclusive. The test records evidence; it does not arm the automatic trial.

The current `Wiki Devotion article <https://runescape.wiki/w/Devotion>`_ lists
9.6 seconds of duration and matching protected damage reduced to 1. These are
guide-derived expectations until checked in the attached executable.

The 2026-11-11 capture recorded Alt+G followed 406ms later by a native cooldown
start. The player confirmed visible Devotion activation and no other defensives
or stuns during the first three ordinary autos. Their hitmarks were:

====  ===================  ===================  ======
Auto  Melee key after cue  Hitmark after cue    Damage
====  ===================  ===================  ======
1     Already active       529.956ms            1
2     227.746ms            549.759ms            1
3     92.929ms             619.546ms            1
====  ===================  ===================  ======

These are successful cases, not a latest-safe-switch boundary. For auto 3,
the first sampled Melee flag appeared about 9.5ms after the hitmark; flags and
hitmarks are read separately. That observation cannot place the server's check
on either side of the client flag transition. A later miss is inconclusive;
later ordinary damage after Devotion's expected expiry is outside this control.
The report is ``validation/telos-prayer-check-2026-11-11.json``. Reproduce it
with ``work/analyze_telos_calibration.py`` and ``--confirmed-devotion-opening``
only when the player supplied that clean-opening label. Automatic coverage and
deadline flags remain false.

Controlled keyboard experiments
--------------------------------

The user authorized scripted calibration on 2026-11-11 after manual subsecond
switching proved impractical. ``work/calibrate_prayer_check.py`` uses the existing
foreground C input API, independently of the gated production trial::

    python work/calibrate_prayer_check.py --controlled-input --pid 12345 --delay-ms 350 --enrage-label 300 --output work/prayer_research/controlled.jsonl

The player selects Melee, starts a fresh practice encounter and casts Devotion.
The runner observes a dormant-to-active Telos opening and requires a fresh
native Devotion cooldown plus a one-damage ordinary-auto control. It then sends
Ctrl+F3, waits for Soul Split acknowledgement, and schedules one Ctrl+F4 at the
requested delay after the next ordinary auto cue. It records the hit, confirms
Melee and stops. It does not continue fighting or repeat switch cycles.

Input requires fresh frames and cues, the same process/scene/NPC, and the game
in the foreground. Requested delays are bounded to 0..600ms; command lateness,
unconfirmed prayers, event gaps, special actions and expired control windows
stop the experiment. Restoration uses only fresh, known state; loss of a
trustworthy target is reported as requiring manual protection. Setup waits for
observed Melee rather than sending baseline toggles through possible menus.
A baseline acknowledgement timeout from the earlier setup is retained in the
report, alongside an inconclusive miss that cancelled one 450ms attempt.

Enrage is a user label, not an input to the switch calculation. Completed
clean trials produced the following observations:

========  ===============  ===============  ============  ==========
Enrage    Requested delay  Accepted delay   Control hit   Test hit
========  ===============  ===============  ============  ==========
300%      350ms            359.036ms        1             1
300%      500ms            515.533ms        1             691
2000%     450ms            451.795ms        1             8003
2000%     400ms            408.287ms        1             5402
========  ===============  ===============  ============  ==========

The player confirmed active Devotion and clean openings. Native cooldown ages
at the test hits were 3.6..4.82 seconds; the active buff itself remains unmapped.
The Melee command-to-observed-flag delays ranged from 260ms to 761ms. These
observations support a successful case at 359ms and late cases from 408ms,
not a universal cutoff or an exact server prayer-check timestamp. Anticipation
using the ordinary four-tick rhythm and acknowledgement margins needs separate
validation before production use. Other phases, stuns, Freedom, beams and
complete incoming-attack coverage remain outside these opening experiments.

The compact report retains each capture hash and enrage label. Its input flag
distinguishes actual scripted commands from manual key observations; misses
and aborted runs do not become successful trials. Use ``--capture-enrages``
when reproducing a report that combines different user-reported enrages.

Directly available data
-----------------------

``rs3_snapshot_combat`` returns masks of known/active overheads and raw variable
values with independent presence bits. Its size is 48 bytes. In particular:

================  ======================  ===============================
Variable          Bits                    Meaning from local definitions
================  ======================  ===============================
3272              6 / 7 / 8               Magic / Ranged / Melee protection
3275              7 / 8 / 9 / 18          Magic / Ranged / Melee / Soul Split
3273              3                       Necromancy protection
5859              17                      Necromancy deflection
================  ======================  ===============================

The earlier account's protection transitions used VARP 3272 values 64, 128 and
256. On the intended account, the 2026-11-10 baseline positively observed VARP
3275 values 128 (Magic), 256 (Ranged), 262144 (Soul Split) and 512 (Melee), with
VARP 3272 zero. Necromancy rows remain definition-derived and fixture-tested;
their live validation is outstanding. The selected prayer book is not inferred from the
user's key labels. An absent sparse entry remains unknown. A positively observed
single overhead can be identified even if other flag containers are absent;
claiming that every overhead is off requires complete known masks. Conflicting
positive overhead bits are rejected.

The combat extension checks client state 30 at offset ``0x19fc0`` and withholds
its prayer state outside that scene. On the original executable, the predicate
at RVA ``0x356b0`` loads the owning client from ``+8`` and compares this field
with 30; its instruction bytes are now included in the profile signatures.
The field was observed as 30 in the live scene. Logout/world-hop behaviour is
fixture-tested but has not had a controlled live validation. ``scene_epoch``
increments after observed scene loss, world-context replacement or process
creation changes. Transitions entirely missed by polling cannot be guaranteed.

``rs3_snapshot_npc_animations`` returns 32-byte records tied to the same local NPC
instance IDs as the existing snapshot. On the original 951-3 executable
(``fd8afd32...358d45``), it reads action sequence ``+0xaa0`` and movement sequence
``+0x970``, validating the component at ``+0x310`` against vtable RVA ``0x15dc740``.
Both IDs are re-read before publication, followed by the existing NPC identity
checks. A failed optional animation read does not discard valid NPC HP.

Static provenance: actor constructor RVA ``0xc3c110`` embeds this component;
component constructor ``0xc12910`` creates the two sequence slots; slot
constructor ``0xc14a00`` initializes the sequence member at slot ``+8`` to -1.
Observed idle/movement sequences and nonnegative action sequences establish
that the fields change, not that a particular sequence is a Telos attack.

Compatibility Mode (``7a60c69f...42dbf``) uses the same sequence offsets and
component vtable RVA ``0x15b3890``. The constructor at RVA ``0xc3bf00`` sets the
component at ``+0x310``. The call at ``0xc13006`` passes the action slot at
component ``+0x788`` into the sequence application routine. That routine writes
the application stamp to slot ``+0x7c`` (actor ``+0xb14``) after applying the
sequence. The collector rechecks the stamp alongside the sequence IDs.

NPC action event stream
-----------------------

``rs3_npc_action_events_since`` returns the 80-byte ``rs3_npc_action_event``
records from a separate queue of 4096 observations. Start with a caller-owned
cursor of zero and advance it to ``next`` after each drain. ``lost`` reports
overwritten entries. The queue stays readable between snapshot reads and each
event identifies the sampled process, scene, NPC instance and client logic tick.

On Compatibility Mode, a changed stamp on an active action slot emits one
event even if the sequence ID stays the same. On the original executable, a
change to a nonnegative action sequence emits an event. A newly observed NPC,
failed read, or scene change establishes a fresh baseline. The worker can miss
changes between samples and applications that share a stamp. The stamp and
observation time describe when the client processed or exposed an animation.
The server's prayer-check time requires separate measurement.

Python consumers can call ``Reader.drain_npc_action_events()`` to get owned
records and the queue loss count. For example, inside a loop with a running
``Reader(worker_hz=100)``::

    events, lost = reader.drain_npc_action_events()
    for event in events:
        print(event.instance_id, event.action_sequence, event.observed_ns)

Before using an event for live decisions, match its PID, process creation time
and scene epoch to the current snapshot, check its age, and invalidate any
inferred attack state after a queue gap.

The Compatibility Mode smoke capture recorded 21 events in 60 seconds with
zero queue loss. Two observations kept the same sequence ID around NPC config
changes. Fixtures exercise repeated IDs, torn reads, scene changes and queue
overflow. See ``validation/npc-action-validation.json`` for the capture summary.

Local-player hitmarks
---------------------

``rs3_snapshot_player_hit_info`` returns a 24-byte status/identity record;
``rs3_snapshot_player_hits`` copies caller-owned ``rs3_hit`` records. The
independent ``rs3_player_hit_events_since`` queue holds 4096 observations with
an explicit cursor and overwritten-event count. Its 96-byte records carry the
process creation time, scene epoch, local-player instance, native observation
time and sample sequence. Verify layouts with ``rs3_combat_sizeof(4)`` and
``rs3_combat_sizeof(5)`` before binding from another language.

The original executable's world context exposes the local index at ``+0x48``
and an optional actor override at ``+0x58``. Without an override, the collector
uses the player table at world ``+0x19968`` and slot ``+0x38``. It validates the
world-context owner, actor vtable RVA ``0x15367e0`` and actor UID at ``+0x88``
before decoding the hitmark ring at actor ``+0xf18``. It rechecks identity and
ring fields after the read. The profile hash/signature checks still apply.
The Compatibility Mode local-player actor layout has not been validated, so
this optional channel reports unsupported there.

An initial active ring establishes a baseline instead of replaying old hits as
new events. Failed reads and scene changes invalidate this baseline. Scheduled
records remain visible in the snapshot but enter the event queue only when
observed active. A valid empty ring is an empty observation, not proof of complete
incoming-attack coverage. A failed optional read does not discard player HP or
NPC observations. There is no attacker field in these records.

Python exposes ``Reader.player_hit_info``, ``Reader.player_hits`` and
``Reader.drain_player_hit_events()``. JSON telemetry adds ``player_hit_splats``
independently of the NPC list. ``work/capture_prayer.py`` records these native
observations and configured foreground prayer-key down edges. The separate
``work/capture_local_combat.py`` is a research/reference diagnostic, not the
runtime acquisition engine.

Planner contract
----------------

Create one ``rs3_prayer_planner`` per serialized consumer and destroy it with the
matching API. No collector context or game process is required. All time values
must use one monotonic clock domain; live consumers can use
``rs3_monotonic_ns``. Absolute wall-clock capture timestamps must not be mixed
with that clock. The struct sizes are 32/56/40/56 bytes for config/frame/threat/
decision; verify them with ``rs3_prayer_sizeof`` when binding another language.

1. Supply a fresh ``rs3_prayer_frame`` containing session identity, sequence,
   observed overhead, positive prayer points and available prayers.
2. Supply the complete current list of pending threats. Each needs a unique
   event identity, source instance, required protection and a validated interval
   during which prayer may be checked. This interval is independent of when
   damage appears. A known unprotectable attack uses ``RS3_PRAYER_NONE``.
3. ``rs3_prayer_step`` expands protection windows by lead/hold margins, returns
   SET/HOLD/BLOCKED/WAIT_ACK/FAULT, and marks a missed earliest deadline. Two
   incompatible required overheads in overlapping windows produce a conflict.
4. A SET result is a proposal, not a keypress. Send it through the separate
   explicit input layer only after checking the current scene and binding.
   Call ``rs3_prayer_note_sent`` only if Windows accepted that input.
5. A later sample must confirm the new overhead. Re-reading the same sample
   cannot acknowledge a command. While waiting, no second toggle is proposed.
   A timeout or loss of trustworthy observations after dispatch latches a fault
   instead of blindly toggling again. Re-observe and explicitly reset to recover.

Soul Split is proposed only with explicit complete attack coverage extending
far enough into the future and enough room for acknowledgement plus the minimum
healing window. An empty attack list alone never establishes that it is safe.
Without enough time, the planner favours early protection. The 600ms lead,
100ms hold and other default settings are starting margins, not measured Telos
latency or a guarantee. This engine cannot promise perfect prayer timing over
network delay, missed samples or incomplete client information.

``next_wakeup_ns`` is an upper bound for re-evaluation, not permission to ignore
new observations. Continue processing new native frames and threat changes.
Game ticks (600ms), the client timer unit (20ms) and acquisition intervals are
distinct. Sampling at 100Hz does not reveal future server decisions.

Input and interoperation
------------------------

``rs3_set_prayer(snapshot, prayer, virtual_key, modifiers)`` is an explicit C
function. It checks the verified process, foreground game window, modifier/key
states, an observed different overhead, positive prayer points and a snapshot
no older than 150ms. Modifier bit 1 is Ctrl, 2 Shift, 4 Alt. The key chord releases
its modifiers, including a best-effort release after a partial Windows insertion.
User-held modifiers cause rejection. Input calls should be serialized by their
caller. An accepted key does not establish a correct action-bar mapping or an
unlocked prayer; confirm the actual resulting overhead.

The sampler and general prayer observer only collect data. The experimental
``telos_prayer.py --trial`` path is blocked by the unvalidated coverage/deadline
flags in the shipped profile. Its bindings are Ctrl+F4 Melee and Ctrl+F3 Soul
Split, matching the intended account; it has not been run during calibration.

CMake builds ``prayer_example`` from ``examples/prayer.c``. Python replay is
shown above. The Java 22+ FFM example verifies layouts before invoking C::

    java --enable-native-access=ALL-UNNAMED examples/PrayerPlanner.java C:/absolute/path/rs3_native.dll

All three consumers use the same C engine and their examples use synthetic
observations. Existing ABI-v1 layouts are unchanged; new structs/accessors are
additive. Installations include ``rs3/prayer.h`` and ``rs3/combat.h``.

Remaining Telos calibration
---------------------------

Before enabling live flicking, the remaining native observations are:

* Attacker target identity: an NPC animating nearby may be attacking someone else.
* Verified Telos sequence-to-attack/style mappings, conditioned on phase, distance
  and special-attack components. Do not identify attacks from an arbitrary ID.
* Repeated-animation coverage in Telos combat. Compatibility Mode has a raw
  application stamp. The original profile observes sequence changes only.
* Prayer-check deadlines, including projectile travel and paired/scheduled hits;
  an animation start or a recorded hitsplat is not automatically that deadline.
* Complete relevant incoming-attack coverage, including golems and phase changes.
* More key-to-observed-overhead measurements under combat load and server-tick
  alignment. The initial keyboard baseline confirms Soul Split acknowledgement
  on the intended account, but does not establish worst-case dispatch latency.

A manual Telos practice capture should label melee-range autos, out-of-range
magic autos and specials separately. Record enrage, phase, whether Telos
targets the player, the visible cue, projectile or hit timing, and the prayer
used. Compare the event timestamp against the latest successful manual
protection change for each attack. A cue that arrives after prayer is checked
cannot drive a reactive switch. Use the sequence mapping and lead time only
after validating them in the intended encounter.

Sources
-------

* `PVME prayer timing <https://pvme.io/pvme-guides/new-to-bossing/prayer-flicking/>`_.
* `PVME melee Telos <https://pvme.io/pvme-guides/rs3-full-boss-guides/telos/melee/>`_.
* ``docs/telos-melee-research.rst``: guide assumptions and conflicting legacy values.
* Local read-only JS5 VarBit definitions: 16745-16747, 16768-16770, 16779,
  53274 and 53281. Their symbolic names were research leads from public
  `gameval metadata <https://github.com/BotWithUs/NXTDebugger/tree/c771c1d374c30373662f55776760762300097d8d/resources/gameval>`_.
  No game agent from that project is used.

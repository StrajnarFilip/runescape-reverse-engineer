Prayer timing engine and native combat observations
===================================================

Status
------

The C timing planner, native prayer/animation observations, Python observer and
C/Python/Java consumers are implemented. The separate Telos script can repeat
an experimental switch cycle. A calibrated automatic Telos flicker requires
measured prayer-check windows and attack coverage.

The intended account has Soul Split. ``profiles/prayer_flick.json`` enables it
and records the user's bindings: Ctrl+F1 Magic, Ctrl+F2 Ranged, Ctrl+F3 Soul Split,
Ctrl+F4 Melee. Soul Split could not be live-validated on the calibration account,
which has not unlocked it. Both healing and protection transitions are covered
by deterministic replay tests.
The requested Telos range is 300%-4000% enrage. The user is preparing that
account and will signal when it is available for live calibration.

Layout follows the current repository:

* ``src/prayer.c``: deterministic C policy, with no memory access or input.
* ``include/rs3/prayer.h``: language-neutral planner API and documented layouts.
* ``src/combat.c``: C acquisition of the numeric overhead prayer flags.
* ``src/npc_animation.c``: raw NPC animation sequences and NPC action events.
* ``src/input.c``: the explicit, opt-in ``rs3_set_prayer`` key chord.
* ``include/rs3/combat.h``: additive snapshot accessors and explicit prayer input.
* ``python/rs3_reader/prayer.py``: ctypes binding; policy remains in C.
* ``python/rs3_reader/prayer_flick.py``: observer and synthetic replay runner.
* ``python/rs3_reader/telos_prayer.py``: Telos timing capture and experimental trial.
* ``profiles/prayer_flick.json``: intended-account availability, bindings, margins.
* ``tests/native/test_prayer_planner.c``, ``tests/native/test_combat.c`` and
  ``tests/python/test_prayer.py``: policy, acquisition and FFI checks.

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
timestamps manual, unmodified F1 (melee protection) and F2 (Soul Split) down
edges while the attached RuneScape process has focus. The observed overhead
comes from the native prayer flags. An unknown overhead is JSON null, and a
sampled key press is recorded separately from any observed prayer transition.
Very short key presses can fall between samples.

Rows include all observed Telos action applications, current animation, life
points and native sample times. Compare a cue, key press, overhead transition
and resulting hit across multiple attacks. An HP change alone cannot establish
the server's prayer check or distinguish overlapping hits.

To try the requested 600 ms rule on successive melee autos::

    python -m rs3_reader.telos_prayer --trial

With the game in the foreground, the trial waits for a fresh ``28929`` event
from Telos. It presses F1 if melee protection is inactive and waits to observe
melee protection. It attempts F2 on the first fresh sample at least 600 ms after
the cue when melee protection has been confirmed. It skips F2 if Soul Split is
already active. Another matching cue before the return restarts the 600 ms
interval. The trial runs until Ctrl+C unless ``--duration`` sets a limit.

The trial waits up to 1.2 seconds for the game to report the requested overhead.
A cue received while Soul Split is pending can start the next cycle if it is
still fresh when Soul Split appears. A timed-out key, input failure or loss of
reliable observations skips the affected cycle. The trial waits for a fresh
cue and a known overhead before starting another. The trace records key
dispatch, overhead acknowledgement and skipped cycles separately. The 600 ms
interval is an experimental setting supplied by the player.

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

The observed protection transitions used VARP 3272 values 64, 128 and 256.
The other rows above are definition-derived and fixture-tested, with their live
validation still outstanding. The selected prayer book is not inferred from the
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

The sampler and general prayer observer only collect data. The explicit
``telos_prayer.py --trial`` mode uses this function for experimental switch cycles.
An input failure abandons the cycle and applies the same cooldown as an
unconfirmed key before the trial considers another cue.

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
* Measured key-to-observed-overhead latency, tick alignment and live Soul Split
  acknowledgement on the intended account.

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

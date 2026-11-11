RS3 live tracker
================

Direct memory telemetry in a local desktop window.

Start
-----

Build the C library with CMake; see the repository README for the commands.
Double-click ``Start Tracker.cmd`` in the repository root or run the tracker from
the command line. The launcher puts ``python/`` on ``PYTHONPATH``, uses
``pythonw.exe`` from ``C:\Python39`` or ``PATH`` and forwards command-line
options. The tracker picks a visible RS3 window by default; use ``--pid ID`` to
select a specific process. Close the window to stop its Python consumer and the
native worker. No pip packages, injected game agent or network server are
required.

Collection
----------

The C worker samples at 100 Hz by default. UI and log output run at 20 Hz::

    $env:PYTHONPATH = "python"
    python -m rs3_reader.tracker --native-hz 500

Requested rates depend on system load. A faster reader does not increase the
game's server update rate. The raw/source tab shows engine timing and event gaps.
``RS3_NATIVE_LIBRARY`` can select an alternative DLL.

Compatibility Mode client 6ada3f59-1a86000
------------------------------------------

This profile displays the live player tile alongside combat and inventory data.
The JSONL ``position`` field gives ``x`` (east), ``y`` (north) and ``plane``
(0–3). A null position indicates that the tile is unavailable.

Verified fields in the pinned 951-3 builds
------------------------------------------

* HP and maximum HP: direct VARP 13537/13538.
* Prayer: VARP 3274 bits 0..14 / 10; the UI displays the integer part.
* Maximum prayer: base Prayer level × 10 (derived, not a separate variable).
* Adrenaline: VARP 679 / 10; previously observed changing from 0 to 100% in combat.
* GCD: VARC 2091/2092 against the client logic clock; five live starts
  previously confirmed 90 ticks × 20 ms = 1.8 s.
* NPCs: name, config ID/type, runtime ID/instance, current and maximum HP from
  numeric statistics. Goblin 550/550, Giant spider 1000/1000, Goblin 550 -> 326
  alongside the user's independently reported 224 hit. Also captured 550/1000.
* Hit splats: raw type ID, amount, recipient, start/end client ticks, duration.
  Filters and history are available. Wounded NPCs appear before healthy ones.
* Telos phases and animations remain unconfirmed.

Inventory, equipment and abilities
----------------------------------

Tabs show all 28 backpack slots, equipped gear and individual cooldowns. Slots
are 1..28 in the UI and zero-based in JSON and C; the four-column grid is a
logical view and does not measure screen coordinates.

Item names, IDs, quantities, explicit potion families/doses, moves, replacements,
equipment transfers and dose changes come from the C collector. A token follows
unambiguous changes; identical duplicates can be ambiguous. Saradomin brew flask
(6) ID 23351 -> (5) ID 23353 retains its healing family. Super restore ID 3028 ->
3030 was verified live with the same tracking token.

The 169 ability definitions include 140 client timer mappings. Hurricane, Flurry
and Resonance were checked live with two-handed, dual-wield and shield setups.
Unknown cooldowns remain unknown; a gear match is not full castability. Bone
Shield substitution, runes, unlocks and other cast conditions are separate
checks. See ``inventory-and-abilities.rst`` and
``validation/inventory-validation-summary.json``. The build generates this
metadata (``rs3_catalog.bin``) beside the DLL when the game cache is installed.
Missing metadata leaves raw IDs and positions available, with explicit unknowns,
and the Inventory and Abilities tabs show the reason.

Auditability
------------

Samples go to ``python/rs3_reader/logs/capture-DATE-TIME-PID.jsonl`` at 20 Hz.
Logs rotate at about 25 MB with three backup parts. Each record contains the
acquisition time, sequence, PID, executable hash, raw and decoded values, read
duration and native diagnostics. Buttons open the log folder and copy the
current JSON. Data is read directly from memory: only
``PROCESS_QUERY_INFORMATION | PROCESS_VM_READ`` access is requested, no game
functions are called, no game memory is written and no keys or clicks are sent.
All source, comments, UI and documentation use English.

Limitations
-----------

* Supported hashes and loaded-code signatures are listed in
  ``profiles/profile_*.json``. Both are checked before offsets are used.
* Failed reads and process loss produce unavailable fields. Stale samples are
  hidden in the UI. Logout and world changes still need controlled live
  validation; process existence and an advancing client clock do not prove a
  logged-in state.
* ``last_change_age_ms`` is the time since the last observed value change, not
  the age of server data. The latest server update time is unknown.
* A snapshot is not atomic. NPCs cover the loaded scene, without a distance check.
* Untransmitted NPC HP is null/``not_transmitted``, not zero HP.
* Hit mark amounts may be healing, zero or text marks; the attacker is unknown.
* ``newly_observed`` means first observed, including marks already active at
  attachment. The native queue holds 4096 observations and reports
  ``events_lost`` on overwrite. Polling can miss records overwritten or removed
  between native samples.

Command line and tests
----------------------

::

    $env:PYTHONPATH = "python"
    python -m rs3_reader.jsonl_stream --once --raw
    python -m rs3_reader.jsonl_stream --native-hz 500 --hz 20 --duration 60 --raw --output capture.jsonl
    python -m rs3_reader.tracker --history PATH_TO_EARLIER_CAPTURE.jsonl
    ctest --test-dir build -C Release --output-on-failure

CTest runs the deterministic C fixtures and the Python tests, including DLL ABI
checks. The reports under ``docs/validation/`` describe earlier live results and
their limits. The C port was checked against the Python reference reader; full
Telos combat remains untested. Visual inspection through Computer Use was
previously denied. Window creation and real capture logs were verified; no
visual inspection is claimed.

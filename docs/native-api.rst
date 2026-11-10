RS3 C telemetry library
=======================

``rs3_native.dll`` is the read-only Windows x64 acquisition foundation. It owns
process discovery, SHA-256/signature validation, remote reads, structure decoding,
reconnection, high-frequency sampling and hit observation deduplication. Consumers
receive typed snapshots through a plain C ABI. Python remains responsible for
presentation, JSON serialization and the desktop UI. No callbacks into Python or
Java are needed on the collector thread.

Foreground input
----------------

The additive ABI-v1 exports ``rs3_move_to_tile``, ``rs3_activate_ability`` and
``rs3_interact`` send Windows input to the foreground client. Each call takes an
immutable snapshot from ``rs3_sample`` or ``rs3_copy_latest``. Take a fresh
snapshot for each action. The DLL accepts verified samples up to one second old
and checks the foreground window's PID and process creation time before dispatch.
Mouse actions also check that the pixel is in the unobscured client area. Use
physical desktop pixels, including negative coordinates on secondary monitors.
Sampling keeps read-only process access.

**Movement.** On the Compatibility Mode profile, copy the player's
``rs3_position`` into ``rs3_minimap.anchor``. Measure the center of that tile on
the minimap. Measure the pixel displacement to known tiles due east and due north,
then divide each displacement by its tile separation to get the two basis
vectors. Choose a radius that excludes minimap controls and edges. For example,
if the player is at (3200, 3200, 0), the minimap is north-up, and each tile is
four pixels wide::

    rs3_minimap map = {
        .anchor = player_tile,
        .screen_x = 1000, .screen_y = 200,
        .east_dx = 4, .north_dy = -4,
        .radius_pixels = 60
    };
    int32_t result = rs3_move_to_tile(snapshot, 3203, 3202, 0, &map);

This projects (3203, 3202) to pixel (1012, 192). Recalibrate if the minimap
scrolls, rotates, zooms or changes layout. The destination must be on the player's
plane and within the calibrated circle. The game handles routing from the click.

**Abilities.** Build the local catalog and find the ability's ``struct_id`` in
``rs3_snapshot_abilities``. Place that ability on the active action bar and
configure an unmodified A–Z, 0–9 or F1–F12 shortcut in the game. For an ability
bound to Q, call ``rs3_activate_ability(snapshot, struct_id, 'Q')``. The caller
supplies the binding and checks that the game is ready to receive the shortcut.
The game applies cooldown, resource and targeting rules.

**Entities.** Provide the NPC's ``instance_id`` from the snapshot and the screen
pixel over the visible entity. Pass zero for a scene object such as a bank
booth or chest. Use ``RS3_ENTITY_DEFAULT`` for a left click or
``RS3_ENTITY_OPEN_MENU`` for a right click. Once the menu is visible, take a
fresh snapshot and call ``rs3_interact`` with ``RS3_ENTITY_SELECT_OPTION`` and
the pixel over the desired **Talk-to**, **Bank** or other menu row. The caller
resolves the entity's screen position and the option's visible location.

``RS3_OK`` reports that Windows accepted the input events. Verify movement,
ability use, dialogue or the bank interface through subsequent observations.
``RS3_E_ARGUMENT`` indicates invalid parameters. ``RS3_E_STATE`` covers stale or
unavailable samples, mismatched minimap anchors and held input. ``RS3_E_TARGET``
covers missing IDs, off-map destinations and foreground or click-target
mismatches. ``RS3_E_OS`` covers Windows input failures, which can include
partially submitted events. Keep the client foreground and the visible UI stable
while making a call. Windows dispatches through the desktop input stream.
`Microsoft SendInput documentation <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput>`_.

Build
-----

See the repository README for the CMake configure, build and test commands.
Python 3.9+ generates the private header from the pinned JSON profiles at build
time. The DLL links Windows system libraries, including BCrypt. C and Java
consumers can load it directly without a Python runtime.

Install the DLL, import library, header and CMake package to a local prefix::

    cmake --install build --config Release --prefix build/install

A separate CMake project can set ``CMAKE_PREFIX_PATH`` to that prefix and use::

    find_package(rs3_native 1 CONFIG REQUIRED)
    target_link_libraries(my_app PRIVATE rs3::native)

Include ``<rs3/rs3.h>`` and put the DLL beside the consuming executable or on
its runtime PATH. Use a matching Windows x64 compiler and import library.

Consumers
---------

Python (standard library only)::

    python examples/read.py
    python python/rs3_reader/reader.py --native-hz 500 --hz 20 --duration 10 --raw
    python python/rs3_reader/tracker.py

``python/rs3_reader/native.py`` is the reusable typed ctypes binding.
``Reader(worker_hz=100)`` starts the native collector; ``worker_hz=0`` samples
synchronously. ``reader.sample()`` returns typed info/player/NPC/hit buffers.
They are views of reusable storage: copy values that must survive another sample.
``reader.drain_events()`` returns owned event copies and an overflow count.
Use a context manager or ``close()``. Each Reader must be confined to one Python
thread. ctypes CDLL calls release the GIL; the native worker does not acquire it.
The adapter in ``native_telemetry.py`` preserves the previous JSON/tracker schema
and adds ``backend: c``, native read metrics, sample sequence, age and event gaps.
Existing profile and executable hashes are checked before returning game data.
Both pinned executable profiles provide combat, inventory and ability telemetry.
The ``6ada3f59-1a86000`` profile also provides player tiles.
The original executable additionally provides optional local-player hitmarks
through ``rs3/combat.h``, with an independent event cursor and explicit overflow
count. See `Prayer timing <prayer-flick.rst#local-player-hitmarks>`_ for layouts,
identity guards, Python accessors and limitations.

C example from the Visual Studio build::

    build\bin\Release\read_example.exe

It loads the DLL from the executable directory, resolves the public exports,
starts a 100 Hz collector and reads snapshots at 10 Hz. Optionally pass an
absolute DLL path and a duration in seconds. C/C++ projects may instead link the
import library from their compiler/CMake build and include ``<rs3/rs3.h>``.
Public declarations use ``extern "C"`` for C++ consumers.

Java **22+** uses the stable Foreign Function & Memory API, with no JNI bridge
or JNA dependency. The included AutoCloseable ``Rs3Reader`` class was compiled
and run with **JDK 25** against the live DLL::

    javac -d build/java examples/Rs3Reader.java
    java --enable-native-access=ALL-UNNAMED -cp build/java Rs3Reader build/bin/Release/rs3_native.dll 5

Use a 64-bit Windows JVM. ``Rs3Reader.sample()`` returns Java-owned immutable
records for player values, NPCs, hitmarks and newly observed events.
``Rs3Reader.inventory()`` returns items, equipment, abilities and item changes,
using its own independent item-event cursor. Keep this
reader on its creating thread and use try-with-resources. ``close()`` destroys
the native context (joining its worker) before unloading the library. The API
also works through JNA/JNI on older Java versions, but no Java 17 bridge is
bundled. Official FFM references:
https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/lang/foreign/Linker.html
and https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/lang/foreign/SymbolLookup.html.

ABI, ownership and concurrency
------------------------------

The public contract is ``../include/rs3/rs3.h``. ABI version **1**, Windows x64 C
calling convention; fixed-width integer fields, default 8-byte alignment and
UTF-8, NUL-terminated strings. Do not apply packed struct layout. ABI-v1 struct
layouts are fixed. Validate ``rs3_abi_version()`` and ``rs3_sizeof()`` before
reading buffers. A future incompatible layout requires a new ABI version.

``rs3_context`` and ``rs3_snapshot`` are opaque, library-owned allocations.
Destroy them only with ``rs3_destroy`` / ``rs3_snapshot_destroy``. Array output
buffers belong to the caller; no CRT allocator crosses the language boundary.
Borrowed result/profile strings are immutable and valid while the DLL is loaded.

* ``rs3_create(options, &context)``: NULL options use defaults. PID 0 selects the
  sole client process or, with several processes, prefers the foreground game
  window and then the visible game window with the lowest PID. A nonzero PID
  selects that process directly. Set ``struct_size`` to ``sizeof(rs3_options)``
  and reserved to zero. Retry 0 selects 1000 ms.
  No running/supported client is a sample availability condition, not a create error.
* ``rs3_snapshot_create(&snapshot)``: one caller-owned snapshot per consumer.
* ``rs3_sample(context, snapshot)``: synchronous acquisition. Mutually exclusive
  with a running native worker (returns ``RS3_E_STATE``).
* ``rs3_start(context, hz)``: requested 1..1000 Hz, dedicated Windows thread and
  high-resolution waitable timer when available, normal timer fallback.
* ``rs3_copy_latest(context, snapshot)``: copies the latest frame without sampling.
  Before the first worker result, status is ``RS3_NO_SAMPLE``.
* ``rs3_snapshot_position(snapshot, &position, sizeof position)`` copies the
  tile. Read the coordinates only when ``position.valid`` is 1. If a verified
  tile is available during a combat read failure, the snapshot is partial and
  the combat fields are unavailable.
* ``rs3_snapshot_client_version(snapshot)`` returns the full version label as a
  borrowed string. ``rs3_info.client_version`` holds a short label.
* ``rs3_snapshot_info/player``: supply an output struct and its size. Too-small
  output returns ``RS3_E_ABI``. Snapshot NPC/hit accessors report required counts;
  too-small capacity returns ``RS3_E_BUFFER`` and writes no array records.
* ``rs3_events_since``: independent event cursor; details below.
* ``rs3_stop``: stops and joins, idempotent. ``rs3_destroy`` also stops the worker.
  NULL destroys are safe. No API call may race context destruction/library unload.

Lifecycle calls serialize on a context. The worker publishes under a separate
lock, so consumers can copy snapshots/drain events while it runs. A snapshot
belongs to one consumer at a time: externally serialize operations on the same
snapshot. Returned arrays are copies, without pointers into the target process.
There are no foreign callbacks or target pointers to dereference.

``RS3_OK`` means the API operation succeeded; it does not mean game data exists.
Inspect ``info.status`` (no_sample/available/partial/unavailable) and ``info.reason``.
Unavailable frames clear player validity and NPC/hit counts. Skipped NPCs remain
absent with an explicit count. Consumer/argument/allocation errors are negative
``rs3_result`` values, described by ``rs3_result_string``.

Layouts and units
-----------------

``rs3_sizeof`` structure IDs and expected sizes:

============ ========== =====
ID           Structure  Bytes
============ ========== =====
1            options    16
2            info       384
3            player     136
4            npc        568
5            hit        40
6            event      608
7            inventory  168
8            item       192
9            ability    192
10           item_event 424
11           position   16
============ ========== =====

Foreign bindings can use the header directly. The included Java example documents
field offsets in executable code and verifies all six sizes. Important offsets:
info sequence 16, wall time 24, monotonic time 32, read duration 40, logic tick 80,
message 192; player validity 0, ten int64 raw values 8, prayer base 120, GCD
remaining/duration 124/128 and reason 132; NPC name 48; event hit 48, name 88.

``timestamp_ns`` / ``observed_at_ns`` are Unix epoch nanoseconds. Durations and
change times use the DLL's QPC clock; use ``rs3_monotonic_ns() - info.monotonic_ns``
for sample age. Foreign monotonic clocks may use different origins (notably
Python versions). ``process_creation_time`` is the Windows FILETIME identity
(100 ns units since 1601), not Unix time. Sequence counters belong to a context
and increase across reconnects. Samples identify PID and process creation time.

``valid_variables`` bit i controls ``raw_variables[i]``. Invalid entries contain
zeroed storage but must be treated as unknown, never numeric zero:

===== ========================== ====================================
Index Variable                   Interpretation
===== ========================== ====================================
0/1   VARP 13537 / 13538          Current / maximum life points
2     VARP 3274                  Prayer = bits 0..14 / 10
3     VARP 679                   Raw / 10 = adrenaline percent
4/5/6 VARP 4501 / 4502 / 9847     Server GCD start/end/duration
7/8   VARC 2091 / 2092            Client GCD start/end ticks
9     VARP 6363                  Telos stage bits 21..23, unverified
===== ========================== ====================================

Maximum prayer is derived from ``prayer_base_level * 10``. GCD milliseconds are
valid only with available player data and ``gcd_reason == RS3_REASON_NONE``.
Clock/tick arithmetic wraps at uint32, using signed differences; 20 ms per tick.
Change timestamps measure observed changes, not the age of server information.

NPC ``flags`` independently validate name/config/health. HP max 0 means not
transmitted; current 0 with a positive max is a valid zero-HP NPC. Runtime ID is
the loaded index; config ID identifies type. ``instance_id`` is assigned locally
and changes after an observed despawn, actor replacement or world-context change.
An unobserved recycle between polls cannot always be distinguished. NPC hit slices
refer to the snapshot's flat hit array via ``hit_offset`` and ``hit_count``.
Hit state is active or scheduled; type/secondary IDs remain raw. A secondary type
below zero means absent. Do not assume all amounts are damage or infer attacker.

Event queue and sampling limits
--------------------------------

Each consumer keeps ``after=0`` initially and calls ``rs3_events_since``. Advance
to ``next`` after processing the returned array. ``lost`` reports overwritten
sequence numbers before the oldest retained record. Zero-capacity calls query
the gap without returning records. The queue holds **4096** observations. Each
event carries its own original observation time, PID/process identity, recipient
identity, raw hit and sequence, even when consumed after a reconnect.

First observation includes already-active hits at attachment. Scheduled marks are
emitted only once they become active. Stable active slot records are deduplicated
per NPC instance. A separate queue keeps observations across slower UI refreshes;
it cannot recover game records overwritten between native polls. Event draining
and latest snapshot copying are separate operations, so events may belong to a
newer or older frame than the copied snapshot. Use the event's own identity/time.

Steady-state allocations are bounded: 1024 NPCs, 64 marks each, fixed snapshot and
event buffers; bucket storage grows only when its validated capacity grows. Actor
fields are decoded from one bulk read per NPC, reducing kernel calls. A larger
byte count can be cheaper than many small ReadProcessMemory calls. Reads and
consistency checks are still subject to concurrent game updates. Requested polling
frequency is not a real-time guarantee and does not change the game's update rate.
Logout state and Telos classification remain separate, documented research gaps.

Validation
----------

``tests/test_native.c`` uses private fake-memory fixtures by including the
implementation, without exporting a testing/injection interface. Eleven groups test
decoding, sparse sentinels/int64, profile mismatch, cyclic chains, unknown/zero HP,
expiry/scheduling/wrap, torn rings, world/list transitions, NPC reuse, deduplication,
buffer errors, two consumer cursors, overflow, failed-frame invalidation, missing
process and worker lifecycle.
The inventory groups additionally cover moves, dose changes, quantity/replacement,
duplicate ambiguity, gear switches/defenders, individual timers, catalog validation
and item-event queue gaps. There are 30 Python tests in the combined suite,
including the independently added player-tile tests.
Assertions remain enabled in optimized test builds. Python tests verify the actual
DLL ABI/lifecycle and preserve observation identity/time through UI coalescing.

``work/benchmark_native.py`` compares the C adapter to Python, bracketed by two
Python reads to exclude changing scenes. The compact ``validation/native-validation.json``
records actual timings, parity and achieved worker rates. The measured scene can
change during rate tests; each rate reports its final loaded NPC count. These
numbers are observations on this machine, not a guarantee for boss combat.

Inventory and ability extension
-------------------------------

The additive ABI-v1 extension preserves all original struct layouts and exports.
``rs3_snapshot_inventory_info`` reports separate inventory/catalog status and
counts. ``rs3_snapshot_items`` selects both containers (0), backpack (93) or
equipment (94). All 47 slots, including empty cells, are represented. Empty
``item_id == -1`` is distinct from an unavailable container (count zero/status).
``rs3_snapshot_abilities`` returns definitions and live timer observations;
``flags & 1`` validates the live timer, ``flags & 2`` marks an ability-book entry,
and ``flags & 4`` validates its base cooldown. Never interpret zeroed unavailable
timer storage as ready. ``equipment_match`` checks weapon setup, not full castability.

``rs3_item_events_since`` uses a separate caller cursor and a 1024-entry queue.
Before/after records describe added, removed, moved, replaced, quantity-changed
and dose-changed observations. A local inferred ``token`` follows unambiguous
matches; ambiguity is flagged. No client-side unique serial number is assumed.
Events retain their original process identity/time through slower consumer polls.

The C context tries ``rs3_catalog.bin`` beside its loaded module. The standard
build generates it from the local cache in SQLite read-only mode; use
``--cache-root`` to select another installation and ``--skip-catalog`` to keep
an existing catalog. ``rs3_load_catalog(context, utf8_path)`` loads a selected file
while the worker is stopped. A failed reload retains an already valid catalog.
Missing metadata does not prevent raw item IDs/quantities/slots from being read.
Names/families/ability definitions remain explicitly unavailable. Generated
catalogs and cache files stay local; the repository contains the decoder and
compact definition bindings only.

Our catalog interchange format uses little-endian words (not game cache bytes):

* Header: 8-byte ``RS3CAT1\0``, uint32 format=1, item count, ability count,
  string-table byte count, 32 raw bytes of executable SHA-256, 32 raw bytes of
  the following body's SHA-256. Total 88 bytes.
* Items: sorted by ID, 40 bytes each. Ten 32-bit words: item ID, name offset,
  raw potion family ID, signed doses, signed next-dose item ID, signed equip
  slot, combat style, equipment flags, effect flags, base flat heal. Unknown
  doses/next/equip slot are -1. Family ID zero can be valid when doses are known.
* Abilities: sorted by struct ID, 48 bytes each. Twelve 32-bit words: struct ID,
  name offset, signed start/end VarClient IDs, base cooldown ms, requirements,
  combat style, timer source (1=client pair), level, raw adrenaline requirement,
  native ability ID, definition flags (ability-book=1, known base cooldown=2).
* Body ends with UTF-8 NUL-terminated strings. Offsets are relative to that table.
  Names copied into public item/ability structs use a 128-byte UTF-8 buffer.

The loader bounds file size/counts/offsets, checks hashes and rejects invalid
timer indices. A checksum verifies integrity, not trust in an externally supplied
catalog. The public item struct is 192 bytes (name at 60); ability 192 (name at
64); inventory info 168 (message at 40); item event 424 (before at 40, after at
232). Python validates all layouts; the Java example reads the same fields.

``inventory-and-abilities.rst`` documents the recovered paths,
slot conventions, dose/effect semantics, Bone Shield limits and live evidence.
``validation/inventory-performance.json`` measures the expanded C workload, including the
new inventory/equipment/ability sampling. The earlier ``validation/native-validation.json``
describes the original smaller C port workload.

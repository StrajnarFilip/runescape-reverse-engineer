RS3 C telemetry library
=======================

``rs3_native.dll`` is the read-only Windows x64 acquisition foundation. It owns
process discovery, SHA-256/signature validation, remote reads, structure decoding,
reconnection, high-frequency sampling and hit observation deduplication. Consumers
receive typed snapshots through a plain C ABI. Python remains responsible for
presentation, JSON serialization and the desktop UI. No callbacks into Python or
Java are needed on the collector thread.

Build
-----

Requires a Windows x64 C11 compiler and Python 3.9+ for profile generation.
From the repository root, in a compiler-enabled shell::

    python outputs/rs3_native/build.py --test

Use ``--cc path/to/compiler`` for MSVC cl, clang, gcc or Zig. The implementation
and examples were built and tested here using official Zig **0.15.2**, targeting
``x86_64-windows-gnu``, with ``-O2 -Wall -Wextra -Werror``::

    python work/bootstrap_native_tools.py
    python outputs/rs3_native/build.py --cc work/tools/zig-x86_64-windows-0.15.2/zig.exe --test

The bootstrap verifies the official archive's pinned SHA-256 and extracts only
under ``work/tools/``. It does not change PATH or install a game agent. Standard
MSVC/Clang/GCC build paths are provided but were not exercised on this machine.
Build output (ignored by Git): ``outputs/rs3_native/build/``. Windows will prevent
replacing a DLL while a consumer has it loaded; close consumers before rebuilding.

CMake is an alternative with an installed compiler/SDK::

    cmake -S outputs/rs3_native -B outputs/rs3_native/build/cmake -A x64
    cmake --build outputs/rs3_native/build/cmake --config Release
    ctest --test-dir outputs/rs3_native/build/cmake -C Release --output-on-failure

For that build, pass the actual DLL path to consumers or set
``RS3_NATIVE_LIBRARY``; CMake's configuration directory differs from build.py.
The C implementation links only Windows system libraries (including BCrypt).
It does not depend on a Python/Java runtime or any third-party game agent.

Consumers
---------

Python (standard library only)::

    python outputs/rs3_native/examples/read.py
    python outputs/rs3_reader/reader.py --native-hz 500 --hz 20 --duration 10 --raw
    python outputs/rs3_reader/tracker.py

``outputs/rs3_reader/native.py`` is the reusable typed ctypes binding.
``Reader(worker_hz=100)`` starts the native collector; ``worker_hz=0`` samples
synchronously. ``reader.sample()`` returns typed info/player/NPC/hit buffers.
They are views of reusable storage: copy values that must survive another sample.
``reader.drain_events()`` returns owned event copies and an overflow count.
Use a context manager or ``close()``. Each Reader must be confined to one Python
thread. ctypes CDLL calls release the GIL; the native worker does not acquire it.
The adapter in ``native_telemetry.py`` preserves the previous JSON/tracker schema
and adds ``backend: c``, native read metrics, sample sequence, age and event gaps.
Existing profile and executable hashes are checked before returning game data.

C example (built by build.py)::

    outputs\rs3_native\build\read.exe

It loads the DLL from the executable directory, resolves the public exports,
starts a 100 Hz collector and reads snapshots at 10 Hz. Optionally pass an
absolute DLL path and a duration in seconds. C/C++ projects may instead link the
import library from their compiler/CMake build and include ``<rs3/rs3.h>``.
Public declarations use ``extern "C"`` for C++ consumers.

Java **22+** uses the stable Foreign Function & Memory API, with no JNI bridge
or JNA dependency. The included AutoCloseable ``Rs3Reader`` class was compiled
and run with **JDK 25** against the live DLL::

    javac -d outputs/rs3_native/build outputs/rs3_native/examples/Rs3Reader.java
    java --enable-native-access=ALL-UNNAMED -cp outputs/rs3_native/build Rs3Reader C:/absolute/path/rs3_native.dll 5

Use a 64-bit Windows JVM. ``Rs3Reader.sample()`` returns Java-owned immutable
records for player values, NPCs, hitmarks and newly observed events. Keep this
reader on its creating thread and use try-with-resources. ``close()`` destroys
the native context (joining its worker) before unloading the library. The API
also works through JNA/JNI on older Java versions, but no Java 17 bridge is
bundled. Official FFM references:
https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/lang/foreign/Linker.html
and https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/lang/foreign/SymbolLookup.html.

ABI, ownership and concurrency
------------------------------

The public contract is ``include/rs3/rs3.h``. ABI version **1**, Windows x64 C
calling convention; fixed-width integer fields, default 8-byte alignment and
UTF-8, NUL-terminated strings. Do not apply packed struct layout. ABI-v1 struct
layouts are fixed. Validate ``rs3_abi_version()`` and ``rs3_sizeof()`` before
reading buffers. A future incompatible layout requires a new ABI version.

``rs3_context`` and ``rs3_snapshot`` are opaque, library-owned allocations.
Destroy them only with ``rs3_destroy`` / ``rs3_snapshot_destroy``. Array output
buffers belong to the caller; no CRT allocator crosses the language boundary.
Borrowed result/profile strings are immutable and valid while the DLL is loaded.

* ``rs3_create(options, &context)``: defaults when options is NULL; PID 0 discovers
  the unique client. Multiple clients need an explicit PID. ``struct_size`` must
  equal ``sizeof(rs3_options)``; reserved must be zero. Retry 0 selects 1000 ms.
  No running/supported client is a sample availability condition, not a create error.
* ``rs3_snapshot_create(&snapshot)``: one caller-owned snapshot per consumer.
* ``rs3_sample(context, snapshot)``: synchronous acquisition. Mutually exclusive
  with a running native worker (returns ``RS3_E_STATE``).
* ``rs3_start(context, hz)``: requested 1..1000 Hz, dedicated Windows thread and
  high-resolution waitable timer when available, normal timer fallback.
* ``rs3_copy_latest(context, snapshot)``: copies the latest frame without sampling.
  Before the first worker result, status is ``RS3_NO_SAMPLE``.
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
implementation, without exporting a testing/injection interface. Seven groups test
decoding, sparse sentinels/int64, profile mismatch, cyclic chains, unknown/zero HP,
expiry/scheduling/wrap, torn rings, world/list transitions, NPC reuse, deduplication,
buffer errors, two consumer cursors, overflow, failed-frame invalidation, missing
process and worker lifecycle.
Assertions remain enabled in optimized test builds. Python tests verify the actual
DLL ABI/lifecycle and preserve observation identity/time through UI coalescing.

``work/benchmark_native.py`` compares the C adapter to Python, bracketed by two
Python reads to exclude changing scenes. The compact ``validation-summary.json``
records actual timings, parity and achieved worker rates. The measured scene can
change during rate tests; each rate reports its final loaded NPC count. These
numbers are observations on this machine, not a guarantee for boss combat.

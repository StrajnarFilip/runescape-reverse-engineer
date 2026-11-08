RS3 native memory reader and live tracker
========================================

A local, read-only RuneScape 3 telemetry project. A C11 DLL discovers and verifies
``rs2client.exe``, reads player variables and NPC structures, and publishes typed
snapshots and hit observations. The Tkinter UI and JSON Lines output remain in
Python. All project source, comments, interface text and documentation use English.

Current capabilities
--------------------

* Version-pinned Windows x64 client **951-3**, executable SHA-256 and loaded-code
  signatures checked before using offsets.
* Player current/max HP, prayer (maximum derived from base level), adrenaline,
  and global cooldown using the client's 20 ms logic clock.
* Loaded NPC names, config/type IDs, runtime IDs, exact transmitted current/max HP.
* Native hitmark rings: recipient, amount, raw type, start/end ticks, local history.
* Native collector: configurable **1..1000 Hz**, default **100 Hz**. Python UI and
  log refresh independently at 20 Hz. Actual throughput depends on system load.
* Stable, documented C ABI with explicit ownership, validity flags and event gaps.
  Working Python ctypes and Java FFM consumers; no language-specific game agent.

Previously verified in live combat: Goblin **550/550 HP**, Giant spider
**1000/1000 HP**, and Goblin **550 -> 326 HP** alongside the independently reported
**224** hit. The same capture includes reported hits of 550 and 1000. The C port's
parity/timing evidence is in ``outputs/rs3_native/validation-summary.json``; earlier
combat evidence remains in ``outputs/rs3_reader/npc-validation-summary.json``.
Telos animations/phases remain **unconfirmed**. Controlled logout, world-hop and
restart checks are still needed. Sampling frequency is not server update frequency.

Build and run
-------------

Use Windows x64, a C compiler, and 64-bit Python with Tkinter (no pip packages).
From the repository root, with a compiler on PATH::

    python outputs/rs3_native/build.py --test
    python outputs/rs3_reader/tracker.py
    python outputs/rs3_reader/reader.py --once --raw

The build script accepts MSVC, Clang, GCC or Zig. On this development machine::

    C:\Python39\python.exe outputs/rs3_native/build.py --cc work/tools/zig-x86_64-windows-0.15.2/zig.exe --test

``work/bootstrap_native_tools.py`` downloads the official pinned Zig release and
checks its SHA-256. It does not install a game agent or modify the game. Downloaded
tools and build products remain local. Native build instructions, ABI contracts,
C/Python/Java examples and field units: ``outputs/rs3_native/README.rst``.

Double-click ``outputs/rs3_reader/Start Tracker.cmd`` on this machine, or use::

    python outputs/rs3_reader/tracker.py --native-hz 500
    python outputs/rs3_reader/reader.py --native-hz 500 --hz 20 --duration 60 --raw --output capture.jsonl
    python outputs/rs3_reader/tracker.py --history capture.jsonl

The original Python decoder remains available explicitly for reference/comparison::

    python outputs/rs3_reader/tracker.py --backend python
    python outputs/rs3_reader/reader.py --backend python --once --raw

Alternatively set ``RS3_BACKEND=python``. A missing or mismatched native library
produces an explicit error rather than silently selecting a different engine.
``RS3_NATIVE_LIBRARY`` can select a DLL at a different absolute path.
The tracker rotates logs at about 25 MB, with three backups per run; buttons open
the log directory or copy the latest JSON. Both implementations request only
``PROCESS_QUERY_INFORMATION | PROCESS_VM_READ``. No game calls, writes or inputs.

Layout and verification
-----------------------

* ``outputs/rs3_native/include/rs3/rs3.h``: public C ABI.
* ``outputs/rs3_native/src/rs3_native.c``: process lifecycle, decoder, worker, events.
* ``outputs/rs3_native/tests/``: deterministic C fixtures and lifecycle checks.
* ``outputs/rs3_reader/native.py``: typed ctypes binding; no remote-memory decoder.
* ``outputs/rs3_reader/native_telemetry.py``: C observations to tracker/JSON schema.
* ``outputs/rs3_reader/profile_951_3.json``: single source for hash/signatures/offsets.
* ``outputs/rs3_reader/winmem.py``, ``rs3.py``, ``entities.py``: Python reference backend.
* ``outputs/rs3_reader/reader.py``, ``tracker.py``: JSONL CLI and desktop UI.
* ``outputs/rs3_reader/research-notes.rst``: recovered structures and open questions.
* ``outputs/rs3_reader/*validation-summary.json``: earlier actual live measurements.
* ``work/*.py`` and ``work/ghidra_scripts/``: our research tools.

Checks::

    python outputs/rs3_native/build.py --test
    python -m unittest discover -s outputs/rs3_reader -v
    python work/benchmark_native.py

The last command is an opt-in live parity/timing check; it needs the supported
client and records only a compact report. C tests cover sparse tables, signed
variants, profile refusal, clock wrap, NPC stats, torn rings, world/list transitions,
identity reuse, deduplication, independent cursors, overflow and worker lifecycle.
The Python suite additionally checks the actual DLL ABI and UI history handling.
A clean checkout without a built DLL skips binding tests until after the C build.

Limits
------

A snapshot is a guarded sequence of reads, not an atomic process dump. A running
client/advancing clock does not establish logged-in state. Unknown values remain
null with reasons. NPCs represent the loaded scene, not a measured distance.
Hitmarks can include healing, zero amounts and text marks; their raw type IDs are
retained. The recipient is known; the attacker is not. Polling can miss records
removed between samples. The 4096-entry native event queue reports overwritten
observations explicitly; it cannot recover records the collector never observed.
The UI shows observation time, not a guaranteed stream of all server hits.
Visual window inspection through Computer Use was denied previously; window
creation and actual log output were checked. This change does not claim visual QA.

Research tools and synchronization
----------------------------------

``work/bootstrap_tools.py`` fetches pinned official Ghidra/JDK tools with hash
checks. Static PE research scripts use ``pefile==2024.8.26`` and
``capstone==5.0.6`` under ``work/vendor/``::

    python -m pip install --target work/vendor pefile==2024.8.26 capstone==5.0.6

``work/read_cache.py`` is an independent read-only local SQLite cache decoder.
``work/decompile.ps1`` expects the local Ghidra project
``work/ghidra_projects/RS3_Targeted`` with an imported client::

    .\work\decompile.ps1 -Name native_vars -Addresses '140c08610','140c08db0'

Generated decompilation, game binaries/cache, tools and raw gameplay logs stay
local. Research scripts produce candidates, not automatically verified offsets.
The user requested publishing completed project changes to this GitHub repository;
``AGENTS.md`` preserves that workflow. No background uploader is installed.

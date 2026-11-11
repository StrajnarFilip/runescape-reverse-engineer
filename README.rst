RS3 native memory reader and live tracker
========================================

A local RuneScape 3 telemetry project with read-only process access. A C11 DLL discovers and verifies
``rs2client.exe``, reads player variables and NPC structures, and publishes typed
snapshots and hit observations. The Tkinter UI and JSON Lines output remain in
Python. All project source, comments, interface text and documentation use English.

Current capabilities
--------------------

* Version-pinned Windows x64 **951-3** builds, including **6ada3f59-1a86000**
  in Compatibility Mode. The reader checks the executable SHA-256 and loaded-code
  signatures before using offsets.
* Live player tiles alongside combat telemetry on **6ada3f59-1a86000**.
* Player current/max HP, prayer (maximum derived from base level), adrenaline,
  and global cooldown using the client's 20 ms logic clock.
* Loaded NPC names, config/type IDs, runtime IDs, exact transmitted current/max HP.
* Native hitmark rings: recipient, amount, raw type, start/end ticks, local history.
* All 28 inventory slots and 19 equipment slots, with item IDs, quantities, names,
  dose families and observed move/replace/consume history.
* 169 ability definitions, 140 client timer mappings, and live equipment checks
  for dual wield, two-handed weapons and shields. Unknown values stay explicit.
* Native collector: configurable **1..1000 Hz**, default **100 Hz**. Python UI and
  log refresh independently at 20 Hz. Actual throughput depends on system load.
* Stable, documented C ABI with explicit ownership, validity flags and event gaps.
  Working Python ctypes and Java FFM consumers; no language-specific game agent.
* Opt-in C commands for minimap movement, action-bar abilities and visible entity
  options such as Talk-to and Bank. See `Foreground input <docs/native-api.rst#foreground-input>`_.
* Direct overhead-prayer flags, raw NPC animation sequences and a native NPC
  action event queue on both supported executables, plus a C prayer timing
  planner with Soul Split support. The original executable also provides a
  guarded local-player hitmark snapshot and event queue. See
  `Prayer timing <docs/prayer-flick.rst>`_ for the observer and initial Telos
  calibration; automatic flicking remains disabled pending validated deadlines.
  The opt-in controlled calibration runner records scripted Ctrl+F-key tests
  under Devotion; its findings are in ``docs/validation/telos-prayer-check-2026-11-11.json``.

Previously verified in live combat: Goblin **550/550 HP**, Giant spider
**1000/1000 HP**, and Goblin **550 -> 326 HP** alongside the independently reported
**224** hit. The same capture includes reported hits of 550 and 1000. The C port's
parity/timing evidence is in ``docs/validation/native-validation.json``; earlier
combat evidence remains in ``docs/validation/npc-validation-summary.json``.
Hurricane, Flurry and Resonance were also checked live with two-handed, dual-wield
and shield setups. Item moves and a restore dose change retained their tracking
tokens. See ``docs/inventory-and-abilities.rst`` and its compact
validation report. Two 300% phase-1 Telos captures establish four labelled
sequence associations and confirm all four configured curses, including Soul
Split. Other phases, attack coverage and prayer-check deadlines remain
unvalidated; see ``docs/validation/telos-prayer-calibration-2026-11-10.json``.
Controlled logout, world-hop and
restart checks are still needed. Sampling frequency is not server update frequency.

Build and run
-------------

Use Windows x64, CMake 3.20 or newer, a C11 compiler and 64-bit Python 3.9+
with Tkinter. With Visual Studio and the Desktop development with C++ workload
installed, run these commands from the repository root::

    cmake -S . -B build -A x64
    cmake --build build --config Release
    ctest --test-dir build -C Release --output-on-failure

For a single-config generator such as Ninja, configure from a shell with your
Windows x64 compiler on PATH::

    cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
    cmake --build build
    ctest --test-dir build --output-on-failure

Use a separate build directory when switching generators or compilers. The
tracker finds the DLL in ``build/bin/Release/`` for Visual Studio or
``build/bin/`` for Ninja. Set ``RS3_NATIVE_LIBRARY`` to the DLL's absolute path
when using a different directory or configuration.

When the game cache is installed, the build also generates item names and
ability metadata (``rs3_catalog.bin``) beside the DLL, and regenerates them after
a game cache update. Set ``-DRS3_CACHE_ROOT=C:/path/to/cache`` at configure time
to use a different cache, or ``-DRS3_BUILD_CATALOG=OFF`` to skip the step. The
generator reads the cache locally. Without the catalog, raw inventory IDs and
slots stay available and the tracker shows why metadata is unavailable.

Double-click ``Start Tracker.cmd`` or run the desktop UI and JSONL stream
directly. The ``rs3_reader`` package lives in ``python/``; put it on the import
path once per shell (PowerShell shown; use ``set PYTHONPATH=python`` in ``cmd``)::

    $env:PYTHONPATH = "python"
    python -m rs3_reader.tracker --native-hz 500
    python -m rs3_reader.jsonl_stream --native-hz 500 --hz 20 --duration 60 --raw --output capture.jsonl
    python -m rs3_reader.tracker --history capture.jsonl

The tracker selects a visible RuneScape window automatically. If several are
open, it prefers the foreground game window at attachment, then the visible
client with the lowest PID. Use ``--pid`` to select a specific process. In
PowerShell, replace ``12345`` with the ID from the game window's row::

    Get-Process rs2client | Select-Object Id, MainWindowTitle
    & ".\Start Tracker.cmd" --pid 12345

The tracker and CLI use the C collector. The independent Python decoder lives
under ``work/reference/`` for research and parity benchmarks.
The tracker rotates logs at about 25 MB, with three backups per run; buttons open
the log directory or copy the latest JSON. The collector requests only
``PROCESS_QUERY_INFORMATION | PROCESS_VM_READ``. No game calls, writes or inputs.

On client build **6ada3f59-1a86000**, the desktop tracker shows **Player Tile**
alongside life points, prayer, adrenaline, cooldowns, nearby NPCs and inventory.

JSONL records carry ``position`` with integer ``x`` (east), ``y`` (north), and
``plane`` (0–3). The tile is ``null`` when unavailable. For repeatable boss
markers, record tile offsets relative to a recognizable arena landmark and
locate that landmark each run.

Layout and verification
-----------------------

* ``include/rs3/`` defines the public C ABI (``rs3.h``, ``combat.h``, ``prayer.h``).
* ``src/`` contains the collector, process verification and gameplay decoders,
  one module per responsibility.
* ``python/rs3_reader/`` is the Python package: ctypes binding, telemetry
  records, command-line tools and the ``tracker`` subpackage.
* ``profiles/`` contains pinned client hashes, signatures, offsets and ability bindings.
* ``tests/native/`` contains deterministic C fixtures, one CTest executable per area;
  ``tests/python/`` contains the Python tests, including DLL ABI checks.
* ``examples/`` contains C, Python and Java consumers.
* ``tools/`` contains the build-time profile and optional catalog generators.
* ``docs/`` contains the API contract, research notes and compact validation reports.
* ``work/`` contains research scripts and opt-in live validation tools.

See `the native API guide <docs/native-api.rst>`_ for CMake installation and C, Python and Java usage,
and `the development guide <docs/development.rst>`_ for the module map, coding
conventions (Allman braces via ``.clang-format``, PEP 8 for Python) and checks.
The live combat benchmark in ``work/benchmark_native.py`` uses the original
``profile_951_3.json`` executable.

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

`Melee Telos research <docs/telos-melee-research.rst>`_ covers encounter mechanics,
PVME rotation assumptions, prayer timing, consumables, EoF contents and Lunar
defensives. It distinguishes published guidance from live-validated telemetry
and records outdated or conflicting guide values.

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

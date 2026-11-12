Development guide
=================

Repository layout
-----------------

::

    include/rs3/          Public C ABI: rs3.h, combat.h, prayer.h
    src/                  rs3_native.dll implementation (C11, Windows x64)
    tools/                Build-time generators: profile sources, metadata catalog
    profiles/             Version-pinned client profiles and ability bindings
    python/rs3_reader/    Python package: ctypes binding, telemetry, CLIs
    python/rs3_reader/tracker/  Desktop tracker (Tkinter)
    arduino/serial_keyboard/  Leonardo USB serial-to-keyboard and mouse sketch
    tests/native/         Deterministic C fixture tests, one executable per area
    tests/python/         Python unit tests, including DLL ABI checks
    examples/             C, Python and Java consumers
    docs/                 API contract, research notes, validation reports
    work/                 Research scripts; work/reference/ is the independent
                          Python reference decoder used for parity checks
    cmake/                CMake package configuration template

See `Arduino Leonardo serial keyboard and mouse <arduino-keyboard.rst>`_ for
build instructions, the serial protocol and the foreground game capture tool.

The `PVME melee Telos runner <pvme-telos-runner.rst>`_ documents
``rs3_reader.pvme_telos`` and ``rs3_reader.rotation_keys``: an editable guide
recipe with SendInput/Leonardo transports, manual checkpoints and a track-L
validation gate. It is separate from the roadmap's autonomous decision engine.

The `Telos automation roadmap <telos-automation/README.rst>`_ splits the
remaining work into one brief per agent under ``docs/telos-automation/tracks/``.
New decision code goes in ``python/rs3_reader/bot/``.
`Action cooldowns <telos-automation/action-cooldowns.rst>`_ lists which
actions have their own timer, which share one, and what may go out in one tick.

Native library modules
----------------------

Every ``src/<module>.c`` has a matching internal header ``src/<module>.h``.
Internal headers are included without a prefix (``"npcs.h"``); public headers
always use the ``rs3/`` prefix (``"rs3/combat.h"``).

=================== =========================================================
Module              Responsibility
=================== =========================================================
``context``         Lifecycle, native worker, sampling and frame publication
``snapshot``        Public snapshot allocation and accessors
``event_queues``    Public readers of the hit, item, NPC action and player hit queues
``event_ring``      Bounded ring buffer with independent consumer cursors
``frame``           Decoding of one frame and its consistency checks
``process``         Client discovery, attachment and fingerprinting
``memory``          Bounded, accounted remote reads
``variables``       Domain registry and sparse variable tables
``player``          Player statistics, variables, GCD and change tracking
``position``        Compatibility Mode player tile
``npcs``            NPC registry, identities, health, hit marks and hit events
``npc_animation``   Raw NPC animations and NPC action events
``combat``          Overhead prayer flags and scene epochs
``player_hits``     Local player actor, its hit marks and player hit events
``catalog``         ``rs3_catalog.bin`` loading and validation
``inventory``       Backpack/equipment slots, item identities and item events
``abilities``       Ability cooldown timers and weapon requirement checks
``input``           Opt-in foreground input
``prayer``          Deterministic prayer planner
``clock``           Monotonic and wall-clock time sources
``decode_utils``    Byte decoding helpers for copied remote memory
``profile``         Pinned client profiles (tables generated at build time)
=================== =========================================================

``tools/generate_profile.py`` turns ``profiles/profile_951_3.json`` and
``profiles/profile_6ada3f59.json`` into ``build/generated/profile_generated.h``
(``LAYOUT_*``, ``POSITION_*`` and ``PROFILE_*`` macros) and
``build/generated/profile_generated.c`` (the ``client_profile`` tables).

A frame is decoded in two phases. Everything is first staged into the working
snapshot; only after the player, client and world contexts are verified
unchanged are identities assigned and events published. A remote address is
only ever dereferenced through ``memory_read()``.

Coding conventions
------------------

C (sources, headers, C examples) and Java (examples, Ghidra script):

* Format with the repository ``.clang-format``: Allman braces, four spaces,
  100 columns, braces around every ``if``/``for``/``while`` body. Run
  ``clang-format -i`` on every changed file.
* Use explicit, descriptive names; no single-letter names except trivial loop
  counters. Name structure offsets and limits instead of writing magic numbers.
* Functions are ``snake_case`` and prefixed with their module
  (``npc_decode_list``, ``catalog_load_file``). Public symbols keep the
  ``rs3_`` prefix. Macros and constants are ``UPPER_CASE``.
* Document every non-static function in its header and every non-obvious static
  function where it is defined. Comments explain why, not what.
* Fallible internal functions return nonzero on success. Use
  ``return context_fail(context, reason, message);`` to record a failure.
* The public ABI is append-only: never reorder, resize or rename existing
  structure fields or exported functions. Assert new layouts with
  ``_Static_assert`` and expose them through ``rs3_sizeof`` or the matching
  ``*_sizeof`` function.

Python:

* Follow PEP 8 with a 100-column limit (configured in ``pyproject.toml``).
  Keep the code compatible with Python 3.9 and the standard library.
* Package modules use relative imports; run entry points with
  ``python -m rs3_reader.<module>`` and ``PYTHONPATH=python``.
* Mirror C enumerations with ``IntEnum``/``IntFlag`` from ``rs3_reader.native``
  instead of numeric literals.
* The JSON telemetry schema (``schema_version`` 1) is a contract for captures
  and the tracker. Additive changes only.

Research scripts in ``work/`` import shared paths from ``work/workspace.py``.

Checks
------

Configure, build and test from the repository root::

    cmake -S . -B build -A x64
    cmake --build build --config Release
    ctest --test-dir build -C Release --output-on-failure

The build compiles with ``/W4`` (MSVC) or ``-Wall -Wextra`` and should stay free
of warnings. Run the Python tests alone with::

    $env:PYTHONPATH = "python;work/reference"
    $env:RS3_NATIVE_LIBRARY = "$PWD\build\bin\Release\rs3_native.dll"
    python -m unittest discover -s tests/python -v

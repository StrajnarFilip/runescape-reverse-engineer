"""Python consumers of the read-only rs3_native C library.

Modules:

* ``native`` - ctypes mirror of the public C ABI and the ``Reader`` binding.
* ``telemetry`` - conversion of native snapshots into JSON-ready records.
* ``inventory_records`` - inventory, equipment and ability record conversion.
* ``hit_history`` - deduplicated history of observed hit marks.
* ``prayer`` - ctypes binding of the deterministic C prayer planner.
* ``cache_definitions`` - read-only decoder for local game cache definitions.

Command-line entry points (run with ``python -m``):

* ``rs3_reader.tracker`` - desktop tracker window.
* ``rs3_reader.jsonl_stream`` - JSON Lines telemetry stream.
* ``rs3_reader.prayer_flick`` - prayer observer and synthetic replay.
* ``rs3_reader.telos_prayer`` - Telos prayer timing capture and trial.

Importing the package does not load the native library.
"""

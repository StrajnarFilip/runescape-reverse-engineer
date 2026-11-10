"""Local desktop tracker for the read-only RS3 memory reader (standard library only).

Run with ``python -m rs3_reader.tracker``. Modules:

* ``application`` - main window, refresh loop and command-line entry point;
* ``collector`` - background thread that samples, logs and publishes records;
* ``panels`` - NPC, inventory, ability and raw-value tabs;
* ``widgets`` - meters, tables and shared formatting;
* ``theme`` - colours, fonts and ttk styles.
"""

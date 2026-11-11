S0b - Single runtime profile
============================

:Status: waiting for live session
:Wave: 0
:Branch: ``telos/S0b``
:Kind: C (native DLL)

Shared rules and procedure: ``../README.rst``.

Goal
----

One client profile that reports both the player tile and local-player
hitmarks. Today the tile exists only on the Compatibility Mode profile and the
hitmarks only on the original executable, so no single client gives the bot
both.

Read first
----------

* ``docs/prayer-flick.rst``, section "Local-player hitmarks".
* ``src/position.c``, ``src/player_hits.c``, ``src/profile.c``.
* ``profiles/profile_951_3.json``, ``profiles/profile_6ada3f59.json``.
* ``tools/generate_profile.py``.

Owns
----

* ``src/position.c``, ``src/position.h``
* ``src/player_hits.c``, ``src/player_hits.h``
* ``profiles/profile_951_3.json``, ``profiles/profile_6ada3f59.json``
* ``tests/native/test_player_hits.c`` and the position tests
* ``docs/validation/profile-unification.json``

Depends on
----------

Nothing.

Delivers
--------

The chosen runtime profile, named in the validation report, with both channels
available. Every later track validates against that profile.

Done when
---------

Fixture tests pass on both profiles, and on the chosen profile a live sample
shows a valid tile and a player hit event in the same session. Choose whichever
port is cheaper and say why in the report.

Result so far
-------------

The chosen runtime profile is **Compatibility Mode** (``6ada3f59-1a86000``).
It is the installed client, and it lacked only the player actor vtable
(``0x150d8c8``), now pinned with two code signatures. The tile and the hit
marks share one verified actor lookup in ``src/position.c``. Evidence and live
counts are in ``docs/validation/profile-unification.json``. Fixture tests pass
on both profiles.

Needs the user
--------------

Request 1 below must land first, so the shipped DLL reports both channels.
Then one short session on the Compatibility Mode client (the installed
launcher client):

1. Log in and stand where an NPC can attack you.
2. The agent starts a read-only ``Reader(worker_hz=100)`` sampler for about
   60 seconds. It counts samples with ``position.valid``, samples with
   ``player_hit_info.status == AVAILABLE``, distinct tiles, and the events from
   ``drain_player_hit_events()`` with their loss count.
3. Walk three to five tiles.
4. Take one hit from any NPC.

Pass: at least two distinct tiles, at least one player hit event in a sample
with a valid tile, and zero lost events. The agent records the counts in the
validation report and sets this track to done.

Requests
--------

These files are outside this track's Owns list and owned by no other track;
track L routes them.

Integration update (2026-11-11): requests 1-4 below are implemented on
``telos/integration``. The generated Compatibility Mode table now carries
``0x150d8c8``. The remaining live movement-plus-hit check under Needs the user
is still pending; this merge does not change the track's validation status.

1. ``tools/generate_profile.py``: add ``"player_actor_vtable_rva"`` to
   ``OPTIONAL_PROFILE_FIELDS``, so ``compatibility_addresses()`` emits
   ``combat_layout.player_actor_vtable_rva``. Also reword the comment in
   ``primary_addresses()`` that says only the original executable has a
   verified local player actor. Checked on a scratch copy: the generated
   table carries ``0x150d8c8u``, all ten CTest targets pass, and a live sample
   showed both channels in all 729 samples. Once it lands, the fallback in
   ``compatibility_profile()`` of ``tests/native/test_player_hits.c`` is unused.
2. ``docs/prayer-flick.rst``, section "Local-player hitmarks": replace the
   sentence saying the Compatibility Mode actor layout is unvalidated with the
   S0b result and a link to ``validation/profile-unification.json``.
3. ``docs/research-notes.rst``, section "Compatibility Mode build": add the
   player actor vtable ``0x150d8c8``, its constructor ``0x40cc00`` and the
   type-2 UID getter ``0x36c400``.
4. ``AGENTS.md``: mention that Compatibility Mode is the runtime profile chosen
   by S0b, where it says hit marks come from the original profile.

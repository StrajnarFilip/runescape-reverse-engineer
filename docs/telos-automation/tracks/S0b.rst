S0b - Single runtime profile
============================

:Status: open
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

Needs the user
--------------

One short session on the chosen client: walk a few tiles, then take one hit
from any NPC.

Requests
--------

None yet.

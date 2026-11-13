L - Integration ladder
======================

:Status: in progress
:Wave: 3
:Branch: ``telos/integration``
:Kind: Integration and live validation

Shared rules and procedure: ``../README.rst``.

Goal
----

Merge the finished tracks and take the bot live one rung at a time, with the
user at the keyboard, until it kills Telos unattended across the target enrage
range.

Read first
----------

* ``../README.rst`` and the Status and Requests sections of every brief.
* ``docs/prayer-flick.rst``, section "Remaining Telos calibration".

Owns
----

* Merges into ``telos/integration`` and conflict resolution in the shared
  wiring files.
* ``docs/validation/telos-ladder-*.json``
* The ``Requests`` sections of all briefs: route each one to the owning track.

Depends on
----------

Each rung depends on the tracks named beside it. Merging can start as soon as
the first track is done.

Delivers
--------

The rungs, in order. Do not start a rung before the previous one has passed.

1. **Shadow** (D1, D8, any producers). The full stack logs intents during the
   user's manual kills. Compare with the user's own keys and the hits taken.
2. **Prayer only** (D2, A1, D6). The user plays everything else.
3. **Plus survival** (D3).
4. **Plus rotation** (D4). The user still moves and handles mechanics.
5. **Full supervised kill at 300%** (D5, A2).
6. **Unattended loop** (D7) with the streak and loot policy.
7. **Enrage bands** 300-999, 1000-1999 and 2000-4000. Each band repeats the P6
   calibration and rungs 1-5 before it is enabled.

Done when
---------

A rung passes after repeated clean runs with zero watchdog trips and zero
event-queue losses, recorded in a compact ladder report. Any death or watchdog
trip is analysed from the session log and assigned to its owning track before
the rung is retried.

Needs the user
--------------

Present for every rung, with the kill switch within reach.

Requests
--------

None yet.

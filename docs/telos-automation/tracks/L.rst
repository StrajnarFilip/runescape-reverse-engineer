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

* Integration checkpoint, 2026-11-13: all perception and decision modules are
  merged for shadow preparation. Pending live statuses remain unchanged. The
  release build, all 15 CTest targets (271 Python tests) and installation of
  all eight public headers pass. No ladder rung has passed and no automatic
  input is enabled. See ``docs/validation/telos-ladder-integration-2026-11-13.json``.
* D8 request resolved: ``python -m rs3_reader.bot --capture capture.jsonl
  --arbitrate`` connects D6 through one RecordingActuator with dispatch disabled.
  D1 runs first; D2 and D3 receive its same-frame state. Arbitration decisions
  are logged without simulated input acceptance. Native telemetry now publishes
  the NPC-action queue with original event identity, timestamps and loss counts.
  Old captures are not backfilled: missing inventory queues or encounter evidence
  still stop replay. These expected stops are not clean shadow runs.
* Physical stop check passed outside combat on 2026-11-13: the user pressed
  Ctrl+Shift+F12 with RuneScape focused. The observer latched ``kill_switch``
  after 93 supervised frames, with zero input acceptances. Use the explicit
  live CLI option ``--kill-key Ctrl+Shift+F12``; the source default is unchanged.
  This verifies the read-only stop path, not combat timing or held-key release.
* P1/P2 update, 2026-11-14: the original-profile collector now supplies the
  legacy/P1 player tile, the local player's typed NPC/cleared target and a
  received-server-tick ordinal in one runtime. Player target config/instance
  resolution requires a current same-world NPC record. The counter warms up
  at a transition and invalidates on stale, torn or changed-identity evidence.
  ``docs/validation/telos-ladder-native-foundation-2026-11-14.json`` records the
  combined checks. NPC incoming-attack targets, castability, phase 5 and
  protection-check data still block their dependent actions.
* A1/A2/P5: native target/minimap/view evidence and serialized pointer dispatch
  remain required before safe-area movement acceptance. No extra Telos fight
  is requested while these dependencies are being connected.
* P1/P2/P3/P6: the current L calibration scope is explicitly the pinned original
  951-3 runtime: all new target/counter evidence and existing Telos semantics
  belong to its hash. S0b's historical Compatibility Mode result remains
  separate; no pose, target, tick or encounter mapping is transferred to it.
* P2/A1/D6: the received ordinal is now available on the original profile.
  Server phase and transport uncertainty remain unknown (NaN in C, null in
  JSON). It does not establish prayer deadlines or the server tick in which
  a future input arrives. No automatic input or ladder rung is enabled here.
* D4: intended-account rotation rules/bindings and complete castability remain
  unconfigured. A successful module merge is not a demonstrated optimal rotation.
* P4/D5: respect the user's deferred earlier beam/rock research. Phase-5 beams
  remain a later supervised task; no missing hazard channel is marked complete.
* S0b: newer commits contain a separate experimental zero-enrage controller,
  screenshot helpers and Arduino mouse extensions beyond the completed runtime
  foundation. Review that distinct control path before adopting it into L;
  do not merge its whole branch as though it were only profile-unification data.
  Only the completed foundation at ``d9e496c`` is included in this checkpoint.
  The later ``32d8253`` update adds Compatibility Mode spatial/zero-enrage
  encounter work and further practice-attempt records. Review those additions
  independently against their executable and encounter scope before integration;
  they do not establish a 300%-4000% ladder pass.

* P1 follow-up, 2026-11-14: NPC entity references now decode in C. Telos's
  player-kind UID matched the exact local player index in 692 research samples;
  fresh C/JSON confirmed 60 dormant-Telos clears. Active-Telos C/JSON confirmation
  remains a next-session check. Entity references can mean follow/owner and do
  not establish an attack or protection deadline. See
  ``docs/validation/telos-ladder-npc-targets-2026-11-14.json``.
* A2/native input owner: implement balanced modified-ability and exact-item
  key-chord entry points in the owned input module, with rejection/cleanup tests.
  Request additive declarations in the unowned public ``rs3.h``; L will route
  that narrow ABI registration with the implementation merge. A1 then connects
  its existing serialized backend. This is software preparation only; no rung
  or automatic input is enabled while the user is away.

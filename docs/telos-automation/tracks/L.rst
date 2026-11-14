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

* A2 native-input request resolved: two additive public chord declarations
  are registered without changing any ABI structure or old function signature.
  The native tests call pure preparation helpers on positive paths, never OS
  dispatch. A1 connection and physical-delivery validation remain pending.

* A1 chord/binding connection is merged: the native backend supports exact
  tracked-item and modified-ability chords, with explicit older-DLL rejection.
  Dynamic bindings are rechecked before dispatch and each serial key command.
  A newer native copy rejects for policy re-evaluation instead of executing an
  old decision. All 15 CTest targets pass (283 Python tests, zero skips),
  including 31 A1 fake-device/ABI/host-firmware cases. No live input is enabled.

* D8 readiness inspection is merged. It is bounded and read-only, imports no
  controllers/devices on capture replay, and reports availability per process/
  scene/sample. All 15 CTest targets pass (291 Python tests, zero skips).
  Ctrl+Shift+F12 is now the default; the earlier stop-key check remains the
  physical evidence. The forty-row historical native audit is in
  ``docs/validation/telos-ladder-readiness-2026-11-14.json``.
* Native snapshot owner/A1: add a caller-owned snapshot-copy accessor so the
  backend can use the same immutable reader sample examined by its policy.
  Copying the worker's newer latest frame must keep rejecting until the policy
  is re-evaluated, but a stable consumer snapshot should not be rejected merely
  because the 100 Hz worker advanced independently. Keep old-DLL fallback safe.

* Exact snapshot handoff is integrated: a local copy preserves original frame
  identity/time, item tokens and targets even after source mutation/destruction.
  A1 now uses the examined consumer snapshot while the worker keeps sampling;
  consumer refresh and dispatch must remain serialized. Older DLLs keep safe
  latest-frame rejection. Seven native input groups, 33 A1 cases and all 15
  CTest targets pass (293 Python tests, zero skips). The copy-only microbenchmark
  median was 0.067 ms; this is not game-input latency or a collector benchmark.

* Fault-handoff issue resolved: A1's latched uncertainty no longer looks like
  an ordinary rejected send to D6. The arbiter closes the backend and stops
  the remaining batch, and D8 stops in the same frame. Accepted input remains
  recorded even if cleanup faults. All 15 CTest targets pass (300 Python tests,
  zero skips), including real A1/D6 and D6/D8 integration against fake devices.
  ``docs/validation/telos-ladder-fault-handoff-2026-11-14.json`` records scope.
  Physical key delivery/release and every combat rung remain unvalidated here.

* P5's raw-menu decoder is integrated. Route its requested minimal shared
  storage registration: one private snapshot field, one worker-copy line and
  one additive accessor. No existing public layout changes. P5 now wires its
  clear/stage/ctypes/telemetry paths and validates unavailable-frame handling.
  Menu data remains unvalidated and cannot authorize pointer actions.

* P5 raw-menu capture preparation is complete: guarded C staging, worker copy,
  owned-snapshot accessor, additive 304-byte ABI, ctypes and diagnostic JSON.
  ``ui.pointer_candidate.value`` remains null and raw fields stay unvalidated.
  All 15 CTest targets pass (300 Python tests, zero skips), with 18 native UI
  groups and four local serializer/real-ABI checks. The game was closed.
  ``docs/validation/telos-ladder-menu-candidate-2026-11-14.json`` lists the
  required later labels. No hit-test, full castability or pointer input is enabled.

* A1 pointer bridge is integrated: explicit current MinimapBinding and
  NpcClickBinding records connect the existing native exports to the serialized
  policy. No built-in provider supplies them. Native geometry/path/hit-test
  acquisition remains P1/P5/A2 work; raw menu candidates authorize nothing.
  Serial stays keyboard-only. All 15 CTest targets pass (309 Python tests,
  zero skips), including 43 actuator cases. See
  ``docs/validation/telos-ladder-pointer-backend-2026-11-14.json``. No live input
  was sent and no ladder rung was enabled.

* P6 full-frame recording is integrated at a4e8430. The read-only command now
  keeps guarded encounter observations and all four native queues/loss counters
  in schema-1 records usable by replay and shadow. Synchronous C acquisition
  prevents worker advancement between the frame copy and recorder queue drains.
  All 15 CTest targets pass (317 Python tests, zero skips); an absent-PID real
  DLL capture also passed readiness and analysis with unavailable values and
  zero input. See
  ``docs/validation/telos-prayer-calibration-recorder-2026-11-14.json``.
  No attack timing/classification authorization changed. Historical input
  provenance remains true/false/unknown, and analysis cannot overwrite inputs.

* D4 target regression fixed at c601487: an old offensive intent no longer
  survives a selected-target switch, missing/mismatched config/runtime identity
  or replacement action under its old ID. All 15 CTest targets pass (321 Python
  tests, zero skips), including four new regressions and 18 rotation cases.
  ``docs/validation/telos-ladder-rotation-target-2026-11-14.json`` records scope.
  No live input or account/castability/profile authorization changed.
* D3: add a producer can_send hook for current survival eligibility. D6's
  generic item/ability readiness does not recheck changed resource thresholds,
  protection, stun/defensive blockers or threat identity after proposal. Keep
  validation side-effect free and preserve simultaneous food/potion lanes.

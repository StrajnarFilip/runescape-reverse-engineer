Combined Telos practice controller
=================================

``work/telos_zero.py --ordered-p1`` composes the ordered P1 opening with the
existing native-state mechanic and prayer controller. This integration has
offline tests. Complete kills, protection coverage and Soul Split healing
attribution remain unverified. P5 still enters observation mode.

Running
-------

Build the DLL/catalog and replace ``CURRENT_PID`` with the intended process::

    $env:PYTHONPATH = 'python'
    python work/telos_zero.py --pid CURRENT_PID --input-backend win32 --ordered-p1

F12 stops this controller. Focus loss pauses input. The process lifetime and
scene remain pinned. The opening requires original profile 951-3, native
in-instance evidence from VARP 6359, dormant Telos, dual Lengs, Deflect Melee
and at least 115% observed adrenaline. Dormant Telos outside the instance cannot
arm it. Enrage is metadata, not a required equality to 300%. The agent or
operator handles preparation, entry and the staging Surge. The existing
mechanic controller owns combat movement.

Win32 submits each prepared batch in one balanced input call. Three 50 ms
presses would otherwise overrun the shared 100 ms snapshot guard. OS insertion
order does not establish server-tick placement. Arduino remains selectable;
its serial batch timing requires separate live validation.

Shared observation and input
----------------------------

The C Reader samples once. Each of its four event queues is drained once.
``AttemptLog.frame`` retains ``SnapshotEvents`` and the inventory events also
used by the potion monitor. ``NativeTelemetry(reader=reader).record_current``
serializes them without sampling, draining or taking ownership of the Reader.
Its exact ``observed_ns`` stays in the native monotonic clock domain.

The opening consumes a scoped immutable WorldState containing its eight
configured abilities and the Telos forms. Mechanics and logging retain the
complete source snapshot. Omitted channels stay unknown. ``observe`` runs
before urgent responses and preserves the first native wake sample. Prayer
and defensive decisions run first; ``dispatch`` then yields to a requested
or pending defensive. A missed wake is reported rather than rebased to a later
sample. D6 prepares every descriptor and native role before the shared backend
receives input. The bounded pilot reuses these same action checks.

The requested order remains Devotion; Berserk then renewal; A, vulnerability,
Greater Barge; Meteor Strike; Assault; Overpower; EZK equip and physical special;
red EoF equip and its declared Dragon scimitar special. Renewal consumption
remains optional after its ordered press. Retained adrenaline neither blocks
the opening nor proves a perk proc. Equipment, target, inventory and ordinary
ability cooldowns each require their own later observation.

Logging
-------

All events share the attempt's schema-1 JSONL sequence in ``training-data/``.
``p1_key_batch_requested`` precedes input. ``p1_key_input`` records descriptors,
native start/end times and balanced versus uncertain delivery. A partial Win32
insertion identifies the inserted prefix, releases held keys and latches a
fault. It is never replayed. ``p1_batch_sent`` does not confirm a cast or server
tick. ``p1_action_confirmed`` identifies the later action-specific observation.
Phase changes end the opening without asserting that every action fired;
the mechanic controller retains its backend.

Specials and C effects
---------------------

The user clarified that physical weapon-special cooldowns appear on the debuff
bar. EoF has no personal cooldown beyond the ordinary ability delay. Its
readiness uses GCD, resources and equipment; a generic button timer cannot
acknowledge the stored special.

Two appended native effect candidates come from revision-951 gamevals:

* ID 15, ``igneous_showdown_cooldown``: VarClient 8301,
  ``combatv2_cooldown_weapon_igneous_showdown_end_client``.
* ID 16, ``dragon_scimitar``: VarClient 7092,
  ``combatv2_buff_dragon_scimitar_effect_end_client``.

Two original-profile dummy captures on November 17 paired each special with
its own fresh endpoint, 2998--3000 client logic ticks ahead on observation.
These support 20 ms deadline units and a 60-second initial deadline. The C
collector now reports remaining time for these two rows on the original
profile only. Compatibility Mode retains raw, unvalidated candidates.
Active presence and early cancellation remain unknown. An expired cached
endpoint is a zero deadline, not evidence of an inactive buff or a new cast.

The opening acknowledges a special only from a strictly newer same-session
frame with the exact equipment and variable, a changed endpoint, and a newly
started deadline of at least 57.5 seconds. A generic GCD or adrenaline change
cannot acknowledge it. An explicitly ``not_set`` native variable permits one
bounded first-use attempt without inventing an expired timer; read failures
still block. The physical special waits for its own cooldown. EoF never waits
for the Dragon scimitar effect to expire. Greater Barge's channel conversion
still needs a validated signal.

``rs3_effect`` remains 72 bytes and IDs 1-14 retain their meanings.
``RS3_EFFECT_COUNT`` is now 16. C/Java consumers must honor the accessor's
``required`` count and ``RS3_E_BUFFER`` negotiation. Python's buffer and names
were updated. These deadline rows set only ``RS3_EFFECT_REMAINING_KNOWN``;
``active`` and ``stacks`` stay unknown. See the complete evidence and capture
limitations in ``docs/validation/telos-special-effects-and-precast-2026-11-18.json``.

Damage queueing and the bounded dummy probe
------------------------------------------

Ordered damage and the Tendrils Hurricane response can be pressed in the last
600 ms of GCD. Pending input is not repeated while its native cast outcome is
awaited. Defensive responses retain their existing 200 ms press limit and
their individual incoming-hit windows; wider damage queueing does not validate
protection or defensive timing.

The standalone probe can record eight basic casts without specials::

    python work/calibrate_special_effects.py --pid CURRENT_PID --duration 45 --basic-casts 8

It requires the intended 10,400-HP original-profile account, equipped EZK and
an exact native training-dummy target. It resolves current bindings from C UI
labels and selects eligible Greater Fury, Adaptive Strike, Dismember, Rend or
Punish, rather than waiting solely for Adaptive Strike. F12 stops it. Focus,
process, target, health and event-loss checks end the bounded run. Without
``--basic-casts`` it instead calibrates one physical special and one red-EoF
special, building adrenaline with basics as needed.

The November 18 trace has four consecutive basic starts 1800 ms apart, followed
by an unintended physical special caused by a probe-mode fallthrough. The bug
is fixed and covered by a loop-level regression test. The original trace is
preserved as a mixed run, not an eight-cast clean pass. A clean repeat with all
five basics remains pending. This probe does not implement a full boss rotation;
the broad controller's ordinary damage fallback still depends on Revolution.

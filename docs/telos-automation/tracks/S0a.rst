S0a - Contracts and replay
==========================

:Status: done
:Wave: 0
:Branch: ``telos/S0a``
:Kind: Python

Shared rules and procedure: ``../README.rst``.

Goal
----

Define the three contracts (``WorldState``, ``Intent``, ``Actuator``) and build
a replay harness that feeds a recorded JSONL capture through a decision stack
and logs the intents it emits. No game is needed to run it.

Read first
----------

* ``python/rs3_reader/telemetry.py`` and ``jsonl_stream.py`` (capture schema).
* ``python/rs3_reader/prayer_flick.py`` (``--replay``) and
  ``examples/prayer_replay.json``.
* ``python/rs3_reader/native.py`` (what the DLL exposes today).

Owns
----

* ``python/rs3_reader/bot/__init__.py``
* ``python/rs3_reader/bot/world_state.py``
* ``python/rs3_reader/bot/intents.py``
* ``python/rs3_reader/bot/actuator.py`` (the interface and a recording fake)
* ``python/rs3_reader/bot/replay.py``
* ``tests/python/test_bot_contracts.py``, ``tests/python/test_bot_replay.py``
* ``tests/python/fixtures/bot/``

Depends on
----------

Nothing.

Delivers
--------

* ``WorldState`` built from today's telemetry, with named optional placeholders
  for the fields P1-P5 will fill: NPC tile/facing/target, player facing/target,
  Telos phase/enrage/streak/meter/Freedom, game tick, player effects, hazards,
  open interfaces and action-bar bindings.
* ``Intent`` types with priority, deadline and evidence.
* The ``Actuator`` interface and a fake that records what it was sent.
* ``python -m rs3_reader.bot.replay <capture> --stack <module>`` writing a
  JSON Lines intent log.

Done when
---------

A small synthetic capture fixture replays end to end with a null decision
stack, golden-file tests pass, and every placeholder field is documented with
the track that will fill it.

Needs the user
--------------

Nothing.

Requests
--------

For A1 and D6: preserve independent action lanes. The user explicitly clarified
that food, a potion, an ability and a prayer change may share one game tick.
Do not impose a global one-input-per-tick limit. Real action-specific budgets,
cooldowns and serialization belong to those tracks, not the recording fake.
See ``../action-cooldowns.rst`` for research leads. A lane is a classification,
not a limit of one action in that lane; shared groups and combo food still
require action-specific policy and validation in D6/A1/L.

Contract reference (version 1)
------------------------------

``WorldState.from_telemetry(record, now_ns=None)`` accepts schema-1 telemetry
and the existing unversioned ``capture_prayer`` records. It imports no native
module and retains no values between samples. Measurements are frozen
``Observation(value, age_ms, source, reason, complete)`` objects; access a
collection through its ``.value``. ``None`` is unknown, whereas zero, false
and an explicitly complete empty tuple are known. Missing age fails
``is_fresh``. Value-change age is not observation age. Inventory, equipment
and ability entries retain their existing schema keys as immutable mappings;
their cooldown/castability reasons remain intact.

Producer placeholders and additive telemetry locations:

* P1: ``npcs.entities[].tile/facing/target`` and ``player.facing/target``.
  A tile is ``{x, y, plane}``; facing is ``{angle, unit}`` with an explicit
  ``degrees``, ``radians`` or ``client_units`` unit. Targets are ``EntityRef``
  objects (kind and optional instance/runtime/config/player IDs). A target
  with kind ``none`` is explicitly absent; an unknown target is ``None``.
* P2: ``telos.phase/enrage/streak/meter/freedom`` and ``game_tick``. Freedom
  is an observed active-state boolean. Game-tick data carries its index/phase
  and ``uncertainty_ms`` in a mapping. Neither raw stage bits nor the client
  logic clock establishes these decoded fields.
* P3: ``player.effects.entries`` with effect identity, stacks and remaining
  time. Completeness is required before an omitted effect means absent.
* P4: ``hazards.entries``. P5: ``ui.open`` and ``action_bar.entries``.

Additive values may use ``{value, age_ms, source, reason, complete}`` envelopes.
Collections use section ``status`` (available/partial/unavailable) or an
explicit envelope completeness flag. Missing completeness stays unknown.
Per-field age is relative to captured replay time; an explicitly later
``now_ns`` ages it further. Historical queue records keep their original
process, scene and timestamp; absent loss counters stay unknown.

``Intent`` names its action, priority (higher wins), lane, issue/deadline times,
snapshot ``observed_ns`` and sequence, session identity, and evidence. Evidence
contains a field/rule name and its original observation. Available actions are
``SetPrayer``, ``ActivateAbility`` (catalog struct ID), ``ConsumeSlot`` (zero-based
backpack slot, expected item ID and optional token), ``MoveTo`` and ``Interact``.
Lanes are food, potion, prayer, GCD, off-GCD and pointer. They classify budgets;
they do not set their limits. Deadlines use native monotonic nanoseconds and
are inclusive. Real backends must reject missing/stale snapshot provenance.

``Actuator.send(intent) -> SendResult`` reports accepted/rejected input only.
Only a later observation can confirm the action. ``close()`` is idempotent.
``RecordingActuator`` records accepted and rejected attempts, uses an injected
clock or the issue time, checks deadlines, and never changes the world state.

Replay, with ``PYTHONPATH=python``::

    python -m rs3_reader.bot.replay tests/python/fixtures/bot/synthetic_capture.jsonl --stack null
    python -m rs3_reader.bot.replay capture.jsonl --stack my_stack --output intents.jsonl

A trusted stack module exports ``create_stack()`` returning an object with
``step(world) -> Iterable[Intent]``. The harness emits one deterministic JSONL
row per sample, including proposals, identity/clock/sequence boundaries and
queue loss counters. It does not dispatch or acknowledge proposals, sleep,
reorder samples or supply an actuator to producers. Session resets/watchdog
policy remain with D1/D8. Logs always state ``input_sent: false``.

The five-row committed fixture and both golden logs are synthetic; they
contain no gameplay capture. The example stack exercises the contract, not
combat decisions. The tests also invoke the CLI with a nonexistent DLL path
and assert that neither the native nor telemetry module was imported.

Validation
----------

On 2026-11-11, rebased on ``telos/integration`` at ``665c547``:

* Windows x64 native configure/build passed using the existing Ninja/Zig
  toolchain. No C source or shared build registration was changed.
* ``ctest --test-dir build -C Release --output-on-failure``: 10/10 suites
  passed, including 78 Python tests. The 25 new contract/replay tests cover
  unknown and partial data, immutable ownership, original event provenance,
  snapshot ageing, deadlines, concurrent action categories, both golden
  logs and the offline CLI/plugin path.
* Black checks passed for all eight added Python files.

No live session or game input was needed. Real backends, arbitration and
combat policy remain with their assigned tracks.

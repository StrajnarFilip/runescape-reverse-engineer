S0a - Contracts and replay
==========================

:Status: open
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

None yet.

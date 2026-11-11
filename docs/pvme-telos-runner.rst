PVME melee Telos rotation runner
================================

``python -m rs3_reader.pvme_telos`` prints a complete, configurable keyboard
recipe for the `PVME melee Telos guide`_, reviewed on 2026-11-11. It includes
War's Retreat preparation, phases 1-4, all three fonts, and both phase-5 beam
routes. SendInput is the default transport. ``--backend arduino --port COM5``
uses the existing Leonardo sketch, with no Python dependencies beyond the
standard library.

This is a **supervised, calibrated sequence runner**, not a claim of flawless
execution or an unattended Telos bot. It does not recognize phases, fonts,
beam positions, selected targets, buffs, castability or the contents of EoFs.
An accepted key event is not a confirmed cast. It does not retry uncertain
casts. The guide's arrows are action order, not sufficient timing evidence.

The supplied profile deliberately has unbound actions and unset timing.
The attached action-bar screenshot cannot establish every action, gear switch,
EoF payload or timing dependency. No key mapping was guessed from it. The
existing Ctrl+F1/F2/F3/F4 prayer bindings remain reserved and unchanged.
There is no prayer automation in this runner.

Files
-----

* ``python/rs3_reader/pvme_telos.py``: CLI, recipe validation, scheduling,
  manual checkpoints, native session/focus guards and evidence gate.
* ``python/rs3_reader/rotation_keys.py``: matching physical key definitions,
  Win32 SendInput and standard-library Windows serial transport.
* ``profiles/pvme_telos.json``: editable actions, alternatives and recipe.
* ``tests/python/test_pvme_telos.py``: fake-device, clock and session tests.

Inspect the recipe
------------------

From the repository root:

.. code-block:: powershell

    $env:PYTHONPATH = "python"
    python -m rs3_reader.pvme_telos
    python -m rs3_reader.pvme_telos plan --segment p1
    python -m rs3_reader.pvme_telos plan --beam green
    python -m rs3_reader.pvme_telos plan --segment p5_red --choice red_finisher=punish

``plan`` never loads the native reader, constructs an input backend or opens a
serial port. It prints the selected actions, all manual checkpoints, missing
configuration and the hash needed to identify the exact plan during validation.
The default route includes Wars through font 3 and the red phase-5 route.
Use ``--beam green`` for the alternative. ``--segment`` can be repeated to
select a particular ordered subset; both phase-5 alternatives cannot run in
one invocation.

Configure bindings and timing
-----------------------------

Make a local copy under ignored ``outputs/`` before entering account-specific
configuration. Pass that path using ``--profile``. Never commit raw captures
as part of calibration.

Each action's ``binding`` is a list of explicit keypresses. For example, this
illustrates the syntax only; it is **not the user's verified Berserk key**:

.. code-block:: json

    "berserk": {
      "label": "Berserk",
      "binding": [{"key": "1", "at_ms": 0}]
    }

A gear switch followed by a special can use multiple presses with calibrated
offsets. For example, a hypothetical switch/activation/restoration binding:

.. code-block:: json

    "binding": [
      {"key": "Shift+Q", "at_ms": 0},
      {"key": "F5", "at_ms": 120},
      {"key": "Shift+W", "at_ms": 400}
    ]

The example delays do not establish equipment acknowledgement or spec success.
Verify each weapon, its activation binding, and the required weapon restoration.
For Varanus's Mercy, verify the actual stored special; amulet colour or item ID
is insufficient. EZK stall and release are distinct operations, not two normal
special attacks. Their bindings and targeting/position setup require separate
calibration.

Keys name **US physical keyboard positions**, consistently across both backends.
Available keys include A-Z, 0-9, F1-F12, navigation keys, Space, Tab, Enter,
Escape, Backspace, Minus and Equals. Prefix with Ctrl+, Shift+ or Alt+ as needed.
Key names are case-insensitive; ``a`` does not imply Shift. On other layouts,
verify the resulting in-game shortcut. F8 and F12 must be unbound in RuneScape
and are reserved runner controls, including modified forms. Configured
Ctrl+F1 through Ctrl+F4 prayer shortcuts are rejected.

Timing fields:

* ``tick_seconds`` is a planning conversion (initially 0.6), not a native
  server-tick observation.
* ``after_ticks`` is the interval from the previous step's **start**, or from
  F8 release after a checkpoint. Null requires calibration. The guide's
  explicit one-/two-tick delays are retained, with their unresolved anchors
  described in the step notes.
* Each step action's ``at_ms`` is its offset from that step's start. A guide
  ``+`` does not prove simultaneous input is legal: additional action offsets
  are left null until measured. This includes consumables and defensive spells.
* Each binding press's ``at_ms`` is its offset from that action's start.
  Macro presses and action groups must be in increasing, nonoverlapping order.
* ``hold_ms`` is key-down duration. ``max_lateness_ms`` is the tolerated host
  scheduling delay, bounded to 150ms. Late sends abort; there is no catch-up burst.

Channels, Greater Barge's bleed setup, GCD, adrenaline, gear effects, individual
cooldowns, bombs on the last GCD tick, and off-GCD combinations all need measured
timing. The runner does not silently assign a three-tick interval to them.

The ``choices`` object defines the guide's alternatives. Select ``red_finisher``,
``green_filler`` and ``green_finisher`` with ``--choice NAME=ACTION``. These are
explicit choices, not inferred combat decisions. ``melee_basic`` is an explicit
binding to a suitable available basic, not a randomly chosen ability.

Live validation and execution
-----------------------------

The project requires staged live enablement through `track L`_. This runner
does not promote a rung or manufacture evidence. There is currently no matching
passed live report. It therefore ships with live execution blocked, while both
backend implementations are available for the controlled validation workflow.
Do not treat a fixture test or a successful serial acknowledgement as a live
rotation pass.

Track L's compact JSON report for this runner must contain:

* ``schema_version: 1``, ``kind: "pvme_rotation"`` and an ``evidence`` reference.
* ``backend`` (``sendinput`` or ``arduino``) and the native-verified
  ``client_sha256``. Calibrate each transport independently.
* Timezone-aware ``recorded_at`` and ``expires_at`` timestamps. Evidence must
  already exist and remain unexpired throughout a run.
* ``enrage_band``: exactly ``[300, 999]``, ``[1000, 1999]`` or ``[2000, 4000]``.
* ``rungs``: objects named ``"1"`` through ``"4"``, each with ``status:
  "passed"``, at least two ``clean_runs``, zero ``watchdog_trips`` and zero
  ``event_queue_losses``. Follow the roadmap's actual acceptance procedure.
* ``plans``: selected segment names mapped to their printed ``plan_sha256``.
  The hash covers the full profile and resolved alternatives. Changes to keys,
  timing, route choices or the recipe invalidate prior calibration.

Check a fully configured plan and report without touching devices:

.. code-block:: powershell

    python -m rs3_reader.pvme_telos check --profile outputs/telos-local.json --segment p1 --enrage 300 --validation docs/validation/telos-ladder-pvme.json

The report filename above is a future validation artifact, not a supplied pass.
After that controlled validation, start the same plan:

.. code-block:: powershell

    python -m rs3_reader.pvme_telos run --profile outputs/telos-local.json --segment p1 --enrage 300 --validation docs/validation/telos-ladder-pvme.json

For the Leonardo, first upload ``arduino/serial_keyboard/serial_keyboard.ino``
as described in `Arduino keyboard setup <arduino-keyboard.rst>`_, close any
serial monitor and use its backend-specific validation report:

.. code-block:: powershell

    python -m rs3_reader.pvme_telos run --profile outputs/telos-local.json --segment p1 --enrage 300 --validation docs/validation/telos-ladder-pvme-arduino.json --backend arduino --port COM5

``--pid`` selects a particular client if needed. The DLL still verifies its
executable profile and signatures. Memory access stays read-only. No new
memory acquisition engine, game function call or memory write is introduced.

At every printed checkpoint, the operator verifies the phase, target, gear,
prayers, chat/dialog state and action bar, then presses and releases **F8** with
RuneScape in the foreground. An already-held F8 cannot start a segment. The
runner pins process identity and checks fresh native health/scene data. It
stops on focus loss, process/scene change during a segment, unknown/zero health,
stale data, expired evidence or a backend failure. It never resumes an aborted
segment automatically. A phase/font checkpoint may accept a new scene, but
never a new process identity.

**F12** stops globally; Ctrl+C stops from the terminal. Held synthetic keys are
released during normal shutdown and exceptions. Arduino uses one command per
acknowledgement; a timeout/error poisons the stream and drops DTR so the sketch
releases its keys. There are no automatic serial retries. Device failure or
process termination can limit host cleanup; a successful key release still
does not acknowledge any game action.

Movement, font interaction, Dive destination clicks and some targeting/stall
operations remain explicit manual checkpoints. The current Leonardo sketch
is keyboard-only, and this runner uses identical keyboard capabilities for
both transports. These checkpoints intentionally prevent a claim that this
script already completes an unattended kill. The wider roadmap supplies the
missing encounter, movement, cast-confirmation and survival behavior.

Validation of this implementation
--------------------------------

Fake clocks/devices cover guide ordering and alternatives, missing configuration,
calibration invalidation, deadlines, interruption during waits, foreground and
native-session failures, SendInput delivery/release failures, and Arduino
errors/timeouts. They never send OS keyboard input. Live transport and complete
kill validation remain pending.

.. _PVME melee Telos guide: https://pvme.io/pvme-guides/rs3-full-boss-guides/telos/melee/
.. _track L: telos-automation/tracks/L.rst

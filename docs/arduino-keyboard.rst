Arduino Leonardo serial keyboard
================================

The ``arduino/serial_keyboard/serial_keyboard.ino`` sketch listens on the
Leonardo's USB serial port and sends keyboard events to the same computer over
USB HID. It only emits keystrokes in response to serial commands.

Build and upload
----------------

In Arduino IDE, install **Arduino AVR Boards** and Arduino's **Keyboard**
library, open the sketch, select **Arduino Leonardo** and its serial port,
then upload. The equivalent Arduino CLI commands from the repository root are
shown below. Replace ``COM5`` with the port listed for your board. The monitor
must be closed during upload. Run ``arduino-cli board list`` again afterward
because Windows may assign a new COM port when the keyboard interface appears.

.. code-block:: powershell

    arduino-cli core update-index
    arduino-cli core install arduino:avr
    arduino-cli lib install Keyboard
    arduino-cli board list
    arduino-cli compile --fqbn arduino:avr:leonardo arduino/serial_keyboard
    arduino-cli upload --fqbn arduino:avr:leonardo --port COM5 arduino/serial_keyboard

Serial protocol
---------------

Open the USB serial port at 115200 baud with DTR asserted. Send one ASCII
command per line, terminated by LF or CRLF. Command names are uppercase and
the key argument is two hexadecimal digits from USB HID Keyboard/Keypad usage
page ``07``. For example, ``04`` is the A key, ``3A`` is F1, ``E0`` is left
Ctrl and ``E1`` is left Shift. The host's keyboard layout determines the
characters produced by those keys.

================== =============================================================
Command            Effect
================== =============================================================
``HELLO``          Reply ``RS3_KEYBOARD 1``; no key event.
``PING``           Reply ``OK`` and refresh the held-key watchdog.
``PRESS <key>``    Hold a key. A repeated press of a held key has no effect.
``RELEASE <key>``  Release a key. A release of an idle key has no effect.
``TAP <key>``      Press and immediately release a key.
``RELEASE_ALL``    Release all keys and modifiers held by this sketch.
================== =============================================================

Read one reply before sending the next command. ``OK`` means the sketch
processed the command. ``ERR COMMAND`` signals an unknown or missing command,
``ERR KEY`` an invalid key, and ``ERR FRAME`` a line that is too long or contains
a NUL byte. ``ERR HELD`` means a tap was rejected because that key was already
held. ``ERR ROLLOVER`` means all six regular key slots were occupied. The
supported usage ranges are ``04`` through ``73`` for regular keys and ``E0``
through ``E7`` for modifiers. The sketch also releases held keys when it sees
DTR go low and discards any unfinished command. Send ``RELEASE_ALL`` before
closing the port to explicitly clear held keys.

If keys remain held for 1,000 ms without a valid key command or ``PING``, the
sketch releases them and discards the partial command. This uses unsigned
``millis()`` subtraction so clock wrap does not extend the timeout. ``HELLO``
does not refresh it. The host sends complete short chords and releases them
immediately; it does not depend on holding a key or sending background pings.

For a manual test, open the serial monitor with newline enabled and send
``TAP 04``. The A keystroke goes to the focused application. To send uppercase
A on a US layout, send ``PRESS E1``, ``TAP 04`` and ``RELEASE E1`` in order.

See the `Arduino Keyboard reference
<https://docs.arduino.cc/language-reference/en/functions/usb/Keyboard/>`_ for
the library used by the sketch.

A1 backends
-----------

``rs3_reader.bot.actuator_sendinput`` supplies ``SendInputActuator``,
``NativeSnapshotDevice``, the shared ``InputPolicy`` and calibrated ``Bindings``.
``rs3_reader.bot.actuator_serial`` supplies ``SerialActuator``,
``WindowsSerialPort`` and ``SerialKeyboardDevice``. Both implement the S0a
``send(intent) -> SendResult`` interface. Importing these modules opens no
process or port. No third-party Python packages are required.

Create a native device from an already verified, live ``Reader``. The adapter
owns its own native snapshot and borrows the reader's context. Close the
actuator before closing the reader. It copies the latest snapshot and checks
its process/creation/scene identity before calling the existing guarded DLL
functions. C owns balanced key presses and cleanup after partial insertion;
Python does not send global key-ups that could release a user's physical keys.

Create a serial device from an explicit port; it never scans ports::

    from rs3_reader.bot.actuator_serial import (
        SerialActuator, SerialKeyboardDevice, WindowsSerialPort,
    )

    device = SerialKeyboardDevice(WindowsSerialPort("COM5"))
    with SerialActuator(
        device,
        world_provider=world_store.latest,
        clock=reader.monotonic_ns,
        session=world_store.latest().session,
        bindings=calibrated_bindings,
    ) as actuator:
        pass  # Disabled by default; only D6 dispatches intents during track L.

Here ``world_store`` and ``calibrated_bindings`` are caller-owned dependencies,
not globals supplied by A1. The provider returns an immutable ``WorldState``.
The clock must use the DLL's monotonic domain. ``enabled=True`` is a separate
explicit opt-in; construction does not arm input. The four configured curse
bindings remain Ctrl+F1 Magic, Ctrl+F2 Ranged, Ctrl+F3 Soul Split, Ctrl+F4 Melee.
Bindings must carry validation provenance and match the observed account/bar.
Native modifier bits are Ctrl=1, Shift=2, Alt=4; P5's raw game modifier codes
must be translated before creating a binding.

Common dispatch policy
~~~~~~~~~~~~~~~~~~~~~~

The bounded synchronous FIFO serializes concurrent callers; there is no input
worker. The caller at its head performs I/O. Every queued intent is checked
again after waiting. A session change or device fault stops the instance;
reconnection requires a new instance. ``close()`` wakes waiting callers,
releases device-owned keys and closes the transport. It is idempotent.

Input requires a current foreground game process with matching creation time,
a non-minimized enabled window, fresh snapshot/evidence, an unexpired deadline,
an observed game tick and an explicitly validated key binding. Default maximum
age is 150 ms, queue capacity 64, and live intent history capacity 4,096. Unknown
or invalid fields reject the action. Prayer input also requires fresh prayer
points/current overhead and rejects an already-active overhead. Item input
rechecks the exact zero-based slot, item ID and optional tracking token before
each serial command. A moved item or changed dose rejects the old intent.

The default per-tick limits are GCD 1, off-GCD 2, food 1, potion 1, prayer 4 and
pointer 3, with a total of 12. These are configurable dispatch budgets, not a
claim about game cooldown groups. Food, potion, ability and prayer may all be
sent in one tick. D6 owns arbitration and D3/D4 own timer/castability decisions.
Intent IDs remain deduplicated until their deadlines expire. An attempted
dispatch consumes budget even if the device later rejects or fails; no retry
is automatic.

The original-profile C collector now provides a guarded received-server-tick
ordinal after observing a transition. An unknown/stale tick still rejects
``server_tick_unknown``; the client logic clock and wall-clock divisions are
never substituted. Server phase and input-arrival uncertainty remain unknown.
A1 does not establish protection deadlines; track L controls live enablement.

Transport guarantees and limits
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Only one serial command may be outstanding. Replies are bounded and checked
exactly; the driver accepts fragmented reads/writes. Timeout, malformed reply
or transport failure closes the port and drops DTR. It never consumes a late
reply as the acknowledgement of a later action. The default command deadline
is 150 ms, with Windows reads/writes bounded at 20 ms. Focus, evidence, tick and
deadline are checked before each key command. USB and OS scheduling still add
latency; a serial ``OK`` acknowledges firmware processing, not game receipt or
successful prayer/ability activation. Later native observations provide game
acknowledgement.

Function-key HID positions map directly. Letter/digit bindings require an
explicit ``hid_usage`` from keyboard-layout calibration; the driver does not
assume a US layout. Fault cleanup releases board-owned keys. If the serial
link cannot deliver ``RELEASE_ALL``, dropping DTR and the firmware watchdog are
fallbacks, not proof of immediate release at the OS. No background heartbeat
is needed for the short chords used by this backend.

The native DLL also exports ``rs3_activate_ability_chord`` and
``rs3_activate_item_chord``. New chords require an active game scene, a snapshot
and advancing client clock within 150 ms, plus sampled ability identity or an
exact unambiguous item slot/ID/nonzero token/positive quantity. The adapter uses
64-bit item tokens. Older DLLs retain unmodified abilities and explicitly
reject unsupported modified/item input. Full castability remains a caller
policy; neither native acceptance nor a serial reply proves an ability fired
or an item was consumed. Neither backend implements pointer movement here.

``binding_provider(action, world)`` optionally supplies a fresh ``KeyBinding``
to either serialized backend. It is checked again before dispatch and before
each serial key command. If it changes, input is rejected and owned modifier
keys are released. ``observed_binding`` resolves one current P5 row, requires
an explicit native ``now_ns``, and accounts for time spent waiting. It decodes
only P5's validated plain/Ctrl function-key labels. It rejects missing,
duplicate, stale or undecoded rows without falling back to a static mapping.
Letter/digit mappings and potion-family aliases require separate calibration.

The native adapter uses ``rs3_snapshot_copy`` when available to copy the exact
caller-owned ``Reader.snapshot`` into its private storage. The caller must
serialize consumer refresh and dispatch, as required by Reader's reusable
storage contract. The independent native worker may keep sampling; it never
mutates that consumer snapshot. Older DLLs fall back to copying worker latest.
Both paths require the exact same sample sequence, process/scene and executable
as the WorldState whose policy and binding were checked. A newer sample rejects with
``native_snapshot_advanced`` so the arbiter can recompute from new evidence.
It never applies an old item/binding decision to a newer unexamined frame.

Verification
------------

``tests/python/test_bot_actuator.py`` runs the same policy tests over a fake
DLL and a byte-level serial fake. It covers stale/deadline/session checks,
same-tick lanes, queue overflow/expiry/shutdown, duplicates, faults, cleanup,
changed item IDs and serial framing/timeouts. No game, port or input DLL is
opened by these tests.

The suite can also compile the actual sketch against host stubs in
``arduino/serial_keyboard/tests``. It checks the parser, HID failure, DTR,
watchdog, heartbeat and unsigned clock wrap. Set ``RS3_FIRMWARE_COMPILER`` to a
JSON compiler-command array if ``clang++`` or ``g++`` is not on PATH. Host tests
do not replace compiling for the Leonardo or checking actual USB key delivery.
Neither the AVR build nor the physical-board text-editor check has been run
for this change.

Protocol implementation references: `Windows DCB
<https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-dcb>`_,
`Windows COMMTIMEOUTS
<https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-commtimeouts>`_
and `Arduino Keyboard raw-usage implementation
<https://github.com/arduino-libraries/Keyboard/blob/master/src/Keyboard.cpp>`_.

The 2026-11-14 A1 follow-up passes 31 fake-device/ABI/firmware tests. New cases
cover typed native chord calls and 64-bit item arguments, old-DLL capability
rejection, same-tick lanes, binding moves (including after modifier press),
P5 unknown/duplicate/stale rows, and copied-frame/build changes. No physical
USB/game delivery or live controller enablement was performed.

The 2026-11-14 owned-copy follow-up passes 33 A1 tests. It verifies that a
newer independent worker cannot replace the consumer frame, and that a changed
or missing consumer snapshot still rejects. The native copy preserves the
original timestamps and never makes stale evidence fresh.

Both serialized backends expose ``fault`` as None or a latched transport/cleanup
reason. ``SendResult.accepted=False`` alone does not distinguish an ordinary
precondition rejection from uncertain delivery. D6 checks the fault channel
before dispatch and after each result, stops the remaining batch and closes
the backend on uncertainty. The property remains latched through shutdown.

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

For a manual test, open the serial monitor with newline enabled and send
``TAP 04``. The A keystroke goes to the focused application. To send uppercase
A on a US layout, send ``PRESS E1``, ``TAP 04`` and ``RELEASE E1`` in order.

See the `Arduino Keyboard reference
<https://docs.arduino.cc/language-reference/en/functions/usb/Keyboard/>`_ for
the library used by the sketch.

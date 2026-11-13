Arduino Leonardo serial keyboard and mouse
==========================================

The ``arduino/serial_keyboard/serial_keyboard.ino`` sketch listens on the
Leonardo's USB serial port and sends keyboard and mouse input to the same
computer over USB HID.

Build and upload
----------------

In Arduino IDE, install **Arduino AVR Boards** and Arduino's **Keyboard** and
**Mouse** libraries. Open the sketch, select **Arduino Leonardo** and its serial
port, then upload. The equivalent Arduino CLI commands are shown below. Run them
from the repository root and replace ``COM5`` with the port listed for your board. The monitor
must be closed during upload. Run ``arduino-cli board list`` again afterward
because Windows may assign a new COM port when the keyboard interface appears.

.. code-block:: powershell

    arduino-cli core update-index
    arduino-cli core install arduino:avr
    arduino-cli lib install Keyboard
    arduino-cli lib install Mouse
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

=================== =================================================================
Command             Effect
=================== =================================================================
``PRESS <key>``     Hold a key. A repeated press of a held key has no effect.
``RELEASE <key>``   Release a key. A release of an idle key has no effect.
``TAP <key>``       Press and immediately release a key.
``RELEASE_ALL``     Release all keys, modifiers and mouse buttons.
``MOVE <dx> <dy>``  Send a relative mouse report. Positive is right or down.
``CLICK <button>``  Click the left (``L``), right (``R``) or middle (``M``) button.
``DOWN <button>``   Hold a mouse button.
``UP <button>``     Release a mouse button.
``WHEEL <steps>``   Scroll up for positive values and down for negative values.
=================== =================================================================

Mouse movement and wheel arguments are signed decimal values from -127 through
127. Windows applies its pointer speed and acceleration to relative mouse reports.

Read one reply before sending the next command. ``OK`` means the sketch
processed the command. ``ERR COMMAND`` signals an unknown or missing command,
``ERR KEY`` an invalid key, and ``ERR FRAME`` a line that is too long or contains
a NUL byte. ``ERR HELD`` means a tap was rejected because that key was already
held. ``ERR ROLLOVER`` means all six regular key slots were occupied.
``ERR MOUSE`` signals an invalid mouse button or delta. The
supported usage ranges are ``04`` through ``73`` for regular keys and ``E0``
through ``E7`` for modifiers. The sketch releases held input when it sees
DTR go low and discards any unfinished command. Send ``RELEASE_ALL`` before
closing the port to explicitly clear held keys.

For a manual test, open the serial monitor with newline enabled and send
``TAP 04``. The A keystroke goes to the focused application. To send uppercase
A on a US layout, send ``PRESS E1``, ``TAP 04`` and ``RELEASE E1`` in order.

See the `Arduino Keyboard reference
<https://docs.arduino.cc/language-reference/en/functions/usb/Keyboard/>`_ for
the keyboard library used by the sketch.

Capture and control the client
------------------------------

Use ``work/game_console.py`` on Windows with the game in the foreground and
the serial monitor closed. MSS captures the visible client region. Pillow
scales and saves the PNG. The script checks foreground focus and the screenshot's
window geometry before routing mouse actions through Leonardo. It uses the
observed Windows cursor position to correct relative HID movement.

.. code-block:: powershell

    python -m pip install 'mss>=10.2' Pillow
    $env:PYTHONPATH = 'python'
    $frame = Join-Path $env:TEMP 'rs3-live.png'
    python work/game_console.py shot $frame
    python work/game_console.py --port COM8 move $frame 250 300
    python work/game_console.py --port COM8 click $frame 250 300 --button R
    python work/game_console.py --port COM8 wheel $frame 250 300 -2
    python work/game_console.py --port COM8 drag $frame 250 300 350 450
    python work/game_console.py --port COM8 key Escape

Use the port assigned after flashing. Coordinates are pixels in the saved PNG.
Capture a fresh frame after any action that changes the game or moves the window.
The keyboard command also accepts chords such as ``Ctrl+F3``. From a visible
PowerShell window, prefix a command with ``Start-Sleep -Seconds 3`` on a separate
line and switch to RuneScape before the delay expires.

On the validation machine, Windows Graphics Capture timed out twice on the
Compatibility Mode client. Over 12 frames per method, capturing the visible
client rectangle with MSS and converting it to RGB took a median of 34 ms.
Pillow's desktop capture and crop took 136 ms. These figures exclude scaling
and saving the PNG and use a capture session kept open between MSS frames.

In a live run on World 10, Leonardo clicks opened the lodestone network. The
Leonardo keyboard sent ``V`` to teleport to Varrock, and minimap clicks guided
the character to the exchange counter. The world map confirmed the final location
inside the Varrock Grand Exchange.

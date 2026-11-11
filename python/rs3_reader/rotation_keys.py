"""Keyboard transports for the supervised PVME runner (standard library only).

Both transports use physical key positions. Serial speaks the repository's
Leonardo protocol; an OK reply acknowledges HID input, never an in-game cast.
Importing this module opens no device and sends no input.
"""

from __future__ import annotations

import ctypes
import os
import re
import time
from dataclasses import dataclass

POLL_SECONDS = 0.01
SERIAL_TIMEOUT_SECONDS = 1.0
SERIAL_REPLY_LIMIT = 64
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ_WRITE = 0xC0000000
OPEN_EXISTING = 3
CLR_DTR = 6
SERIAL_BAUD = 115200
SERIAL_BINARY_DTR_FLAGS = 0x11
DWORD_MAX = 0xFFFFFFFF
USER_MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)


@dataclass(frozen=True)
class Key:
    """One physical key in Win32 virtual/scancode and USB HID coordinates."""

    name: str
    virtual_key: int
    scan_code: int
    usage: int
    extended: bool = False


KEYS = {}
for _name, _scan in zip(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    (
        0x1E,
        0x30,
        0x2E,
        0x20,
        0x12,
        0x21,
        0x22,
        0x23,
        0x17,
        0x24,
        0x25,
        0x26,
        0x32,
        0x31,
        0x18,
        0x19,
        0x10,
        0x13,
        0x1F,
        0x14,
        0x16,
        0x2F,
        0x11,
        0x2D,
        0x15,
        0x2C,
    ),
):
    KEYS[_name] = Key(_name, ord(_name), _scan, 0x04 + ord(_name) - ord("A"))
for _index, _name in enumerate("1234567890"):
    KEYS[_name] = Key(_name, ord(_name), 0x02 + _index, 0x1E + _index)
for _index in range(1, 13):
    _scan = 0x3A + _index if _index <= 10 else 0x57 + _index - 11
    _name = f"F{_index}"
    KEYS[_name] = Key(_name, 0x6F + _index, _scan, 0x39 + _index)
for _name, _virtual, _scan, _usage, _extended in (
    ("CTRL", 0x11, 0x1D, 0xE0, False),
    ("SHIFT", 0x10, 0x2A, 0xE1, False),
    ("ALT", 0x12, 0x38, 0xE2, False),
    ("SPACE", 0x20, 0x39, 0x2C, False),
    ("TAB", 0x09, 0x0F, 0x2B, False),
    ("ENTER", 0x0D, 0x1C, 0x28, False),
    ("ESC", 0x1B, 0x01, 0x29, False),
    ("BACKSPACE", 0x08, 0x0E, 0x2A, False),
    ("MINUS", 0xBD, 0x0C, 0x2D, False),
    ("EQUALS", 0xBB, 0x0D, 0x2E, False),
    ("INSERT", 0x2D, 0x52, 0x49, True),
    ("DELETE", 0x2E, 0x53, 0x4C, True),
    ("HOME", 0x24, 0x47, 0x4A, True),
    ("END", 0x23, 0x4F, 0x4D, True),
    ("PAGEUP", 0x21, 0x49, 0x4B, True),
    ("PAGEDOWN", 0x22, 0x51, 0x4E, True),
    ("LEFT", 0x25, 0x4B, 0x50, True),
    ("RIGHT", 0x27, 0x4D, 0x4F, True),
    ("UP", 0x26, 0x48, 0x52, True),
    ("DOWN", 0x28, 0x50, 0x51, True),
):
    KEYS[_name] = Key(_name, _virtual, _scan, _usage, _extended)

ALIASES = {
    "CONTROL": "CTRL",
    "DEL": "DELETE",
    "INS": "INSERT",
    "HM": "HOME",
    "PGUP": "PAGEUP",
    "PGDN": "PAGEDOWN",
    "ESCAPE": "ESC",
}
MODIFIER_NAMES = ("CTRL", "SHIFT", "ALT")


def parse_chord(value):
    """Parse explicit chords such as Ctrl+F5 or Alt+Q; never infer modifiers."""
    if not isinstance(value, str):
        raise ValueError("A key binding must be a string")
    names = [ALIASES.get(part.strip().upper(), part.strip().upper()) for part in value.split("+")]
    if not names or len(set(names)) != len(names) or any(name not in KEYS for name in names):
        raise ValueError(f"Unknown or repeated key in {value!r}")
    if names[-1] in MODIFIER_NAMES or any(name not in MODIFIER_NAMES for name in names[:-1]):
        raise ValueError(f"Expected modifiers followed by one key: {value!r}")
    modifiers = tuple(KEYS[name] for name in MODIFIER_NAMES if name in names[:-1])
    return modifiers + (KEYS[names[-1]],)


def windows_library(name):
    """Load a Win32 DLL only when a live Windows backend is requested."""
    if os.name != "nt":
        raise OSError("Live keyboard backends require Windows")
    return ctypes.WinDLL(name, use_last_error=True)


def bind(library, name, result, arguments):
    """Give a Win32 function an explicit, pointer-width-safe ctypes signature."""
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("virtual_key", ctypes.c_uint16),
        ("scan_code", ctypes.c_uint16),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("extra_info", ctypes.c_size_t),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("data", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("extra_info", ctypes.c_size_t),
    ]


class InputData(ctypes.Union):
    _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("data", InputData)]


class KeyboardBackend:
    """Serialized runner transport; retain possibly-held keys until released."""

    def __init__(self):
        self.held = []
        self.closed = False

    def event(self, key, down, check):
        """Implement one key transition, raising on any uncertain delivery."""
        raise NotImplementedError

    def tap(self, chord, hold_seconds, check, wait):
        """Press a chord, cooperatively wait, and release even after interruption."""
        if self.closed or self.held:
            raise RuntimeError("Keyboard backend is closed or has unreleased keys")
        try:
            for key in chord:
                check()
                # A failed device acknowledgement can still follow an actual press.
                self.held.append(key)
                self.event(key, True, check)
            wait(hold_seconds)
        finally:
            self.release_all()

    def release_all(self):
        """Attempt every release, including after a failed earlier release."""
        errors = []
        for key in tuple(reversed(self.held)):
            try:
                self.event(key, False, lambda: None)
                self.held.remove(key)
            except (OSError, RuntimeError) as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("Key release failed: " + "; ".join(errors))

    def close(self):
        """Idempotently release keys; a failed release can be retried by close."""
        if not self.closed:
            self.release_all()
            self.closed = True


class SendInputBackend(KeyboardBackend):
    """Physical scancode SendInput backend, with checked delivery counts."""

    def __init__(self, sender=None):
        super().__init__()
        self.sender = sender
        if sender is None:
            self.sender = bind(
                windows_library("user32"),
                "SendInput",
                ctypes.c_uint32,
                [ctypes.c_uint32, ctypes.POINTER(Input), ctypes.c_int],
            )

    def event(self, key, down, check):
        """Insert one event; zero accepted events is a hard failure."""
        check()
        flags = KEYEVENTF_SCANCODE
        if key.extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= KEYEVENTF_KEYUP
        packet = Input(INPUT_KEYBOARD, InputData(keyboard=KeyboardInput(0, key.scan_code, flags)))
        if self.sender(1, ctypes.byref(packet), ctypes.sizeof(Input)) != 1:
            raise OSError("SendInput rejected an event (check focus and process integrity levels)")


class DeviceControlBlock(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("baud", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint16),
        ("xon_limit", ctypes.c_uint16),
        ("xoff_limit", ctypes.c_uint16),
        ("byte_size", ctypes.c_uint8),
        ("parity", ctypes.c_uint8),
        ("stop_bits", ctypes.c_uint8),
        ("xon", ctypes.c_char),
        ("xoff", ctypes.c_char),
        ("error", ctypes.c_char),
        ("eof", ctypes.c_char),
        ("event", ctypes.c_char),
        ("reserved1", ctypes.c_uint16),
    ]


class CommTimeouts(ctypes.Structure):
    _fields_ = [
        ("read_interval", ctypes.c_uint32),
        ("read_multiplier", ctypes.c_uint32),
        ("read_constant", ctypes.c_uint32),
        ("write_multiplier", ctypes.c_uint32),
        ("write_constant", ctypes.c_uint32),
    ]


class WindowsSerial:
    """Bounded synchronous Win32 serial I/O, 115200/8N1 with DTR asserted."""

    def __init__(self, port):
        if not isinstance(port, str) or not re.fullmatch(r"COM[1-9][0-9]*", port.upper()):
            raise ValueError("Arduino port must look like COM5")
        library = windows_library("kernel32")
        pointer = ctypes.c_void_p
        boolean = ctypes.c_int
        integer = ctypes.c_uint32
        self.close_handle = bind(library, "CloseHandle", boolean, [pointer])
        self.escape = bind(library, "EscapeCommFunction", boolean, [pointer, integer])
        self.read_file = bind(
            library,
            "ReadFile",
            boolean,
            [pointer, pointer, integer, ctypes.POINTER(integer), pointer],
        )
        self.write_file = bind(
            library,
            "WriteFile",
            boolean,
            [pointer, pointer, integer, ctypes.POINTER(integer), pointer],
        )
        create = bind(
            library,
            "CreateFileW",
            pointer,
            [ctypes.c_wchar_p, integer, integer, pointer, integer, integer, pointer],
        )
        self.handle = create(
            "\\\\.\\" + port.upper(), GENERIC_READ_WRITE, 0, None, OPEN_EXISTING, 0, None
        )
        if self.handle == INVALID_HANDLE_VALUE:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            state = DeviceControlBlock()
            state.length = ctypes.sizeof(state)
            get_state = bind(
                library, "GetCommState", boolean, [pointer, ctypes.POINTER(DeviceControlBlock)]
            )
            set_state = bind(
                library, "SetCommState", boolean, [pointer, ctypes.POINTER(DeviceControlBlock)]
            )
            set_timeouts = bind(
                library, "SetCommTimeouts", boolean, [pointer, ctypes.POINTER(CommTimeouts)]
            )
            if not get_state(self.handle, ctypes.byref(state)):
                raise ctypes.WinError(ctypes.get_last_error())
            state.baud = SERIAL_BAUD
            state.flags = SERIAL_BINARY_DTR_FLAGS
            state.byte_size, state.parity, state.stop_bits = 8, 0, 0
            timeouts = CommTimeouts(DWORD_MAX, 0, 0, 0, 100)
            if not set_state(self.handle, ctypes.byref(state)) or not set_timeouts(
                self.handle, ctypes.byref(timeouts)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            purge = bind(library, "PurgeComm", boolean, [pointer, integer])
            if not purge(self.handle, 0x0F):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def write(self, payload):
        """Write a complete frame or fail; never retry a possibly executed command."""
        written = ctypes.c_uint32()
        buffer = ctypes.create_string_buffer(payload)
        if not self.write_file(self.handle, buffer, len(payload), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(payload):
            raise OSError("Partial Arduino serial write")

    def read(self):
        """Return at most one immediately available reply byte."""
        buffer = ctypes.create_string_buffer(1)
        received = ctypes.c_uint32()
        if not self.read_file(self.handle, buffer, 1, ctypes.byref(received), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.raw[: received.value]

    def close(self):
        """Drop DTR so the sketch releases keys, then release the COM handle."""
        if self.handle is not None:
            try:
                self.escape(self.handle, CLR_DTR)
            finally:
                self.close_handle(self.handle)
                self.handle = None


class ArduinoBackend(KeyboardBackend):
    """One command/one reply serial transport. Protocol faults poison the session."""

    def __init__(self, port=None, *, transport=None, clock=time.monotonic, sleep=time.sleep):
        super().__init__()
        self.transport = transport if transport is not None else WindowsSerial(port)
        self.clock, self.sleep = clock, sleep
        self.failed = False
        try:
            self.command("RELEASE_ALL", lambda: None)
        except BaseException:
            self.transport.close()
            raise

    def command(self, command, check):
        """Require exactly OK within the timeout; never retry ambiguous input."""
        if self.failed or self.closed:
            raise RuntimeError("Arduino connection is closed or faulted")
        try:
            check()
            self.transport.write((command + "\n").encode("ascii"))
            deadline = self.clock() + SERIAL_TIMEOUT_SECONDS
            response = bytearray()
            while self.clock() < deadline:
                check()
                received = self.transport.read()
                if received == b"\n":
                    if bytes(response).rstrip(b"\r") != b"OK":
                        raise OSError(f"Arduino rejected {command}: {bytes(response)!r}")
                    return
                response.extend(received)
                if len(response) > SERIAL_REPLY_LIMIT:
                    raise OSError("Overlong Arduino reply")
                if not received:
                    self.sleep(POLL_SECONDS)
            raise OSError(f"Arduino acknowledgement timeout: {command}")
        except BaseException:
            self.failed = True
            # An unacknowledged PRESS may have reached the board. DTR is the
            # out-of-band release path; no further commands enter this stream.
            self.transport.close()
            raise

    def event(self, key, down, check):
        """Send an HID usage to the existing Leonardo sketch."""
        self.command(f"{'PRESS' if down else 'RELEASE'} {key.usage:02X}", check)

    def release_all(self):
        """Clear device-held keys, or use DTR after an uncertain serial reply."""
        if self.failed:
            self.transport.close()
            self.held.clear()
        elif self.held:
            self.command("RELEASE_ALL", lambda: None)
            self.held.clear()

    def close(self):
        """Release all keys and drop DTR even if the board stops replying."""
        if not self.closed:
            try:
                if not self.failed:
                    self.command("RELEASE_ALL", lambda: None)
            finally:
                self.transport.close()
                self.held.clear()
                self.closed = True

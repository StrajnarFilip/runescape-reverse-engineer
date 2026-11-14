"""Standard-library Leonardo transport and keyboard-only Actuator backend.

    port = WindowsSerialPort("COM5")  # explicit device, never probe arbitrary ports
    device = SerialKeyboardDevice(port)

Construction performs HELLO and RELEASE_ALL only. Use SerialActuator with the
same policy/world/clock/session arguments as SendInputActuator. Pointer actions
remain unsupported. Serial OK is a device acknowledgement, not a game effect.
"""

from __future__ import annotations

import ctypes
import re
import time
from threading import RLock

from .actuator_sendinput import ALT, CTRL, SHIFT, DeviceFault, InputRejected, SerializedActuator

PROTOCOL_GREETING = b"RS3_KEYBOARD 1"
MAX_REPLY_BYTES = 64
SERIAL_BAUD = 115200
SERIAL_IO_TIMEOUT_MS = 20


class WindowsSerialPort:
    """Exclusive Windows COM handle, 115200/8N1, DTR asserted, bounded reads/writes.

    ctypes mirrors DCB/COMMTIMEOUTS without pyserial. Closing lowers DTR even
    after a protocol timeout; firmware also has an independent held-key timeout.
    """

    def __init__(self, port):
        if not isinstance(port, str) or not re.fullmatch(r"COM[1-9][0-9]*", port.upper()):
            raise ValueError("Supply an explicit COM port, for example COM5")
        from ctypes import wintypes

        class DCB(ctypes.Structure):
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

        class Timeouts(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint32)
                for name in (
                    "read_interval",
                    "read_multiplier",
                    "read_constant",
                    "write_multiplier",
                    "write_constant",
                )
            ]

        assert ctypes.sizeof(DCB) == 28 and ctypes.sizeof(Timeouts) == 20
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        prototypes = {
            "CreateFileW": (
                wintypes.HANDLE,
                [
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.HANDLE,
                ],
            ),
            "GetCommState": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(DCB)]),
            "SetCommState": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(DCB)]),
            "SetCommTimeouts": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(Timeouts)]),
            "EscapeCommFunction": (wintypes.BOOL, [wintypes.HANDLE, wintypes.DWORD]),
            "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        }
        for name in ("ReadFile", "WriteFile"):
            prototypes[name] = (
                wintypes.BOOL,
                [
                    wintypes.HANDLE,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                    ctypes.c_void_p,
                ],
            )
        for name, (result, args) in prototypes.items():
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = result, args
        self.handle = self.kernel.CreateFileW(
            "\\\\.\\" + port.upper(), 0xC0000000, 0, None, 3, 0, None
        )
        if self.handle in (None, ctypes.c_void_p(-1).value):
            self.handle = None
            raise OSError(ctypes.get_last_error(), "Cannot open explicit serial port")
        try:
            configuration = DCB()
            configuration.length = ctypes.sizeof(DCB)
            if not self.kernel.GetCommState(self.handle, ctypes.byref(configuration)):
                raise OSError("GetCommState failed")
            configuration.baud = SERIAL_BAUD
            configuration.flags = 1 | (1 << 4) | (1 << 12)  # binary, DTR on, RTS on
            configuration.byte_size, configuration.parity, configuration.stop_bits = 8, 0, 0
            timeouts = Timeouts(
                0xFFFFFFFF, 0xFFFFFFFF, SERIAL_IO_TIMEOUT_MS, 0, SERIAL_IO_TIMEOUT_MS
            )
            if not self.kernel.SetCommState(self.handle, ctypes.byref(configuration)) or not (
                self.kernel.SetCommTimeouts(self.handle, ctypes.byref(timeouts))
            ):
                raise OSError("Serial configuration failed")
        except BaseException:
            self.close()
            raise

    def write(self, data):
        if not self.handle:
            raise OSError("Serial port closed")
        count = ctypes.c_uint32()
        buffer = ctypes.create_string_buffer(data)
        if not self.kernel.WriteFile(self.handle, buffer, len(data), ctypes.byref(count), None):
            raise OSError("Serial write failed")
        return count.value

    def read(self, size):
        if not self.handle:
            raise OSError("Serial port closed")
        if type(size) is not int or not 1 <= size <= MAX_REPLY_BYTES:
            raise ValueError("Invalid serial read size")
        count = ctypes.c_uint32()
        buffer = ctypes.create_string_buffer(size)
        if not self.kernel.ReadFile(self.handle, buffer, size, ctypes.byref(count), None):
            raise OSError("Serial read failed")
        return buffer.raw[: count.value]

    def close(self):
        if self.handle:
            self.kernel.EscapeCommFunction(self.handle, 6)  # CLRDTR, even when RELEASE_ALL failed
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class SerialKeyboardDevice:
    """One outstanding command, bounded frames, exact replies and no retry on timeout.

    The injected byte channel supplies write/read/close. I/O uses time.monotonic
    seconds; game deadlines use the separately injected native clock in A1.
    A late reply is never reused as an acknowledgement of a new command.
    """

    def __init__(self, channel, *, timeout_seconds=0.15, io_clock=time.monotonic):
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not (0 < timeout_seconds <= 1)
        ):
            raise ValueError("Serial timeout must be in (0, 1] seconds")
        self.channel, self.timeout, self.io_clock = channel, timeout_seconds, io_clock
        self._lock, self._closed, self._faulted = RLock(), False, False
        try:
            self._exchange(b"HELLO", PROTOCOL_GREETING)
            self.release_all()
        except BaseException:
            self.close()
            raise

    def _exchange(self, command, expected=b"OK", *, cleanup=False):
        with self._lock:
            if self._closed or self._faulted and not cleanup:
                raise DeviceFault("serial_closed_or_faulted")
            if (
                not isinstance(command, bytes)
                or not command
                or len(command) > 12
                or any(value in command for value in (b"\r", b"\n", b"\0"))
            ):
                raise ValueError("Invalid protocol command frame")
            deadline = self.io_clock() + self.timeout
            frame = command + b"\n"
            sent, reply = 0, bytearray()
            try:
                while sent < len(frame):
                    if self.io_clock() >= deadline:
                        raise DeviceFault("serial_write_timeout")
                    written = self.channel.write(frame[sent:])
                    if type(written) is not int or not 0 < written <= len(frame) - sent:
                        raise DeviceFault("serial_short_write")
                    sent += written
                while self.io_clock() < deadline:
                    chunk = self.channel.read(1)
                    if not isinstance(chunk, bytes) or len(chunk) > 1:
                        raise DeviceFault("serial_invalid_read")
                    if not chunk:
                        continue
                    if chunk == b"\n":
                        if bytes(reply).removesuffix(b"\r") != expected:
                            raise DeviceFault("serial_unexpected_reply")
                        return
                    reply.extend(chunk)
                    if len(reply) > MAX_REPLY_BYTES or chunk == b"\0":
                        raise DeviceFault("serial_invalid_reply_frame")
                raise DeviceFault("serial_reply_timeout")
            except BaseException:
                self._faulted = True
                # Drop DTR immediately. Sending RELEASE_ALL here could consume a
                # late OK belonging to the failed command, so do not pipeline it.
                self._closed = True
                try:
                    self.channel.close()
                except Exception as close_error:
                    raise DeviceFault("serial_close_failed_input_state_unknown") from close_error
                raise

    @staticmethod
    def usage(binding):
        if binding.hid_usage is not None:
            return binding.hid_usage
        if 0x70 <= binding.virtual_key <= 0x7B:
            return 0x3A + binding.virtual_key - 0x70
        raise InputRejected("hid_key_position_unmapped")

    def tap(self, binding, check):
        usage = self.usage(binding)
        modifiers = [
            value
            for mask, value in ((CTRL, 0xE0), (SHIFT, 0xE1), (ALT, 0xE2))
            if binding.modifiers & mask
        ]
        with self._lock:
            try:
                for modifier in modifiers:
                    check()
                    self._exchange(f"PRESS {modifier:02X}".encode("ascii"))
                check()
                self._exchange(f"TAP {usage:02X}".encode("ascii"))
            finally:
                # Cleanup bypasses the foreground/deadline check. It releases
                # this board's keys, never presses an action in another window.
                if not self._closed:
                    self.release_all()

    def release_all(self):
        if not self._closed:
            self._exchange(b"RELEASE_ALL", cleanup=True)

    def close(self):
        with self._lock:
            if self._closed:
                return
            try:
                self.release_all()
            finally:
                self._closed = True
                self.channel.close()


class SerialActuator(SerializedActuator):
    """Keyboard backend with the same FIFO, identity, age and budget policy."""

    def _supported(self, action, binding):
        self.device.usage(binding)

    def _dispatch(self, intent, binding, world):
        expected_tick = self._tick

        def check():
            current = self._validate(intent)
            if self._resolve_binding(intent.action, current) != binding:
                raise InputRejected("binding_changed_during_dispatch")
            self._preconditions(intent.action, current)
            if not self._fresh(current.game_tick, current, self._now()) or (
                current.game_tick.value.get("index") != expected_tick
            ):
                raise InputRejected("tick_changed_during_dispatch")

        self.device.tap(binding, check)

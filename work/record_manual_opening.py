"""Record one manual prebuild/encounter with native frames and foreground-only input hooks.

Sends no input, calls no game function, and opens game memory only through the
C DLL. Raw compressed captures stay local. Physical mouse points and observed
tile arrivals are distinct from an unmapped intended ground destination.
"""

import argparse
import ctypes
from ctypes import wintypes
from collections import deque
import gzip
import json
from pathlib import Path
import sys
import threading
import time

from workspace import add_import_paths

add_import_paths(package=True)

from rs3_reader.telemetry import NativeTelemetry  # noqa: E402
from rs3_reader.native import decode_text  # noqa: E402

QUEUE_CAPACITY = 8192
KEYBOARD_HOOK = 13
MOUSE_HOOK = 14
MOUSE_MESSAGES = {
    0x200: "move",
    0x201: "left_down",
    0x202: "left_up",
    0x204: "right_down",
    0x205: "right_up",
    0x207: "middle_down",
    0x208: "middle_up",
    0x20A: "wheel",
    0x20B: "extra_down",
    0x20C: "extra_up",
    0x20E: "horizontal_wheel",
}


class KeyboardEvent(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in ("key", "scan", "flags", "time")]
    _fields_ += [("extra", ctypes.c_size_t)]


class MouseEvent(ctypes.Structure):
    _fields_ = [("point", wintypes.POINT)]
    _fields_ += [(name, wintypes.DWORD) for name in ("data", "flags", "time")]
    _fields_ += [("extra", ctypes.c_size_t)]


class ManualInputs:
    """Bounded event queue with explicit overflow/errors and no actuation APIs.

    Low-level Windows hooks are callbacks, not asynchronous-key polling. Their
    delivery can still fail outside the process; complete capture is not claimed.
    Only the selected foreground PID is retained, while the C sampler confirms
    an active game scene. No text conversion or character strings are recorded.
    """

    def __init__(self, pid, clock):
        self.pid, self.clock = pid, clock
        self.allowed = False
        self.queue, self.lock = deque(), threading.Lock()
        self.sequence = self.lost = self.errors = 0
        self.ready, self.stop_event = threading.Event(), threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="manual-input-recorder", daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.error:
            self.close()
            raise RuntimeError("Input hooks could not start: " + str(self.error))

    def _append(self, event):
        with self.lock:
            self.sequence += 1
            event["sequence"] = self.sequence
            if len(self.queue) >= QUEUE_CAPACITY:
                self.lost += 1
            else:
                self.queue.append(event)

    def drain(self):
        """Return owned events and cumulative loss/error counters."""
        with self.lock:
            events = list(self.queue)
            self.queue.clear()
            return events, self.lost, self.errors

    def _run(self):
        hooks = []
        try:
            user = ctypes.WinDLL("user32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
            )
            user.SetWindowsHookExW.argtypes = [
                ctypes.c_int,
                callback_type,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            ]
            user.SetWindowsHookExW.restype = wintypes.HHOOK
            user.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
            user.CallNextHookEx.argtypes = [
                wintypes.HHOOK,
                ctypes.c_int,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            ]
            user.CallNextHookEx.restype = ctypes.c_ssize_t
            user.GetForegroundWindow.restype = wintypes.HWND
            user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
            user.GetDpiForWindow.argtypes = [wintypes.HWND]
            user.GetDpiForWindow.restype = wintypes.UINT
            user.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user.GetAsyncKeyState.restype = ctypes.c_short
            user.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel.GetModuleHandleW.restype = wintypes.HMODULE

            def receive(kind, code, message, pointer):
                try:
                    if code >= 0 and self.allowed:
                        window = user.GetForegroundWindow()
                        owner = wintypes.DWORD()
                        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
                        if owner.value == self.pid:
                            structure = KeyboardEvent if kind == "keyboard" else MouseEvent
                            raw = ctypes.cast(pointer, ctypes.POINTER(structure)).contents
                            event = dict(
                                kind=kind,
                                message=message,
                                received_ns=self.clock(),
                                os_time_ms=raw.time,
                                flags=raw.flags,
                                pid=self.pid,
                            )
                            if kind == "keyboard":
                                event.update(
                                    key=raw.key,
                                    scan=raw.scan,
                                    edge="down" if message in (0x100, 0x104) else "up",
                                    injected=bool(raw.flags & 0x10),
                                    modifiers_sample=sum(
                                        bit
                                        for key, bit in ((0x11, 1), (0x10, 2), (0x12, 4))
                                        if user.GetAsyncKeyState(key) & 0x8000
                                    ),
                                )
                            else:
                                point = wintypes.POINT(raw.point.x, raw.point.y)
                                converted = user.ScreenToClient(window, ctypes.byref(point))
                                event.update(
                                    action=MOUSE_MESSAGES.get(message, "unknown"),
                                    screen_x=raw.point.x,
                                    screen_y=raw.point.y,
                                    client_x=point.x if converted else None,
                                    client_y=point.y if converted else None,
                                    dpi=user.GetDpiForWindow(window),
                                    data=raw.data,
                                    injected=bool(raw.flags & 1),
                                    intended_destination_tile=None,
                                )
                            self._append(event)
                except Exception:
                    self.errors += 1
                return user.CallNextHookEx(None, code, message, pointer)

            # Keep the callback objects alive until both hooks are removed.
            callbacks = [
                callback_type(lambda c, m, p: receive("keyboard", c, m, p)),
                callback_type(lambda c, m, p: receive("mouse", c, m, p)),
            ]
            for hook_id, callback in zip((KEYBOARD_HOOK, MOUSE_HOOK), callbacks):
                handle = user.SetWindowsHookExW(hook_id, callback, kernel.GetModuleHandleW(None), 0)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                hooks.append(handle)
            self.ready.set()
            message = wintypes.MSG()
            while not self.stop_event.is_set():
                while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    pass  # PeekMessage delivers the low-level hook callbacks on this thread.
                time.sleep(0.001)
        except BaseException as error:
            self.error = str(error)
            self.ready.set()
        finally:
            self.allowed = False
            for hook in hooks:
                user.UnhookWindowsHookEx(hook)

    def close(self):
        """Unregister hooks on their owning thread; never release or send a game key."""
        self.allowed = False
        self.stop_event.set()
        self.thread.join(5)
        if self.thread.is_alive():
            raise RuntimeError("Input-hook thread did not stop")


def record(pid, duration, frequency, output, stop_file):
    """Capture full C telemetry plus independent input timestamps into local gzip JSONL."""
    telemetry = hooks = None
    identity = None
    started, frames = time.monotonic(), 0
    last_status, status = 0, None
    try:
        telemetry = NativeTelemetry(pid, 0)
        baseline = telemetry.sample(False)
        identity = (
            baseline.get("pid"),
            baseline.get("process_creation_time"),
            baseline.get("executable_sha256"),
        )
        if (
            identity[0] != pid
            or not identity[1]
            or not identity[2]
            or baseline.get("status") not in ("available", "partial")
        ):
            raise RuntimeError("Selected client has no usable native frame")
        labels = []
        for bar in range(1, 6):
            for slot in range(1, 15):
                try:
                    label = telemetry.reader.read_action_label(bar, slot)
                except RuntimeError:
                    continue
                labels.append(
                    dict(
                        display_bar=bar,
                        preset_bar=label.preset_bar,
                        slot=slot,
                        name=decode_text(label.name),
                        key=decode_text(label.key_label),
                        virtual_key=label.virtual_key,
                        modifiers=label.modifiers,
                        item_id=label.item_id,
                        flags=label.flags,
                        observed_ns=label.observed_ns,
                        sample_sequence=label.sample_sequence,
                    )
                )
        hooks = ManualInputs(pid, telemetry.reader.monotonic_ns)
        with gzip.open(output, "xt", encoding="utf-8", compresslevel=1) as stream:
            print("MANUAL RECORDING ACTIVE; no automatic input", flush=True)
            while time.monotonic() - started < duration and not stop_file.exists():
                frame_started = time.monotonic()
                row = telemetry.sample(False)
                observed = (
                    row.get("pid"),
                    row.get("process_creation_time"),
                    row.get("executable_sha256"),
                )
                if identity is None:
                    identity = observed
                elif identity != observed:
                    raise RuntimeError("Selected game process changed")
                hooks.allowed = (row.get("combat") or {}).get("scene_state") == 30
                events, lost, errors = hooks.drain()
                row["operator_inputs"] = events
                row["manual_actions_lost"] = lost
                row["sample_started_ns"] = telemetry.reader.info.monotonic_ns
                camera = telemetry.reader.spatial.camera
                row["camera"] = dict(
                    observed_ns=camera.observed_ns,
                    pid=camera.pid,
                    process_creation_time=camera.process_creation_time,
                    scene_epoch=camera.scene_epoch,
                    valid_mask=camera.valid_mask,
                    reason=camera.reason,
                    plane=camera.plane,
                    world_to_clip=list(camera.world_to_clip) if camera.valid_mask & 1 else None,
                    viewport=(
                        [
                            camera.viewport_x,
                            camera.viewport_y,
                            camera.viewport_width,
                            camera.viewport_height,
                        ]
                        if camera.valid_mask & 2
                        else None
                    ),
                    client_screen_origin=(
                        [camera.client_screen_x, camera.client_screen_y]
                        if camera.valid_mask & 4
                        else None
                    ),
                )
                if frames == 0:
                    row["initial_native_key_labels"] = labels
                row["manual_capture"] = dict(
                    hook_errors=errors,
                    enabled_in_game=hooks.allowed,
                    input_delivery_complete=None,
                    target_hz=frequency,
                    sends_input=False,
                    ground_destination_decoded=False,
                )
                stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
                frames += 1
                if time.monotonic() - last_status >= 1:
                    status = dict(
                        kind="manual_capture_status",
                        frames=frames,
                        input_events=hooks.sequence,
                        input_events_lost=lost,
                        hook_errors=errors,
                        input_capture_enabled=hooks.allowed,
                        pid=pid,
                        process_creation_time=identity[1],
                        player_tile=(row.get("player") or {}).get("tile", row.get("position")),
                        phase=(row.get("telos") or {}).get("phase"),
                        running=True,
                        sends_input=False,
                    )
                    output.with_suffix(".status.json").write_text(
                        json.dumps(status), encoding="utf-8"
                    )
                    last_status = time.monotonic()
                if errors or lost or hooks.error or not hooks.thread.is_alive():
                    raise RuntimeError("Manual input capture lost events or its hook thread")
                time.sleep(max(0, 1 / frequency - (time.monotonic() - frame_started)))
            hooks.close()
            events, lost, errors = hooks.drain()
            stream.write(
                json.dumps(
                    dict(
                        kind="manual_input_tail",
                        operator_inputs=events,
                        manual_actions_lost=lost,
                        hook_errors=errors,
                    )
                )
                + "\n"
            )
        print(
            json.dumps(
                dict(
                    frames=frames,
                    seconds=time.monotonic() - started,
                    input_events=hooks.sequence,
                    lost=hooks.lost,
                    errors=hooks.errors,
                )
            ),
            flush=True,
        )
    finally:
        if hooks:
            hooks.close()
        if status is not None:
            status.update(running=False, frames=frames, input_events=hooks.sequence)
            output.with_suffix(".status.json").write_text(json.dumps(status), encoding="utf-8")
        if telemetry:
            telemetry.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--hz", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    arguments = parser.parse_args()
    if not 0 < arguments.duration <= 1800 or not 1 <= arguments.hz <= 100:
        parser.error("duration must be 0..1800 seconds and hz 1..100")
    if arguments.output.exists() or arguments.stop_file.exists():
        parser.error("Use new output and stop-file paths")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    record(arguments.pid, arguments.duration, arguments.hz, arguments.output, arguments.stop_file)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
